# addonCore.py

import wx
import time
import re
import ctypes
from enum import Enum
import addonHandler

addonHandler.initTranslation()
_translate = _

import globalPluginHandler
import controlTypes
import browseMode
import cursorManager
import logHandler
import api
import ui
import core
import speech
import textInfos
import keyboardHandler
from comtypes import COMError

from .markerEngine import MarkerEngine
from .gui import MarkerEditDialog, SiteManagerDialog, MarkerManagerDialog, AddSiteDialog
from . import browseModeGestures
from . import customShortcuts
from .scanEngine import ScanEngineMixin
from .contextHelpers import ContextHelpersMixin
from .markerAction import MarkerActionMixin
from .constants import (
	MAX_PRIMED_TREE_INTERCEPTORS,
	MAX_BLANK_LINE_SKIP_STEPS,
	STANDARD_JUMP_KEYS,
	STANDARD_AUTOCLICK_KEYS,
)

# ------------------------- Focus Mode Enum -------------------------
class FocusMode(Enum):
	UNCHANGED = 0
	DONT_ENTER_FORM_MODE = 1
	DISABLE_FOCUS = 2

# ------------------------- Patch for Focus Mode (shouldPassThrough only) -------------------------
_originalShouldPassThrough = None
_activePluginInstance = None

def _patchedShouldPassThrough(self, targetObj, reason=None):
	try:
		focusMode = _getCurrentSiteFocusMode()
		if focusMode == FocusMode.DISABLE_FOCUS:
			return self.passThrough
		if reason == controlTypes.OutputReason.FOCUS and focusMode == FocusMode.DONT_ENTER_FORM_MODE:
			return self.passThrough
	except Exception as e:
		logHandler.log.debug(f"SiteMarker: shouldPassThrough patch error, falling back to core behaviour: {e}")
	return _originalShouldPassThrough(self, targetObj, reason)

def _getCurrentSiteFocusMode():
	gp = _getGlobalPluginInstance()
	if not gp:
		return FocusMode.UNCHANGED
	_, siteConfig = gp.getCurrentSiteConfig()
	if siteConfig and 'focusMode' in siteConfig:
		try:
			return FocusMode(siteConfig['focusMode'])
		except ValueError:
			pass
	return FocusMode.UNCHANGED

def _getGlobalPluginInstance():
	return _activePluginInstance

def applyFocusModePatch():
	global _originalShouldPassThrough
	if _originalShouldPassThrough is not None:
		return
	_originalShouldPassThrough = browseMode.BrowseModeTreeInterceptor.shouldPassThrough
	browseMode.BrowseModeTreeInterceptor.shouldPassThrough = _patchedShouldPassThrough
	logHandler.log.info("SiteMarker: Focus mode patches applied.")

def removeFocusModePatch():
	global _originalShouldPassThrough
	if _originalShouldPassThrough is not None:
		browseMode.BrowseModeTreeInterceptor.shouldPassThrough = _originalShouldPassThrough
	_originalShouldPassThrough = None

# ------------------------- Patch for blank-line skipping (opt-in per site) -------------------------
# Targets cursorManager.CursorManager._caretMovementScriptHelper -- the single
# shared choke point behind script_moveByLine_forward/_back and
# script_moveByParagraph_forward/_back (i.e. Up/Down and Ctrl+Up/Down), rather
# than patching those four script methods individually. This is the same
# NVDA-core integration point third-party browse-mode navigation add-ons use
# for this class of feature; independently confirmed via its real call
# signature `(self, gesture, unit, direction, posConstant, *args, **kwargs)`
# appearing in NVDA's own public issue tracker tracebacks, not copied from
# any third-party add-on's implementation.
_originalCaretMovementScriptHelper = None

def _isBlankLineFilterActive(treeInt):
	try:
		if not isinstance(treeInt, browseMode.BrowseModeDocumentTreeInterceptor):
			return False
		if getattr(treeInt, "passThrough", False):
			return False
		gp = _getGlobalPluginInstance()
		if not gp:
			return False
		_, siteConfig = gp.getCurrentSiteConfig()
		return bool(siteConfig and siteConfig.get("skipBlankLines", False))
	except Exception:
		return False

def _isTextInfoBlank(textInfo):
	text = textInfo.text
	return not text or not text.strip()

def _patchedCaretMovementScriptHelper(self, gesture, unit, *args, **kwargs):
	# IMPORTANT: this must never change the argument shape passed through to
	# the original helper when the filter doesn't apply. An earlier version
	# of this patch assumed a fixed (self, gesture, unit, direction,
	# posConstant, ...) signature and injected posConstant=None even when
	# the caller hadn't supplied one -- that broke every caller of this
	# shared helper across the whole of NVDA (Up/Down, Ctrl+Home,
	# top-of-document, and more), not just Up/Down and Ctrl+Up/Down, because
	# _caretMovementScriptHelper is called with different argument counts
	# depending on the caller (e.g. script_topOfDocument passes no direction
	# at all; some callers pass extra keyword-only arguments). *args/**kwargs
	# now carry whatever was actually received, completely unmodified,
	# whenever this patch defers to the original.
	if unit not in (textInfos.UNIT_LINE, textInfos.UNIT_PARAGRAPH) or not _isBlankLineFilterActive(self):
		return _originalCaretMovementScriptHelper(self, gesture, unit, *args, **kwargs)
	# Every UNIT_LINE/UNIT_PARAGRAPH caller in NVDA's own source (Up/Down,
	# Ctrl+Up/Down) passes direction as the first argument after unit; if
	# that's not the shape actually received here, this patch can't safely
	# guess which way to skip, so it defers to the original rather than
	# risk breaking a caller it doesn't recognise.
	direction = args[0] if args else kwargs.get("direction")
	if direction not in (1, -1):
		return _originalCaretMovementScriptHelper(self, gesture, unit, *args, **kwargs)
	try:
		stepsRemaining = MAX_BLANK_LINE_SKIP_STEPS
		while stepsRemaining > 0:
			stepsRemaining -= 1
			beforeInfo = self.makeTextInfo(textInfos.POSITION_CARET)
			# Move for real (so braille, review position, and document-boundary
			# handling all behave exactly as they normally would), but with
			# speech silenced for this one step -- restored immediately after
			# in a finally block, mirroring the duck/restore-on-all-exit-paths
			# discipline used elsewhere in this codebase for any temporary
			# global state change. This is a runtime mode flag NVDA's own
			# NVDA+S command toggles, not a patch of the speech functions
			# themselves, so it doesn't run afoul of the ban on monkeypatching
			# speech.speak/internal speech functions.
			originalSpeechMode = getattr(speech, "speechMode", None)
			try:
				if originalSpeechMode is not None:
					speech.speechMode = speech.speechMode_off
				_originalCaretMovementScriptHelper(self, gesture, unit, *args, **kwargs)
			finally:
				if originalSpeechMode is not None:
					speech.speechMode = originalSpeechMode
			afterInfo = self.makeTextInfo(textInfos.POSITION_CARET)
			if not _isTextInfoBlank(afterInfo) or beforeInfo.compareEndPoints(afterInfo, "startToStart") == 0:
				# Either landed on real content, or the document boundary was
				# reached without moving at all -- either way this is where
				# the user should end up, so report it for real now.
				speech.speakTextInfo(afterInfo, unit=unit, reason=controlTypes.OutputReason.CARET)
				return
		# Step budget exhausted (an entirely-blank document, or close to it) --
		# report wherever the last step landed rather than looping forever.
		finalInfo = self.makeTextInfo(textInfos.POSITION_CARET)
		speech.speakTextInfo(finalInfo, unit=unit, reason=controlTypes.OutputReason.CARET)
	except Exception as e:
		logHandler.log.debug(f"SiteMarker: blank-line skip patch error, falling back to core behaviour: {e}")
		return _originalCaretMovementScriptHelper(self, gesture, unit, *args, **kwargs)

def applyBlankLineFilterPatch():
	global _originalCaretMovementScriptHelper
	if _originalCaretMovementScriptHelper is not None:
		return
	_originalCaretMovementScriptHelper = cursorManager.CursorManager._caretMovementScriptHelper
	cursorManager.CursorManager._caretMovementScriptHelper = _patchedCaretMovementScriptHelper
	logHandler.log.info("SiteMarker: blank-line filter patch applied.")

def removeBlankLineFilterPatch():
	global _originalCaretMovementScriptHelper
	if _originalCaretMovementScriptHelper is not None:
		cursorManager.CursorManager._caretMovementScriptHelper = _originalCaretMovementScriptHelper
	_originalCaretMovementScriptHelper = None

# ----------------------------------------------------------------

class GlobalPlugin(
	globalPluginHandler.GlobalPlugin,
	ScanEngineMixin,
	ContextHelpersMixin,
	MarkerActionMixin,
):
	scriptCategory = _translate("SiteMarker")

	__gestures = {
		"kb:windows+f12": "handleSiteMarkerAction",
	}

	def __init__(self):
		super().__init__()
		self.engine = MarkerEngine()
		self.lastTapTime = 0
		self.tapCount = 0
		self.activeSiteMarkers = {}
		self.lastProcessedUrl = None
		self._lastVirtualBufferUpdate = 0
		self._jumpComboboxResumeCount = {}
		self._autoClickComboboxResumeCount = {}
		self._recentScanActivity = {}
		self._autoClickRetryState = None
		self._refreshPending = False
		# Keyed by id(treeInterceptor); see _isBufferReady. Entries are removed as
		# soon as readiness is resolved (ready or retries exhausted) so this never
		# accumulates across a long session.
		self._bufferReadySamples = {}

		# Per-marker custom shortcuts (Marker Properties' "Custom Shortcut..."
		# button): sets of raw keystroke strings currently in use by the active
		# site's markers that fall outside the standard j/f/d/z /
		# alt+j/c/x/z choices, plus a normalized-gesture-identifier lookup
		# built from them so the generic customKey scripts below can tell
		# which marker group an incoming physical key press belongs to.
		# Rebuilt by _syncCustomMarkerGestureBindings() whenever
		# refreshActiveLayout() runs.
		self._boundCustomJumpKeys = set()
		self._boundCustomAutoClickKeys = set()
		self._customJumpKeystrokeLookup = {}
		self._customJumpKeystrokeBackLookup = {}
		self._customAutoClickKeystrokeLookup = {}

		# Set eagerly here (rather than left to be lazily set by event_foreground)
		# so that a gainFocus/treeInterceptor_gainFocus event which somehow fires
		# before the first event_foreground never hits an AttributeError.
		self._isTargetActive = False

		self._autoClickScanToken = {}
		self._jumpScanToken = {}
		self._sortScanToken = None
		self._pendingReveals = {}

		self._editableTheftStreak = 0

		self._domTimerRunning = False
		self._domCheckInterval = 2000
		self._startDomCheck()

		# Bounded (see _addPrimedTreeInterceptor) so a long session visiting many
		# documents/SPA routes can't grow this without limit.
		self._primedTreeInterceptors = {}
		self._primeTextInfo = None
		self._primeToken = None

		self._multiTapTimer = None

		browseModeGestures.registerGestures(self)
		global _activePluginInstance
		_activePluginInstance = self
		applyFocusModePatch()
		applyBlankLineFilterPatch()
		self.startTreeInterceptorPoll()

		# Jump/auto-click gestures are no longer hardcoded in __gestures (see
		# customShortcuts.py) so the user can freely reassign any of them
		# without colliding with NVDA's own browse-mode single-letter
		# navigation. Rebuilding from scratch here, rather than relying on
		# the class-level __gestures dict already having bound them, keeps a
		# single source of truth: this same method is called again whenever
		# a per-marker custom shortcut is saved in Marker Properties, and
		# whenever the active site's marker set changes (see
		# _syncCustomMarkerGestureBindings in contextHelpers.py's
		# refreshActiveLayout). There is no separate Preferences-menu entry
		# for this -- shortcuts are managed directly from Marker Properties'
		# "Custom Shortcut..." button, per marker, so all shortcut
		# management stays in one place instead of being split across two
		# dialogs.
		self.reapplyCustomGestureBindings()

	def reapplyCustomGestureBindings(self):
		self.clearGestureBindings()
		fullMap = {"kb:windows+f12": "handleSiteMarkerAction"}
		fullMap.update(customShortcuts.buildEffectiveGestureMap())
		for keystroke in self._boundCustomJumpKeys:
			fullMap["kb:" + keystroke] = "siteMarker_jump_customKey"
			backKeystroke = self._backKeystrokeFor(keystroke)
			if backKeystroke:
				fullMap["kb:" + backKeystroke] = "siteMarker_jump_customKey_back"
		for keystroke in self._boundCustomAutoClickKeys:
			fullMap["kb:" + keystroke] = "siteMarker_autoClick_customKey"
		self.bindGestures(fullMap)

	def _backKeystrokeFor(self, keystroke):
		# A custom jump key automatically gets a Shift+ counterpart bound to
		# the "jump to previous" direction for the same marker group, mirroring
		# how the standard J key has always paired with Shift+J. Skipped when
		# the custom key already includes shift (nothing sensible to double up
		# on) or is itself literally "shift" alone.
		parts = keystroke.split("+")
		if "shift" in parts:
			return None
		return "shift+" + keystroke

	def _syncCustomMarkerGestureBindings(self):
		"""Scans the currently active site's markers for per-marker custom
		shortcuts (any keystroke other than the standard "j" / "alt+j")
		and makes sure the corresponding physical keys are bound, including
		an automatic Shift+ "jump to previous" counterpart for custom jump
		keys. Called from refreshActiveLayout() every time the marker set
		for the current URL is (re)loaded, so a shortcut assigned via Marker
		Properties' "Custom Shortcut..." button takes effect on the very
		next press without requiring an NVDA restart."""
		newCustomJumpKeys = set()
		newCustomAutoClickKeys = set()
		for markersForKey in self.activeSiteMarkers.values():
			for marker in markersForKey:
				keystroke = marker.get("keystroke", "").strip().lower()
				if not keystroke:
					continue
				actionMode = marker.get("actionMode", "jump")
				if actionMode == "jump" and keystroke not in STANDARD_JUMP_KEYS:
					newCustomJumpKeys.add(keystroke)
				elif actionMode == "autoClick" and keystroke not in STANDARD_AUTOCLICK_KEYS:
					newCustomAutoClickKeys.add(keystroke)
		if newCustomJumpKeys == self._boundCustomJumpKeys and newCustomAutoClickKeys == self._boundCustomAutoClickKeys:
			return
		self._boundCustomJumpKeys = newCustomJumpKeys
		self._boundCustomAutoClickKeys = newCustomAutoClickKeys
		self._customJumpKeystrokeLookup = self._buildCustomKeystrokeLookup(newCustomJumpKeys)
		backKeystrokes = {
			self._backKeystrokeFor(k) for k in newCustomJumpKeys if self._backKeystrokeFor(k)
		}
		# Map each Shift+<key> back to the *original* (non-shifted) keystroke,
		# since that's the value stored on the marker and used as the lookup
		# key into activeSiteMarkers -- the "shift+" is purely a physical-key
		# distinction, not part of the marker's own identity.
		backLookupSource = {
			self._backKeystrokeFor(k): k for k in newCustomJumpKeys if self._backKeystrokeFor(k)
		}
		self._customJumpKeystrokeBackLookup = self._buildCustomKeystrokeLookup(
			backKeystrokes, remapTo=backLookupSource
		)
		self._customAutoClickKeystrokeLookup = self._buildCustomKeystrokeLookup(newCustomAutoClickKeys)
		self.reapplyCustomGestureBindings()

	def _buildCustomKeystrokeLookup(self, keystrokeSet, remapTo=None):
		# Maps NVDA's own normalized gesture identifier for each custom
		# keystroke back to the raw keystroke string as stored on the
		# marker, so an incoming physical key press can be matched
		# regardless of the order its component keys were actually held in.
		# When remapTo is given, the *value* stored is remapTo[keystroke]
		# rather than keystroke itself -- used for the Shift+ back-direction
		# lookup, where the physical key bound is "shift+x" but the marker
		# group it should resolve to is still "x".
		lookup = {}
		for keystroke in keystrokeSet:
			try:
				probeGesture = keyboardHandler.KeyboardInputGesture.fromName(keystroke)
			except Exception as e:
				logHandler.log.error(f"SiteMarker: invalid custom marker shortcut '{keystroke}' skipped: {e}")
				continue
			resolvedValue = remapTo[keystroke] if remapTo else keystroke
			for normId in probeGesture.normalizedIdentifiers:
				lookup[normId] = resolvedValue
		return lookup

	def _resolveCustomKeystroke(self, gesture, lookup):
		for normId in getattr(gesture, "normalizedIdentifiers", []):
			if normId in lookup:
				return lookup[normId]
		return None

	def terminate(self):
		global _activePluginInstance
		_activePluginInstance = None
		removeFocusModePatch()
		removeBlankLineFilterPatch()
		self.stopTreeInterceptorPoll()
		self._stopDomCheck()
		if self._multiTapTimer is not None:
			self._multiTapTimer.Stop()
			self._multiTapTimer = None
		self.activeSiteMarkers.clear()
		self.lastProcessedUrl = None
		self._jumpComboboxResumeCount.clear()
		self._autoClickComboboxResumeCount.clear()
		self._recentScanActivity.clear()
		self._autoClickRetryState = None
		self._autoClickScanToken.clear()
		self._jumpScanToken.clear()
		self._sortScanToken = None
		self._pendingReveals.clear()
		self._primedTreeInterceptors.clear()
		self._primeTextInfo = None
		self._primeToken = None
		self._editableTheftStreak = 0
		self._bufferReadySamples.clear()
		self._boundCustomJumpKeys.clear()
		self._boundCustomAutoClickKeys.clear()
		self._customJumpKeystrokeLookup.clear()
		self._customJumpKeystrokeBackLookup.clear()
		self._customAutoClickKeystrokeLookup.clear()
		self._getLastDispatchedTargetTexts().clear()
		self._getLastDispatchedTargetPositions().clear()
		self._getLastDispatchedAnchorPositions().clear()
		if hasattr(self.engine, "cleanUp"):
			self.engine.cleanUp()

	def _addPrimedTreeInterceptor(self, treeInt):
		"""
		Records that buffer priming has been started for this tree interceptor
		object. Bounded to MAX_PRIMED_TREE_INTERCEPTORS, evicting the oldest
		entry first, so a long session that visits many documents/SPA routes
		doesn't grow this without limit for the lifetime of the NVDA session.

		Keyed by the treeInterceptor object itself, not id(treeInterceptor):
		once a treeInterceptor is killed (SPA navigation, tab close, etc.) and
		garbage-collected, CPython is free to reuse its old id() value for an
		unrelated, brand-new object. A dict keyed by that bare id() would then
		wrongly treat the new, never-primed document as "already primed" --
		this was a confirmed, reproducible failure mode (buffer priming and
		the auto-trigger-on-page-load check both silently never firing again
		after the first page of a session) once enough distinct documents had
		been visited for an id() collision to become likely. Keeping the
		object itself as the key sidesteps the whole class of bug: Python's
		default identity-based hashing/equality applies, and holding the
		reference here means the address can't be reused for something else
		while it's still considered "primed" -- eviction below is what
		actually lets it go, not garbage collection timing.
		"""
		self._primedTreeInterceptors[treeInt] = True
		while len(self._primedTreeInterceptors) > MAX_PRIMED_TREE_INTERCEPTORS:
			oldestTreeInt = next(iter(self._primedTreeInterceptors))
			del self._primedTreeInterceptors[oldestTreeInt]

	# ----- Jump gestures (documented for Input Gestures) -----
	# Only "j" remains a built-in default; every other jump key is now a
	# per-marker custom shortcut (see below) so nothing is taken from NVDA's
	# own single-letter browse-mode navigation (F, D, Z, etc.) unless the
	# user explicitly assigns it to a marker.
	def script_siteMarker_jump_j(self, gesture):
		"""Jump to the next marker assigned to the J key"""
		self._handleGlobalJump(gesture, "j", 1)
	def script_siteMarker_jump_j_back(self, gesture):
		"""Jump to the previous marker assigned to the J key"""
		self._handleGlobalJump(gesture, "j", -1)

	# ----- Auto‑click gestures (documented for Input Gestures) -----
	# Only "alt+j" remains a built-in default; see the note above.
	def script_siteMarker_autoClick_j(self, gesture):
		"""Auto‑click the element matching the marker for Alt+J"""
		self._handleAutoClick(gesture, "alt+j")

	# ----- Per-marker custom shortcuts (Marker Properties "Custom Shortcut...") -----
	# A single script bound to potentially many different physical gestures at
	# once -- one per distinct custom keystroke currently in use by the active
	# site's markers -- rather than a dynamically-created script per keystroke.
	# _resolveCustomKeystroke below determines which marker group the actual
	# incoming key press belongs to.
	def script_siteMarker_jump_customKey(self, gesture):
		"""Jump to the next marker assigned to a custom shortcut"""
		keystroke = self._resolveCustomKeystroke(gesture, self._customJumpKeystrokeLookup)
		if keystroke is None:
			gesture.send()
			return
		self._handleGlobalJump(gesture, keystroke, 1)

	def script_siteMarker_jump_customKey_back(self, gesture):
		"""Jump to the previous marker assigned to a custom shortcut (Shift+ the
		custom key, bound automatically alongside it -- see
		_syncCustomMarkerGestureBindings)"""
		keystroke = self._resolveCustomKeystroke(gesture, self._customJumpKeystrokeBackLookup)
		if keystroke is None:
			gesture.send()
			return
		self._handleGlobalJump(gesture, keystroke, -1)

	def script_siteMarker_autoClick_customKey(self, gesture):
		"""Auto‑click the element matching a marker assigned to a custom shortcut"""
		keystroke = self._resolveCustomKeystroke(gesture, self._customAutoClickKeystrokeLookup)
		if keystroke is None:
			gesture.send()
			return
		self._handleAutoClick(gesture, keystroke)

	# ----- Multi‑tap action (Windows+F12) -----
	# Original mapping: 1 tap = Add/Edit Marker, 2 taps = Site Manager, 3 taps = Marker Manager
	def script_handleSiteMarkerAction(self, gesture):
		"""Single tap: Add/Edit Marker. Double tap: Site Manager. Triple tap: Marker Manager."""
		if not self._isInBrowser():
			gesture.send()
			return

		currentTime = time.time()
		if currentTime - self.lastTapTime > 0.4:  # TAP_THRESHOLD
			self.tapCount = 0
		self.tapCount += 1
		self.lastTapTime = currentTime

		if self._multiTapTimer is not None:
			self._multiTapTimer.Stop()
			self._multiTapTimer = None

		def dispatchAction():
			myTapCount = self.tapCount
			self.tapCount = 0

			import gui as nvdaGui
			currentUrl = self.getBrowserUrl()

			if myTapCount == 1:  # Add / Edit Marker
				if not currentUrl:
					ui.message(_translate("Cannot capture browser URL."))
					return
				siteName, siteConfig = self.getCurrentSiteConfig()
				if siteName and siteConfig:
					webFocusObj = self.getRealWebFocusObject()
					found = self._findMarkerInCurrentParagraph(siteConfig, webFocusObj)
					if found:
						existingMarker, existingIndex, paraText = found
						def openEditDialog():
							dlg = None
							try:
								dlg = MarkerEditDialog(nvdaGui.mainFrame, existingMarker, initialText=paraText)
								dlg.Raise()
								if dlg.ShowModal() == wx.ID_OK:
									updatedMarker = dlg.getMarkerData()
									siteConfig["markers"][existingIndex] = updatedMarker
									self.engine.saveSiteConfiguration(siteName, siteConfig)
									self.refreshActiveLayout(force=True)
							except Exception as e:
								logHandler.log.error(f"Failed to open marker edit dialog: {e}")
								ui.message(_translate("Could not open marker editor."))
							finally:
								if dlg:
									try:
										dlg.Destroy()
									except RuntimeError:
										pass
						wx.CallAfter(openEditDialog)
					else:
						def openMarkerManager():
							dlg = None
							try:
								dlg = MarkerManagerDialog(nvdaGui.mainFrame, self.engine, siteName, siteConfig,
														  webFocusObj, autoAddMarker=True, currentUrl=currentUrl)
								dlg.Raise()
								dlg.ShowModal()
							except Exception as e:
								logHandler.log.error(f"Marker manager error: {e}")
							finally:
								if dlg:
									try:
										dlg.Destroy()
									except RuntimeError:
										pass
								self.refreshActiveLayout(force=True)
						wx.CallAfter(openMarkerManager)
				else:
					ui.message(_translate("No site configuration found for current URL. Double tap to add new site."))

			elif myTapCount == 2:  # Site Manager / Add site
				if not currentUrl:
					ui.message(_translate("Cannot capture browser URL."))
					return
				siteName, siteConfig = self.getCurrentSiteConfig()
				currentSiteName = siteName if siteName else None
				def openSiteDialog():
					dlg = None
					try:
						if currentSiteName and siteConfig:
							dlg = SiteManagerDialog(nvdaGui.mainFrame, self.engine, currentUrl, selectedSiteName=currentSiteName)
						else:
							dlg = AddSiteDialog(nvdaGui.mainFrame, self.engine, currentUrl)
						dlg.Raise()
						dlg.ShowModal()
					except Exception as e:
						logHandler.log.error(f"Site dialog error: {e}")
					finally:
						if dlg:
							try:
								dlg.Destroy()
							except RuntimeError:
								pass
						self.refreshActiveLayout(force=True)
				wx.CallAfter(openSiteDialog)

			elif myTapCount >= 3:  # Marker Manager
				if not currentUrl:
					ui.message(_translate("Cannot capture browser URL."))
					return
				siteName, siteConfig = self.getCurrentSiteConfig()
				if not siteName or not siteConfig:
					ui.message(_translate("No site configuration found. Add a site first."))
					return
				def openMarkerManagerTriple():
					dlg = None
					try:
						dlg = MarkerManagerDialog(nvdaGui.mainFrame, self.engine, siteName, siteConfig, None,
												  currentUrl=currentUrl)
						dlg.Raise()
						dlg.ShowModal()
					except Exception as e:
						logHandler.log.error(f"Triple-tap marker manager error: {e}")
					finally:
						if dlg:
							try:
								dlg.Destroy()
							except RuntimeError:
								pass
						self.refreshActiveLayout(force=True)
				wx.CallAfter(openMarkerManagerTriple)

		self._multiTapTimer = core.callLater(400, dispatchAction)  # 0.4 sec

	def _findMarkerInCurrentParagraph(self, siteConfig, webFocusObj):
		if not webFocusObj or not siteConfig:
			return None
		paragraphText = self._getCurrentParagraphText(webFocusObj)
		if not paragraphText:
			return None
		markers = siteConfig.get("markers", [])
		for idx, marker in enumerate(markers):
			markerPattern = marker.get("pattern", "").strip()
			matchMode = marker.get("matchMode", 0)
			if matchMode == 0:
				if markerPattern.lower() in paragraphText.lower():
					return marker, idx, paragraphText
			elif matchMode == 1:
				if markerPattern.lower() == paragraphText.lower():
					return marker, idx, paragraphText
			elif matchMode == 2:
				try:
					if re.search(markerPattern, paragraphText, re.IGNORECASE):
						return marker, idx, paragraphText
				except Exception:
					pass
		return None

	def _getCurrentParagraphText(self, webFocusObj):
		try:
			if not webFocusObj or not hasattr(webFocusObj, "treeInterceptor") or webFocusObj.treeInterceptor is None:
				return ""
			treeInt = webFocusObj.treeInterceptor
			try:
				caretPos = treeInt.makeTextInfo(textInfos.POSITION_CARET)
				if caretPos:
					caretPos.expand(textInfos.UNIT_PARAGRAPH)
					return caretPos.text.strip()
			except Exception:
				pass
			focusObj = api.getFocusObject()
			if focusObj:
				name = getattr(focusObj, "name", "")
				if name and name.strip():
					return name.strip()
			return ""
		except Exception:
			return ""

	def _startDomCheck(self):
		if self._domTimerRunning:
			return
		self._domTimerRunning = True
		core.callLater(self._domCheckInterval, self._doDomCheck)

	def _stopDomCheck(self):
		self._domTimerRunning = False

	def _doDomCheck(self):
		if not self._domTimerRunning:
			return
		try:
			focusObj = api.getFocusObject()
			treeInt = getattr(focusObj, "treeInterceptor", None) if focusObj else None
			if treeInt and isinstance(treeInt, browseMode.BrowseModeTreeInterceptor):
				self.refreshActiveLayout(force=False)
		except Exception as e:
			logHandler.log.debug(f"DOM check refresh failed: {e}")
		core.callLater(self._domCheckInterval, self._doDomCheck)





