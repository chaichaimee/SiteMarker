# __init__.py
# Copyright (C) 2026 Chai Chaimee
# Licensed under GNU General Public License. See COPYING.txt for details.

import os
import sys
import threading
import time
import re
import ctypes
import wx
from enum import Enum
import addonHandler
import globalPluginHandler
import api
import ui
import core
import speech
import logHandler
import controlTypes
import textInfos
import browseMode
from comtypes import COMError
from .markerEngine import MarkerEngine
from .gui import MarkerEditDialog, SiteManagerDialog, MarkerManagerDialog, AddSiteDialog
from . import browseModeGestures

addonHandler.initTranslation()

_translate = _

TAP_THRESHOLD = 0.4
VIEWPORT_SCAN_RANGE = 50
AUTO_CLICK_RETRY_DELAY = 500
MAX_CLICK_RETRIES = 2

MAX_LOAD_MORE_ATTEMPTS = 100
MAX_NO_GROWTH_ATTEMPTS = 25
LOAD_MORE_TIMEOUT_MS = 800
SCAN_PARAGRAPHS_PER_CHUNK = 50
MAX_SCAN_PARAGRAPHS_TOTAL = 8000
PAGE_DOWN_BATCH_SIZE = 3

# ------------------------- Focus Mode Enum -------------------------
class FocusMode(Enum):
	UNCHANGED = 0
	DONT_ENTER_FORM_MODE = 1
	DISABLE_FOCUS = 2

# ------------------------- Patch for Focus Mode (shouldPassThrough only) -------------------------
_originalShouldPassThrough = None
_activePluginInstance = None

def _patchedShouldPassThrough(self, obj, reason=None):
	focusMode = _getCurrentSiteFocusMode()
	if focusMode == FocusMode.DISABLE_FOCUS:
		return self.passThrough
	if reason == controlTypes.OutputReason.FOCUS and focusMode == FocusMode.DONT_ENTER_FORM_MODE:
		return self.passThrough
	return _originalShouldPassThrough(self, obj, reason)

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

class GlobalPlugin(globalPluginHandler.GlobalPlugin):
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
		self.lastJumpInfo = {}
		self._autoClickRetryState = None
		self._refreshPending = False

		self._autoClickScanToken = {}
		self._jumpScanToken = {}
		self._pendingRevealCallback = None
		self._pendingRevealTreeInt = None
		self._pendingJumpRevealCallback = None
		self._pendingJumpRevealTreeInt = None

		self._domTimerRunning = False
		self._domCheckInterval = 2000
		self._startDomCheck()

		self._primedTreeInterceptors = set()
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
		self.lastJumpInfo.clear()
		self._autoClickRetryState = None
		self._autoClickScanToken.clear()
		self._jumpScanToken.clear()
		self._pendingRevealCallback = None
		self._pendingRevealTreeInt = None
		self._pendingJumpRevealCallback = None
		self._pendingJumpRevealTreeInt = None
		self._primedTreeInterceptors.clear()
		self._primeTextInfo = None
		self._primeToken = None
		if hasattr(self.engine, "cleanUp"):
			self.engine.cleanUp()

	def _isInBrowser(self):
		return self.getBrowserUrl() is not None

	def isInEditableContext(self):
		focusObj = api.getFocusObject()
		if not focusObj:
			return False
		return focusObj.role in (controlTypes.Role.EDITABLETEXT, controlTypes.Role.COMBOBOX)

	def _objectLooksEditableOrCombobox(self, obj):
		if not obj:
			return False
		try:
			role = obj.role
		except Exception:
			return False
		comboRoles = [controlTypes.Role.EDITABLETEXT, controlTypes.Role.COMBOBOX]
		searchboxRole = getattr(controlTypes.Role, "SEARCHBOX", None)
		if searchboxRole is not None:
			comboRoles.append(searchboxRole)
		if role in comboRoles:
			return True
		try:
			states = obj.states
		except Exception:
			states = set()
		if controlTypes.State.EDITABLE in states:
			return True
		autoCompleteState = getattr(controlTypes.State, "AUTOCOMPLETE", None)
		if autoCompleteState is not None and autoCompleteState in states:
			return True
		return False

	def _objectHasSearchAttributes(self, obj):
		try:
			attrs = getattr(obj, "IA2Attributes", None)
		except Exception:
			attrs = None
		if not attrs:
			return False
		try:
			textInputType = attrs.get("text-input-type", "")
			if textInputType == "search":
				return True
			xmlRoles = attrs.get("xml-roles", "")
			if "combobox" in xmlRoles or "searchbox" in xmlRoles:
				return True
			autoCompleteAttr = attrs.get("autocomplete", "")
			if autoCompleteAttr and autoCompleteAttr != "off":
				return True
		except Exception:
			return False
		return False

	def _isRealObjectComboboxLike(self, textInfo):
		realObj = None
		try:
			realObj = textInfo.focusableNVDAObjectAtStart
		except Exception:
			realObj = None
		if not realObj:
			try:
				realObj = textInfo.NVDAObjectAtStart
			except Exception:
				return False
		if not realObj:
			return False
		ancestor = realObj
		depth = 0
		while ancestor and depth < 6:
			if self._objectLooksEditableOrCombobox(ancestor):
				return True
			if self._objectHasSearchAttributes(ancestor):
				return True
			try:
				ancestor = ancestor.parent
			except Exception:
				break
			depth += 1
		return False

	def _isFieldsComboboxLike(self, textInfo):
		try:
			checkInfo = textInfo.copy()
			checkInfo.collapse()
			checkInfo.expand(textInfos.UNIT_PARAGRAPH)
			fields = checkInfo.getTextWithFields()
		except COMError:
			return True
		except Exception:
			return False
		comboRoles = [controlTypes.Role.EDITABLETEXT, controlTypes.Role.COMBOBOX]
		searchboxRole = getattr(controlTypes.Role, "SEARCHBOX", None)
		if searchboxRole is not None:
			comboRoles.append(searchboxRole)
		autoCompleteState = getattr(controlTypes.State, "AUTOCOMPLETE", None)
		for field in fields:
			if not isinstance(field, textInfos.FieldCommand):
				continue
			if field.command != 'controlStart':
				continue
			role = field.field.get('role')
			if role in comboRoles:
				return True
			states = field.field.get('states', set())
			if controlTypes.State.EDITABLE in states:
				return True
			if autoCompleteState is not None and autoCompleteState in states:
				return True
		return False

	def _isLikelyComboboxTarget(self, textInfo):
		if self._isRealObjectComboboxLike(textInfo):
			return True
		if self._isFieldsComboboxLike(textInfo):
			return True
		return False

	def _isParagraphEditable(self, textInfo):
		try:
			fields = textInfo.getTextWithFields()
		except COMError:
			return True
		except Exception:
			return True
		for field in fields:
			if not isinstance(field, textInfos.FieldCommand):
				continue
			if field.command != 'controlStart':
				continue
			role = field.field.get('role')
			if role in (controlTypes.Role.EDITABLETEXT, controlTypes.Role.COMBOBOX):
				return True
			states = field.field.get('states', set())
			if controlTypes.State.EDITABLE in states:
				return True
		return False

	def _isMatchTargetSafe(self, textInfo):
		try:
			realObj = textInfo.NVDAObjectAtStart
		except COMError:
			return False
		except Exception:
			return False
		if not realObj:
			return True
		ancestor = realObj
		depth = 0
		while ancestor and depth < 6:
			if self._objectLooksEditableOrCombobox(ancestor):
				return False
			try:
				ancestor = ancestor.parent
			except Exception:
				break
			depth += 1
		return True

	def _verifyThenDispatchJump(self, infoToSelect, dirVal, skipOff, treeInt, baseKey,
							   markersForThisKey, oldSelection, vpStart, vpEnd, useViewport):
		try:
			infoToSelect.updateCaret()
			speech.speakTextInfo(infoToSelect, reason=controlTypes.OutputReason.CARET)
		except Exception as e:
			logHandler.log.error(f"Failed to update caret or speak: {e}")
			return
		isCombobox = self._isLikelyComboboxTarget(infoToSelect)
		logHandler.log.debug(f"SiteMarker: comboboxCheck result={isCombobox} for baseKey='{baseKey}'")
		if isCombobox:
			try:
				speech.cancelSpeech()
			except Exception:
				pass
			resumeInfo = infoToSelect.copy()
			newScanToken = object()
			self._jumpScanToken[baseKey] = newScanToken
			self._processJumpChunk(treeInt, markersForThisKey, baseKey, dirVal,
								   resumeInfo, oldSelection, skipOff, vpStart, vpEnd, useViewport,
								   newScanToken, 0, 0)
			return
		try:
			infoToSelect.collapse()
			if hasattr(treeInt, "selection"):
				try:
					treeInt._set_selection(infoToSelect)
				except AttributeError:
					pass
				treeInt.selection = infoToSelect
			self.lastJumpInfo[baseKey] = {
				'position': infoToSelect._startOffset,
				'direction': dirVal,
				'skipOffset': skipOff
			}
		except Exception as e:
			logHandler.log.error(f"Failed to finalize selection: {e}")

	def _isMatchInRightDirection(self, oldSelection, direction, textInfo):
		origin = oldSelection.copy()
		origin.collapse(end=direction > 0)
		origin.expand(textInfos.UNIT_PARAGRAPH)
		origin.collapse(end=direction > 0)
		if direction > 0:
			origin.move(textInfos.UNIT_CHARACTER, -1)
		cmp = origin.compareEndPoints(textInfo, "startToStart")
		return direction * cmp < 0

	def _sortMarkersByDocumentOrder(self, treeInterceptor):
		if not treeInterceptor:
			return
		for key in self.activeSiteMarkers:
			markers = self.activeSiteMarkers[key]
			if len(markers) <= 1:
				continue
			scanInfo = treeInterceptor.makeTextInfo(textInfos.POSITION_FIRST)
			orderedIndices = []
			iteration = 0
			while iteration < 2000:
				iteration += 1
				foundMarker, _ = self.engine.matchParagraph(scanInfo, markers)
				if foundMarker is not None:
					try:
						foundIdx = markers.index(foundMarker)
					except ValueError:
						pass
					else:
						if foundIdx not in orderedIndices:
							orderedIndices.append(foundIdx)
				try:
					if scanInfo.move(textInfos.UNIT_PARAGRAPH, 1) == 0:
						break
				except (RuntimeError, OSError, Exception):
					break
				scanInfo.expand(textInfos.UNIT_PARAGRAPH)
			if orderedIndices:
				remaining = [i for i in range(len(markers)) if i not in orderedIndices]
				orderedIndices.extend(remaining)
				self.activeSiteMarkers[key] = [markers[i] for i in orderedIndices]

	def _getViewportRange(self, treeInt):
		try:
			caret = treeInt.makeTextInfo(textInfos.POSITION_CARET)
		except Exception:
			return None, None
		caret.expand(textInfos.UNIT_PARAGRAPH)
		start = caret.copy()
		for _ in range(VIEWPORT_SCAN_RANGE):
			if start.move(textInfos.UNIT_PARAGRAPH, -1) == 0:
				break
			start.expand(textInfos.UNIT_PARAGRAPH)
		end = caret.copy()
		for _ in range(VIEWPORT_SCAN_RANGE):
			if end.move(textInfos.UNIT_PARAGRAPH, 1) == 0:
				break
			end.expand(textInfos.UNIT_PARAGRAPH)
		return start, end

	def _scrollRealObjectIntoView(self, textInfo):
		try:
			realObj = textInfo.NVDAObjectAtStart
		except Exception:
			return
		if not realObj or self._objectLooksEditableOrCombobox(realObj):
			return
		try:
			realObj.scrollIntoView()
		except Exception as e:
			logHandler.log.debug(f"scrollIntoView failed: {e}")

	def _sendPageDownKeystroke(self, count=1):
		try:
			VK_NEXT = 0x22
			KEYEVENTF_KEYUP = 0x0002
			for _ in range(count):
				ctypes.windll.user32.keybd_event(VK_NEXT, 0, 0, 0)
				ctypes.windll.user32.keybd_event(VK_NEXT, 0, KEYEVENTF_KEYUP, 0)
		except Exception as e:
			logHandler.log.debug(f"Page Down keystroke failed: {e}")

	def _handleAutoClick(self, gesture, keystroke):
		focusObj = api.getFocusObject()
		treeInterceptor = getattr(focusObj, "treeInterceptor", None) if focusObj else None
		if not treeInterceptor or not isinstance(treeInterceptor, browseMode.BrowseModeTreeInterceptor) or treeInterceptor.passThrough:
			if gesture: gesture.send()
			return
		if not self.getBrowserUrl():
			if gesture: gesture.send()
			return
		self.refreshActiveLayout(force=False)
		if not self.activeSiteMarkers or keystroke not in self.activeSiteMarkers:
			ui.message(_translate("No auto click marker for this key."))
			return
		clickMarkers = [m for m in self.activeSiteMarkers[keystroke] if m.get("actionMode") == "autoClick"]
		if not clickMarkers:
			ui.message(_translate("No auto click marker for this key."))
			return
		self._startAutoClickSearch(treeInterceptor, clickMarkers, gesture, keystroke)

	def _startAutoClickSearch(self, treeInt, clickMarkers, gesture, keystroke):
		scanToken = object()
		self._autoClickScanToken[keystroke] = scanToken
		try:
			textInfo = treeInt.makeTextInfo(textInfos.POSITION_FIRST)
			textInfo.collapse()
			textInfo.expand(textInfos.UNIT_PARAGRAPH)
		except Exception as e:
			logHandler.log.debug(f"AutoClick search init failed: {e}")
			return
		self._processAutoClickChunk(treeInt, clickMarkers, gesture, keystroke, textInfo, scanToken, 0, 0)

	def _processAutoClickChunk(self, treeInt, clickMarkers, gesture, keystroke,
							   textInfo, scanToken, loadMoreAttempts, noGrowthCount):
		if self._autoClickScanToken.get(keystroke) is not scanToken:
			return
		processed = 0
		reachedEnd = False
		comErrorStreak = 0
		totalScanned = loadMoreAttempts * SCAN_PARAGRAPHS_PER_CHUNK + processed
		while processed < SCAN_PARAGRAPHS_PER_CHUNK and totalScanned < MAX_SCAN_PARAGRAPHS_TOTAL:
			processed += 1
			totalScanned += 1
			try:
				if self._isParagraphEditable(textInfo):
					if textInfo.move(textInfos.UNIT_PARAGRAPH, 1) == 0:
						reachedEnd = True
						break
					textInfo.expand(textInfos.UNIT_PARAGRAPH)
					comErrorStreak = 0
					continue
				marker, _ = self.engine.matchParagraph(textInfo, clickMarkers)
				if marker:
					offsetVal = marker.get("offset", 0)
					if offsetVal != 0:
						offsetInfo = textInfo.copy()
						offsetInfo.collapse()
						offsetDir = 1 if offsetVal > 0 else -1
						for _ in range(abs(offsetVal)):
							if offsetInfo.move(textInfos.UNIT_PARAGRAPH, offsetDir) == 0:
								break
						offsetInfo.expand(textInfos.UNIT_PARAGRAPH)
						finalInfo = offsetInfo
					else:
						finalInfo = textInfo.copy()
					if not self._isMatchTargetSafe(finalInfo):
						if textInfo.move(textInfos.UNIT_PARAGRAPH, 1) == 0:
							reachedEnd = True
							break
						textInfo.expand(textInfos.UNIT_PARAGRAPH)
						comErrorStreak = 0
						continue
					def attemptAutoClick(markerVal, infoVal):
						try:
							infoVal.updateCaret()
							speech.speakTextInfo(infoVal, reason=controlTypes.OutputReason.CARET)
						except Exception as e:
							logHandler.log.error(f"Failed to prepare auto click target: {e}")
							return
						if self._isLikelyComboboxTarget(infoVal):
							try:
								speech.cancelSpeech()
							except Exception:
								pass
							resumeInfo = infoVal.copy()
							newScanToken = object()
							self._autoClickScanToken[keystroke] = newScanToken
							self._processAutoClickChunk(treeInt, clickMarkers, gesture, keystroke,
														resumeInfo, newScanToken, 0, 0)
							return
						self._executeAutoClick(markerVal, infoVal, treeInt, gesture, keystroke, skipSpeak=True)
					core.callLater(20, attemptAutoClick, marker, finalInfo.copy())
					return
				if textInfo.move(textInfos.UNIT_PARAGRAPH, 1) == 0:
					reachedEnd = True
					break
				textInfo.expand(textInfos.UNIT_PARAGRAPH)
				comErrorStreak = 0
				if processed % 15 == 0:
					self._scrollRealObjectIntoView(textInfo)
			except COMError as e:
				comErrorStreak += 1
				if comErrorStreak >= 30:
					logHandler.log.debug(f"AutoClick scan aborted after repeated COMError: {e}")
					return
				try:
					if textInfo.move(textInfos.UNIT_PARAGRAPH, 1) == 0:
						reachedEnd = True
						break
					textInfo.expand(textInfos.UNIT_PARAGRAPH)
					comErrorStreak = 0
				except Exception:
					return
				continue
			except Exception as e:
				logHandler.log.debug(f"AutoClick scan error: {e}")
				return
		if not reachedEnd:
			core.callLater(5, self._processAutoClickChunk, treeInt, clickMarkers, gesture, keystroke,
						   textInfo, scanToken, loadMoreAttempts, noGrowthCount)
			return
		if loadMoreAttempts >= MAX_LOAD_MORE_ATTEMPTS or noGrowthCount >= MAX_NO_GROWTH_ATTEMPTS:
			ui.message(_translate("No auto click target found."))
			return
		resumePoint = textInfo.copy()
		def afterScroll():
			self._processAutoClickChunk(treeInt, clickMarkers, gesture, keystroke,
										resumePoint, scanToken, loadMoreAttempts + 1, noGrowthCount + 1)
		self._scrollAndWaitForUpdate(treeInt, afterScroll)

	def _scrollAndWaitForUpdate(self, treeInt, callback):
		self._sendPageDownKeystroke(PAGE_DOWN_BATCH_SIZE)
		self._pendingRevealCallback = callback
		self._pendingRevealTreeInt = treeInt
		core.callLater(LOAD_MORE_TIMEOUT_MS, self._revealTimeout, treeInt)

	def _revealTimeout(self, treeInt):
		if self._pendingRevealCallback and self._pendingRevealTreeInt == treeInt:
			cb = self._pendingRevealCallback
			self._pendingRevealCallback = None
			self._pendingRevealTreeInt = None
			cb()

	def _executeAutoClick(self, marker, targetInfo, treeInt, gesture, keystroke, retryCount=0, skipSpeak=False):
		try:
			targetInfo.updateCaret()
			if hasattr(treeInt, "selection"):
				try:
					treeInt._set_selection(targetInfo)
				except AttributeError:
					pass
				treeInt.selection = targetInfo
			try:
				treeInt.activatePosition(targetInfo)
			except Exception as e:
				logHandler.log.debug(f"Native activation failed: {e}")
				focusable = targetInfo.focusableNVDAObjectAtStart
				if focusable:
					focusable.doAction()
			if not skipSpeak:
				speech.speakTextInfo(targetInfo, reason=controlTypes.OutputReason.CARET)
			ui.message(_translate("Clicked"))
			currentUrl = self.getBrowserUrl()
			paragraphText = targetInfo.text.strip()
			self._autoClickRetryState = {
				"keystroke": keystroke,
				"gesture": gesture,
				"urlBefore": currentUrl,
				"textBefore": paragraphText,
				"marker": marker,
				"retryCount": retryCount,
				"targetInfoStartOffset": targetInfo._startOffset,
				"treeIntId": id(treeInt)
			}
			core.callLater(AUTO_CLICK_RETRY_DELAY, self._checkAutoClickRetry)
		except Exception as e:
			logHandler.log.error(f"Click action failed: {e}")
			ui.message(_translate("Click failed."))
			self._autoClickRetryState = None

	def _checkAutoClickRetry(self):
		state = self._autoClickRetryState
		if not state:
			return
		keystroke = state["keystroke"]
		gesture = state["gesture"]
		currentUrl = self.getBrowserUrl()
		if currentUrl != state["urlBefore"]:
			self._autoClickRetryState = None
			return
		treeInt = None
		focusObj = api.getFocusObject()
		if focusObj:
			treeInt = getattr(focusObj, "treeInterceptor", None)
		if not treeInt or id(treeInt) != state["treeIntId"]:
			self._autoClickRetryState = None
			return
		try:
			currentInfo = treeInt.makeTextInfo(textInfos.POSITION_ALL)
			currentInfo.collapse()
			currentInfo.expand(textInfos.UNIT_PARAGRAPH)
			iteration = 0
			while iteration < 2000:
				iteration += 1
				if currentInfo._startOffset == state["targetInfoStartOffset"]:
					if currentInfo.text.strip() == state["textBefore"]:
						retryCount = state["retryCount"] + 1
						if retryCount <= MAX_CLICK_RETRIES:
							self._autoClickRetryState = None
							self._executeAutoClick(
								state["marker"], currentInfo.copy(), treeInt, gesture, keystroke, retryCount
							)
							return
					break
				if currentInfo.move(textInfos.UNIT_PARAGRAPH, 1) == 0:
					break
				currentInfo.expand(textInfos.UNIT_PARAGRAPH)
		except Exception as e:
			logHandler.log.error(f"Retry check failed: {e}")
		self._autoClickRetryState = None

	def _handleGlobalJump(self, gesture, baseKey, direction):
		focusObj = api.getFocusObject()
		treeInterceptor = getattr(focusObj, "treeInterceptor", None) if focusObj else None
		if not treeInterceptor or not isinstance(treeInterceptor, browseMode.BrowseModeTreeInterceptor) or treeInterceptor.passThrough:
			if gesture: gesture.send()
			return
		if not self.getBrowserUrl():
			if gesture: gesture.send()
			return
		self.refreshActiveLayout(force=False)
		if not self.activeSiteMarkers or baseKey not in self.activeSiteMarkers:
			if direction == 1:
				ui.message(_translate("No next marker."))
			else:
				ui.message(_translate("No previous marker."))
			return
		markersForThisKey = self.activeSiteMarkers[baseKey]
		treeInt = treeInterceptor
		oldSelection = treeInt.selection.copy() if hasattr(treeInt, "selection") else treeInt.makeTextInfo(textInfos.POSITION_CARET)
		useViewport = any(m.get("scope", "document") == "viewport" for m in markersForThisKey)
		vpStart, vpEnd = None, None
		if useViewport:
			vpStart, vpEnd = self._getViewportRange(treeInt)
		if direction < 0:
			try:
				backupCaret = treeInt.makeTextInfo(textInfos.POSITION_CARET)
				backupCaret.move(textInfos.UNIT_CHARACTER, -1)
				backupCaret.select()
			except Exception:
				pass
		skipPos = None
		lastInfo = self.lastJumpInfo.get(baseKey)
		if lastInfo and lastInfo.get('direction') == direction:
			skipPos = lastInfo.get('skipOffset')
		scanToken = object()
		self._jumpScanToken[baseKey] = scanToken
		try:
			textInfo = treeInt.makeTextInfo(textInfos.POSITION_CARET)
			textInfo.collapse()
			textInfo.expand(textInfos.UNIT_PARAGRAPH)
		except Exception as e:
			logHandler.log.debug(f"Jump scan init failed: {e}")
			self._finishJumpNotFound(direction, treeInt)
			return
		self._processJumpChunk(
			treeInt, markersForThisKey, baseKey, direction,
			textInfo, oldSelection, skipPos, vpStart, vpEnd, useViewport,
			scanToken, 0, 0
		)

	def _processJumpChunk(self, treeInt, markersForThisKey, baseKey, direction,
						  textInfo, oldSelection, skipPos, vpStart, vpEnd, useViewport,
						  scanToken, loadMoreAttempts, noGrowthCount):
		if self._jumpScanToken.get(baseKey) is not scanToken:
			return
		processed = 0
		reachedEnd = False
		comErrorStreak = 0
		totalScanned = loadMoreAttempts * SCAN_PARAGRAPHS_PER_CHUNK + processed
		while processed < SCAN_PARAGRAPHS_PER_CHUNK and totalScanned < MAX_SCAN_PARAGRAPHS_TOTAL:
			processed += 1
			totalScanned += 1
			try:
				moveResult = textInfo.move(textInfos.UNIT_PARAGRAPH, direction)
				if moveResult == 0:
					reachedEnd = True
					break
				textInfo.expand(textInfos.UNIT_PARAGRAPH)
				comErrorStreak = 0
				if self._isParagraphEditable(textInfo):
					continue
				if useViewport and vpStart and vpEnd:
					if direction > 0 and textInfo.compareEndPoints(vpEnd, "startToStart") > 0:
						reachedEnd = True
						break
					if direction < 0 and textInfo.compareEndPoints(vpStart, "startToStart") < 0:
						reachedEnd = True
						break
				if skipPos is not None and textInfo._startOffset == skipPos:
					continue
				marker, matchObj = self.engine.matchParagraph(textInfo, markersForThisKey)
				if not marker:
					continue
				try:
					matchedText = textInfo.text.strip()[:60]
				except Exception:
					matchedText = "<unavailable>"
				logHandler.log.debug(
					f"SiteMarker: marker matched for key '{baseKey}' pattern='{marker.get('pattern', '')}' "
					f"matchMode={marker.get('matchMode', 0)} paragraphText='{matchedText}'"
				)
				offsetVal = marker.get("offset", 0)
				if offsetVal != 0:
					offsetInfo = textInfo.copy()
					offsetInfo.collapse()
					offsetDir = 1 if offsetVal > 0 else -1
					for _ in range(abs(offsetVal)):
						if offsetInfo.move(textInfos.UNIT_PARAGRAPH, offsetDir) == 0:
							break
					offsetInfo.expand(textInfos.UNIT_PARAGRAPH)
					finalInfo = offsetInfo
				else:
					finalInfo = textInfo.copy()
				if not self._isMatchInRightDirection(oldSelection, direction, finalInfo):
					continue
				if not self._isMatchTargetSafe(finalInfo):
					continue
				skipOffset = textInfo._startOffset
				core.callLater(10, self._verifyThenDispatchJump, finalInfo.copy(), direction, skipOffset,
							   treeInt, baseKey, markersForThisKey, oldSelection, vpStart, vpEnd, useViewport)
				return
			except COMError as e:
				comErrorStreak += 1
				if comErrorStreak >= 30:
					logHandler.log.debug(f"Jump scan aborted after repeated COMError: {e}")
					self._finishJumpNotFound(direction, treeInt)
					return
				try:
					if textInfo.move(textInfos.UNIT_PARAGRAPH, direction) == 0:
						reachedEnd = True
						break
					textInfo.expand(textInfos.UNIT_PARAGRAPH)
					comErrorStreak = 0
				except Exception:
					self._finishJumpNotFound(direction, treeInt)
					return
				continue
			except Exception as e:
				logHandler.log.debug(f"Jump scan error: {e}")
				return
			if processed % 15 == 0:
				self._scrollRealObjectIntoView(textInfo)
		if not reachedEnd:
			core.callLater(5, self._processJumpChunk,
						   treeInt, markersForThisKey, baseKey, direction,
						   textInfo, oldSelection, skipPos, vpStart, vpEnd, useViewport,
						   scanToken, loadMoreAttempts, noGrowthCount)
			return
		if loadMoreAttempts >= MAX_LOAD_MORE_ATTEMPTS or noGrowthCount >= MAX_NO_GROWTH_ATTEMPTS:
			self._finishJumpNotFound(direction, treeInt)
			return
		if not useViewport:
			resumePoint = textInfo.copy()
			def afterScroll():
				self._processJumpChunk(
					treeInt, markersForThisKey, baseKey, direction,
					resumePoint, oldSelection, skipPos, vpStart, vpEnd, useViewport,
					scanToken, loadMoreAttempts + 1, noGrowthCount + 1
				)
			self._scrollAndWaitForUpdate(treeInt, afterScroll)
		else:
			self._finishJumpNotFound(direction, treeInt)

	def _finishJumpNotFound(self, direction, treeInt):
		if direction < 0:
			try:
				restoreCaret = treeInt.makeTextInfo(textInfos.POSITION_CARET)
				restoreCaret.move(textInfos.UNIT_CHARACTER, 1)
				restoreCaret.select()
			except Exception:
				pass
		if direction == 1:
			ui.message(_translate("No next marker."))
		else:
			ui.message(_translate("No previous marker."))

	def script_siteMarker_jump_j(self, gesture):
		self._handleGlobalJump(gesture, "j", 1)
	def script_siteMarker_jump_j_back(self, gesture):
		self._handleGlobalJump(gesture, "j", -1)
	def script_siteMarker_jump_f(self, gesture):
		self._handleGlobalJump(gesture, "f", 1)
	def script_siteMarker_jump_f_back(self, gesture):
		self._handleGlobalJump(gesture, "f", -1)
	def script_siteMarker_jump_d(self, gesture):
		self._handleGlobalJump(gesture, "d", 1)
	def script_siteMarker_jump_d_back(self, gesture):
		self._handleGlobalJump(gesture, "d", -1)
	def script_siteMarker_jump_z(self, gesture):
		self._handleGlobalJump(gesture, "z", 1)
	def script_siteMarker_jump_z_back(self, gesture):
		self._handleGlobalJump(gesture, "z", -1)

	def script_siteMarker_autoClick_j(self, gesture):
		self._handleAutoClick(gesture, "alt+j")
	def script_siteMarker_autoClick_c(self, gesture):
		self._handleAutoClick(gesture, "alt+c")
	def script_siteMarker_autoClick_x(self, gesture):
		self._handleAutoClick(gesture, "alt+x")
	def script_siteMarker_autoClick_z(self, gesture):
		self._handleAutoClick(gesture, "alt+z")

	def getRealWebFocusObject(self):
		focusObj = api.getFocusObject()
		if not focusObj: 
			return None
			
		if getattr(focusObj, "treeInterceptor", None) is not None:
			return focusObj
			
		if focusObj.role not in (controlTypes.Role.DOCUMENT, controlTypes.Role.PANE, controlTypes.Role.APPLICATION, controlTypes.Role.WINDOW):
			return focusObj

		try:
			for i, child in enumerate(focusObj.children):
				if i >= 5:
					break
				if getattr(child, "treeInterceptor", None) is not None:
					return child
		except Exception:
			pass
			
		return focusObj

	def getBrowserUrl(self):
		focusObj = api.getFocusObject()
		treeInt = getattr(focusObj, "treeInterceptor", None) if focusObj else None
		
		if not treeInt:
			realObj = self.getRealWebFocusObject()
			treeInt = getattr(realObj, "treeInterceptor", None) if realObj else None
			
		if treeInt and hasattr(treeInt, "documentConstantIdentifier"):
			urlStr = treeInt.documentConstantIdentifier
			if urlStr and (urlStr.startswith("http") or urlStr.startswith("https") or urlStr.startswith("file")):
				return urlStr
				
		if self.lastProcessedUrl: 
			return self.lastProcessedUrl
		return None

	def getCurrentSiteConfig(self):
		currentUrl = self.getBrowserUrl()
		if not currentUrl: return None, None
		for siteName, siteConfig in self.engine.siteCache.items():
			if self.engine.checkUrlMatch(siteConfig.get("matchType", 0), siteConfig.get("pattern", ""), currentUrl):
				return siteName, siteConfig
		return None, None

	def refreshActiveLayout(self, force=False):
		currentUrl = self.getBrowserUrl()
		if not currentUrl:
			self.activeSiteMarkers = {}
			self.lastProcessedUrl = None
			self.lastJumpInfo.clear()
			return
		if not force and currentUrl == self.lastProcessedUrl and self.activeSiteMarkers:
			return
		rawMarkers = self.engine.getMarkersForUrl(currentUrl)
		self.activeSiteMarkers = {k.strip().lower(): v for k, v in rawMarkers.items() if k.strip()}
		self.lastProcessedUrl = currentUrl
		self.lastJumpInfo.clear()
		focusObj = api.getFocusObject()
		treeInt = getattr(focusObj, "treeInterceptor", None) if focusObj else None
		if treeInt:
			self._sortMarkersByDocumentOrder(treeInt)

	def event_gainFocus(self, obj, nextHandler):
		treeInt = getattr(obj, "treeInterceptor", None)
		if treeInt is not None and isinstance(treeInt, browseMode.BrowseModeTreeInterceptor):
			self.refreshActiveLayout(force=False)
		nextHandler()

	def event_treeInterceptor_gainFocus(self, treeInterceptor, nextHandler):
		self._pendingRevealCallback = None
		self._pendingRevealTreeInt = None
		self._pendingJumpRevealCallback = None
		self._pendingJumpRevealTreeInt = None
		self.refreshActiveLayout(force=False)
		treeIntId = id(treeInterceptor)
		if treeIntId not in self._primedTreeInterceptors:
			self._primedTreeInterceptors.add(treeIntId)
			primeToken = object()
			self._primeToken = primeToken
			logHandler.log.debug(f"SiteMarker: starting buffer priming pass 1 for treeInt {treeIntId}")
			core.callLater(300, self._primeDocumentBuffer, treeInterceptor, primeToken, 0, 1)
		nextHandler()

	def _primeDocumentBuffer(self, treeInt, primeToken, count, passNum):
		if self._primeToken is not primeToken:
			return
		if count >= 60:
			logHandler.log.debug(f"SiteMarker: buffer priming pass {passNum} finished ({count} paragraphs)")
			if passNum == 1:
				core.callLater(1500, self._primeDocumentBuffer, treeInt, primeToken, 0, 2)
			return
		try:
			if count == 0:
				primeInfo = treeInt.makeTextInfo(textInfos.POSITION_FIRST)
			else:
				primeInfo = self._primeTextInfo
			if primeInfo is None:
				return
			primeInfo.collapse()
			primeInfo.expand(textInfos.UNIT_PARAGRAPH)
			try:
				primeInfo.getTextWithFields()
			except Exception:
				pass
			try:
				_ = primeInfo.focusableNVDAObjectAtStart
			except Exception:
				pass
			try:
				_ = primeInfo.NVDAObjectAtStart
			except Exception:
				pass
			try:
				_ = primeInfo.text
			except Exception:
				pass
			if primeInfo.move(textInfos.UNIT_PARAGRAPH, 1) == 0:
				self._primeTextInfo = None
				logHandler.log.debug(f"SiteMarker: buffer priming pass {passNum} reached end at paragraph {count}")
				if passNum == 1:
					core.callLater(1500, self._primeDocumentBuffer, treeInt, primeToken, 0, 2)
				return
			self._primeTextInfo = primeInfo
		except Exception:
			self._primeTextInfo = None
			return
		core.callLater(15, self._primeDocumentBuffer, treeInt, primeToken, count + 1, passNum)

	def event_virtualBufferUpdated(self, treeInterceptor, nextHandler):
		now = time.time()
		if now - self._lastVirtualBufferUpdate > 0.3:
			self.refreshActiveLayout(force=True)
			self._lastVirtualBufferUpdate = now
		if self._pendingRevealCallback and treeInterceptor == self._pendingRevealTreeInt:
			cb = self._pendingRevealCallback
			self._pendingRevealCallback = None
			self._pendingRevealTreeInt = None
			core.callLater(0, cb)
		if self._pendingJumpRevealCallback and treeInterceptor == self._pendingJumpRevealTreeInt:
			cb = self._pendingJumpRevealCallback
			self._pendingJumpRevealCallback = None
			self._pendingJumpRevealTreeInt = None
			core.callLater(0, cb)
		nextHandler()

	def script_handleSiteMarkerAction(self, gesture):
		if not self._isInBrowser():
			gesture.send()
			return

		currentTime = time.time()
		if currentTime - self.lastTapTime > TAP_THRESHOLD:
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

			if myTapCount == 1:
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

			elif myTapCount == 2:
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

			elif myTapCount >= 3:
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

		self._multiTapTimer = core.callLater(int(TAP_THRESHOLD * 1000), dispatchAction)

	script_handleSiteMarkerAction.__doc__ = _translate(
		"Single tap: Add/Edit Marker. Double tap: Site Manager. Triple tap: Marker Manager."
	)

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
				if name and name.strip(): return name.strip()
			return ""
		except Exception:
			return ""

	def _findMarkerInCurrentParagraph(self, siteConfig, webFocusObj):
		if not webFocusObj or not siteConfig: return None
		paragraphText = self._getCurrentParagraphText(webFocusObj)
		if not paragraphText: return None
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

	def _startDomCheck(self):
		if self._domTimerRunning: return
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