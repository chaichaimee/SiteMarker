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
import logHandler
import api
import ui
import core
import speech
import textInfos
from comtypes import COMError

from .markerEngine import MarkerEngine
from .gui import MarkerEditDialog, SiteManagerDialog, MarkerManagerDialog, AddSiteDialog
from . import browseModeGestures
from .scanEngine import ScanEngineMixin
from .contextHelpers import ContextHelpersMixin
from .markerAction import MarkerActionMixin
from .constants import MAX_PRIMED_TREE_INTERCEPTORS

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
		"kb:j": "siteMarker_jump_j",
		"kb:shift+j": "siteMarker_jump_j_back",
		"kb:f": "siteMarker_jump_f",
		"kb:shift+f": "siteMarker_jump_f_back",
		"kb:d": "siteMarker_jump_d",
		"kb:shift+d": "siteMarker_jump_d_back",
		"kb:z": "siteMarker_jump_z",
		"kb:shift+z": "siteMarker_jump_z_back",
		"kb:alt+j": "siteMarker_autoClick_j",
		"kb:alt+c": "siteMarker_autoClick_c",
		"kb:alt+x": "siteMarker_autoClick_x",
		"kb:alt+z": "siteMarker_autoClick_z",
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

	def terminate(self):
		global _activePluginInstance
		_activePluginInstance = None
		removeFocusModePatch()
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
		self._getLastDispatchedTargetTexts().clear()
		self._getLastDispatchedTargetPositions().clear()
		self._getLastDispatchedAnchorPositions().clear()
		if hasattr(self.engine, "cleanUp"):
			self.engine.cleanUp()

	def _addPrimedTreeInterceptor(self, treeIntId):
		"""
		Records that buffer priming has been started for this tree interceptor id.
		Bounded to MAX_PRIMED_TREE_INTERCEPTORS, evicting the oldest entry first, so a
		long session that visits many documents/SPA routes doesn't grow this without
		limit for the lifetime of the NVDA session.
		"""
		self._primedTreeInterceptors[treeIntId] = True
		while len(self._primedTreeInterceptors) > MAX_PRIMED_TREE_INTERCEPTORS:
			oldestId = next(iter(self._primedTreeInterceptors))
			del self._primedTreeInterceptors[oldestId]

	# ----- Jump gestures (documented for Input Gestures) -----
	def script_siteMarker_jump_j(self, gesture):
		"""Jump to the next marker assigned to the J key"""
		self._handleGlobalJump(gesture, "j", 1)
	def script_siteMarker_jump_j_back(self, gesture):
		"""Jump to the previous marker assigned to the J key"""
		self._handleGlobalJump(gesture, "j", -1)
	def script_siteMarker_jump_f(self, gesture):
		"""Jump to the next marker assigned to the F key"""
		self._handleGlobalJump(gesture, "f", 1)
	def script_siteMarker_jump_f_back(self, gesture):
		"""Jump to the previous marker assigned to the F key"""
		self._handleGlobalJump(gesture, "f", -1)
	def script_siteMarker_jump_d(self, gesture):
		"""Jump to the next marker assigned to the D key"""
		self._handleGlobalJump(gesture, "d", 1)
	def script_siteMarker_jump_d_back(self, gesture):
		"""Jump to the previous marker assigned to the D key"""
		self._handleGlobalJump(gesture, "d", -1)
	def script_siteMarker_jump_z(self, gesture):
		"""Jump to the next marker assigned to the Z key"""
		self._handleGlobalJump(gesture, "z", 1)
	def script_siteMarker_jump_z_back(self, gesture):
		"""Jump to the previous marker assigned to the Z key"""
		self._handleGlobalJump(gesture, "z", -1)

	# ----- Auto‑click gestures (documented for Input Gestures) -----
	def script_siteMarker_autoClick_j(self, gesture):
		"""Auto‑click the element matching the marker for Alt+J"""
		self._handleAutoClick(gesture, "alt+j")
	def script_siteMarker_autoClick_c(self, gesture):
		"""Auto‑click the element matching the marker for Alt+C"""
		self._handleAutoClick(gesture, "alt+c")
	def script_siteMarker_autoClick_x(self, gesture):
		"""Auto‑click the element matching the marker for Alt+X"""
		self._handleAutoClick(gesture, "alt+x")
	def script_siteMarker_autoClick_z(self, gesture):
		"""Auto‑click the element matching the marker for Alt+Z"""
		self._handleAutoClick(gesture, "alt+z")

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





