# __init__.py
# Copyright (C) 2026 Chai Chaimee
# Licensed under GNU General Public License. See COPYING.txt for details.

import os
import sys
import threading
import time
import re
import wx
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
from .markerEngine import MarkerEngine
from .gui import MarkerEditDialog, SiteManagerDialog, MarkerManagerDialog, AddSiteDialog
from . import browseModeGestures

addonHandler.initTranslation()

_translate = _

TAP_THRESHOLD = 0.4
VIEWPORT_SCAN_RANGE = 50
AUTO_CLICK_RETRY_DELAY = 500
MAX_CLICK_RETRIES = 2

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

		self._domTimerRunning = False
		self._domCheckInterval = 2000
		self._startDomCheck()

		browseModeGestures.registerGestures(self)

	def isInEditableContext(self):
		focusObj = api.getFocusObject()
		if not focusObj:
			return False
		return focusObj.role in (controlTypes.Role.EDITABLETEXT, controlTypes.Role.COMBOBOX)

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
		"""Reorders markers in self.activeSiteMarkers so they appear top‑down on the page."""
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
				if scanInfo.move(textInfos.UNIT_PARAGRAPH, 1) == 0:
					break
				scanInfo.expand(textInfos.UNIT_PARAGRAPH)
			if orderedIndices:
				remaining = [i for i in range(len(markers)) if i not in orderedIndices]
				orderedIndices.extend(remaining)
				self.activeSiteMarkers[key] = [markers[i] for i in orderedIndices]

	def _getViewportRange(self, treeInt):
		"""Returns (startParagraph, endParagraph) for a local search window around the caret."""
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
		textInfo = treeInt.makeTextInfo(textInfos.POSITION_CARET)
		textInfo.collapse()
		textInfo.expand(textInfos.UNIT_PARAGRAPH)

		if direction < 0:
			try:
				backupCaret = textInfo.copy()
				backupCaret.move(textInfos.UNIT_CHARACTER, -1)
				backupCaret.select()
			except Exception:
				pass

		skipPos = None
		lastInfo = self.lastJumpInfo.get(baseKey)
		if lastInfo and lastInfo.get('direction') == direction:
			skipPos = lastInfo.get('position')

		# Narrow scope if any marker requests viewport only
		useViewport = any(m.get("scope", "document") == "viewport" for m in markersForThisKey)
		vpStart, vpEnd = None, None
		if useViewport:
			vpStart, vpEnd = self._getViewportRange(treeInt)

		found = False
		iterationCount = 0
		maxIterations = 1000 if not useViewport else 200

		while iterationCount < maxIterations:
			iterationCount += 1
			moveResult = textInfo.move(textInfos.UNIT_PARAGRAPH, direction)
			if moveResult == 0:
				break
			textInfo.expand(textInfos.UNIT_PARAGRAPH)

			if useViewport and vpStart and vpEnd:
				# Stop if we've left the viewport window
				if direction > 0 and textInfo.compareEndPoints(vpEnd, "startToStart") > 0:
					break
				if direction < 0 and textInfo.compareEndPoints(vpStart, "startToStart") < 0:
					break

			if skipPos is not None and textInfo._startOffset == skipPos:
				continue

			marker, matchObj = self.engine.matchParagraph(textInfo, markersForThisKey)
			if not marker:
				continue

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

			def dispatchSpeechAndSelection(infoToSelect, dirVal):
				try:
					infoToSelect.updateCaret()
					speech.speakTextInfo(infoToSelect, reason=controlTypes.OutputReason.CARET)
					infoToSelect.collapse()
					if hasattr(treeInt, "selection"):
						try:
							treeInt._set_selection(infoToSelect)
						except AttributeError:
							pass
						treeInt.selection = infoToSelect

					self.lastJumpInfo[baseKey] = {
						'position': infoToSelect._startOffset,
						'direction': dirVal
					}
				except Exception as e:
					logHandler.log.error(f"Failed to update caret or speak: {e}")

			core.callLater(10, dispatchSpeechAndSelection, finalInfo.copy(), direction)
			found = True
			break

		if not found:
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

	def _executeAutoClick(self, marker, targetInfo, treeInt, gesture, keystroke, retryCount=0):
		"""Perform the actual click and schedule retry if needed."""
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

			speech.speakTextInfo(targetInfo, reason=controlTypes.OutputReason.CARET)
			ui.message(_translate("Clicked"))

			# Prepare retry state
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
							logHandler.log.debug(f"Auto-click retry {retryCount} for {keystroke}")
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

		allMarkers = self.activeSiteMarkers[keystroke]
		clickMarkers = [m for m in allMarkers if m.get("actionMode") == "autoClick"]
		if not clickMarkers:
			ui.message(_translate("No auto click marker for this key."))
			return

		treeInt = treeInterceptor
		# Use viewport scope if any marker requests it
		useViewport = any(m.get("scope", "document") == "viewport" for m in clickMarkers)
		if useViewport:
			vpStart, vpEnd = self._getViewportRange(treeInt)
			if not vpStart:
				ui.message(_translate("Cannot determine viewport."))
				return
			textInfo = vpStart.copy()
		else:
			textInfo = treeInt.makeTextInfo(textInfos.POSITION_ALL)
			textInfo.collapse()
			textInfo.expand(textInfos.UNIT_PARAGRAPH)

		found = False
		iterationCount = 0
		maxIterations = 2000 if not useViewport else 200

		while iterationCount < maxIterations:
			iterationCount += 1

			marker, matchObj = self.engine.matchParagraph(textInfo, clickMarkers)

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

				core.callLater(20, self._executeAutoClick, marker, finalInfo.copy(), treeInt, gesture, keystroke)
				found = True
				break

			moveResult = textInfo.move(textInfos.UNIT_PARAGRAPH, 1)
			if moveResult == 0:
				break
			textInfo.expand(textInfos.UNIT_PARAGRAPH)
			if useViewport and vpEnd and textInfo.compareEndPoints(vpEnd, "startToStart") > 0:
				break

		if not found:
			ui.message(_translate("No auto click target found."))

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
		if not focusObj: return None

		if hasattr(focusObj, "treeInterceptor") and focusObj.treeInterceptor is not None:
			return focusObj
		for child in focusObj.children:
			if hasattr(child, "treeInterceptor") and child.treeInterceptor is not None:
				return child
		return focusObj

	def getBrowserUrl(self):
		focusObj = self.getRealWebFocusObject()
		treeInt = getattr(focusObj, "treeInterceptor", None) if focusObj else None

		if not treeInt:
			currentObj = api.getFocusObject()
			treeInt = getattr(currentObj, "treeInterceptor", None)

		if treeInt and hasattr(treeInt, "documentConstantIdentifier"):
			urlStr = treeInt.documentConstantIdentifier
			if urlStr and (urlStr.startswith("http") or urlStr.startswith("https") or urlStr.startswith("file")):
				return urlStr

		if self.lastProcessedUrl: return self.lastProcessedUrl
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
		self.refreshActiveLayout(force=False)
		nextHandler()

	def event_treeInterceptor_gainFocus(self, treeInterceptor, nextHandler):
		self.refreshActiveLayout(force=False)
		nextHandler()

	def event_virtualBufferUpdated(self, treeInterceptor, nextHandler):
		now = time.time()
		if now - self._lastVirtualBufferUpdate > 0.3:
			self.refreshActiveLayout(force=True)
			self._lastVirtualBufferUpdate = now
		nextHandler()

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

	def script_handleSiteMarkerAction(self, gesture):
		currentTime = time.time()
		if currentTime - self.lastTapTime > TAP_THRESHOLD:
			self.tapCount = 0
		self.tapCount += 1
		self.lastTapTime = currentTime

		def dispatchAction():
			import gui as nvdaGui
			currentUrl = self.getBrowserUrl()

			if self.tapCount == 1:
				if not currentUrl:
					ui.message(_translate("Cannot capture browser URL."))
					self.tapCount = 0
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
								dlg = MarkerEditDialog(
									nvdaGui.mainFrame,
									existingMarker,
									initialText=paraText
								)
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
								dlg = MarkerManagerDialog(
									nvdaGui.mainFrame,
									self.engine,
									siteName,
									siteConfig,
									webFocusObj,
									autoAddMarker=True,
									currentUrl=currentUrl
								)
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

			elif self.tapCount == 2:
				if not currentUrl:
					ui.message(_translate("Cannot capture browser URL."))
					self.tapCount = 0
					return
				siteName, siteConfig = self.getCurrentSiteConfig()
				def openSiteDialog():
					dlg = None
					try:
						if siteName and siteConfig:
							dlg = SiteManagerDialog(nvdaGui.mainFrame, self.engine, currentUrl)
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

			self.tapCount = 0

		core.callLater(int(TAP_THRESHOLD * 1000), dispatchAction)

	script_handleSiteMarkerAction.__doc__ = _translate(
		"Single tap: Add/Edit Marker. Double tap: Site Manager."
	)

	def _startDomCheck(self):
		if self._domTimerRunning: return
		self._domTimerRunning = True
		core.callLater(self._domCheckInterval, self._doDomCheck)

	def _stopDomCheck(self):
		self._domTimerRunning = False

	def _doDomCheck(self):
		if not self._domTimerRunning: return
		try:
			self.refreshActiveLayout(force=False)
		except Exception as e:
			logHandler.log.debug(f"DOM check refresh failed: {e}")
		core.callLater(self._domCheckInterval, self._doDomCheck)

	def terminate(self):
		self._stopDomCheck()
		self.activeSiteMarkers.clear()
		self.lastProcessedUrl = None
		self.lastJumpInfo.clear()
		self._autoClickRetryState = None
		if hasattr(self.engine, "cleanUp"):
			self.engine.cleanUp()