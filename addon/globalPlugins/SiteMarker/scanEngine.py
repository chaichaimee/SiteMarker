# scanEngine.py

import time
import api
import ui
import core
import speech
import logHandler
import controlTypes
import textInfos
import browseMode
from comtypes import COMError
_translate = _
import ctypes
from .constants import (
	AUTO_CLICK_RETRY_DELAY,
	LOAD_MORE_TIMEOUT_MS,
	MAX_CLICK_RETRIES,
	MAX_COMBOBOX_RESUME_ATTEMPTS,
	MAX_LOAD_MORE_ATTEMPTS,
	MAX_NO_GROWTH_ATTEMPTS,
	MAX_SCAN_PARAGRAPHS_TOTAL,
	MAX_SORT_ITERATIONS,
	PAGE_DOWN_BATCH_SIZE,
	SCAN_PARAGRAPHS_PER_CHUNK,
	SORT_PARAGRAPHS_PER_CHUNK,
)


class ScanEngineMixin:
	def _verifyThenDispatchJump(self, infoToSelect, dirVal, skipOff, treeInt, baseKey,
							   markersForThisKey, oldSelection, vpStart, vpEnd, useViewport):
		if not self._isTreeInterceptorStillFocused(treeInt):
			logHandler.log.debug(f"SiteMarker: aborting jump dispatch for key '{baseKey}', tab/document changed.")
			return
		self._recentScanActivity[id(treeInt)] = time.time()
		# IMPORTANT: check combobox-likeness BEFORE calling updateCaret()/speak/select.
		# updateCaret() moves the real browse-mode caret to this position; if the
		# position overlaps a real focusable Chrome element (e.g. the "Search
		# Facebook" combobox), Chrome pulls actual keyboard focus into that field
		# as a side effect of the virtual cursor landing on it. Once real focus is
		# inside that field, any COM property access made afterwards (such as the
		# combobox check itself) can hang indefinitely instead of raising, which is
		# what produced the silent freeze stuck in the search box.
		isCombobox = self._isLikelyComboboxTarget(infoToSelect)
		logHandler.log.debug(f"SiteMarker: comboboxCheck result={isCombobox} for baseKey='{baseKey}'")
		if isCombobox:
			resumeCount = self._jumpComboboxResumeCount.get(baseKey, 0) + 1
			self._jumpComboboxResumeCount[baseKey] = resumeCount
			if resumeCount > MAX_COMBOBOX_RESUME_ATTEMPTS:
				logHandler.log.debug(
					f"SiteMarker: giving up on key '{baseKey}' after {resumeCount} consecutive "
					f"combobox-like matches; stopping instead of resuming silently."
				)
				self._jumpComboboxResumeCount[baseKey] = 0
				self._finishJumpNotFound(dirVal, treeInt)
				return
			logHandler.log.debug(
				f"SiteMarker: resuming scan past combobox-like match for baseKey='{baseKey}' (attempt {resumeCount})"
			)
			resumeInfo = infoToSelect.copy()
			newScanToken = object()
			self._jumpScanToken[baseKey] = newScanToken
			self._processJumpChunk(treeInt, markersForThisKey, baseKey, dirVal,
								   resumeInfo, oldSelection, skipOff, vpStart, vpEnd, useViewport,
								   newScanToken, 0, 0)
			return
		self._jumpComboboxResumeCount[baseKey] = 0
		try:
			infoToSelect.updateCaret()
			speech.speakTextInfo(infoToSelect, reason=controlTypes.OutputReason.CARET)
		except Exception as e:
			logHandler.log.error(f"Failed to update caret or speak: {e}")
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
	def _sortMarkersByDocumentOrder(self, treeInterceptor):
		if not treeInterceptor:
			return
		keysToSort = [key for key, markers in self.activeSiteMarkers.items() if len(markers) > 1]
		if not keysToSort:
			return
		sortToken = object()
		self._sortScanToken = sortToken
		self._continueSortMarkersChunk(treeInterceptor, keysToSort, 0, None, [], sortToken, 0)
	def _continueSortMarkersChunk(self, treeInterceptor, keysToSort, keyIndex, scanInfo,
								  orderedIndices, sortToken, iteration):
		if self._sortScanToken is not sortToken:
			return
		if not self._isTreeInterceptorStillFocused(treeInterceptor):
			return
		if keyIndex >= len(keysToSort):
			return
		key = keysToSort[keyIndex]
		markers = self.activeSiteMarkers.get(key)
		if not markers or len(markers) <= 1:
			core.callLater(0, self._continueSortMarkersChunk,
						   treeInterceptor, keysToSort, keyIndex + 1, None, [], sortToken, 0)
			return
		if scanInfo is None:
			try:
				scanInfo = treeInterceptor.makeTextInfo(textInfos.POSITION_FIRST)
			except Exception as e:
				logHandler.log.debug(f"Marker sort scan init failed: {e}")
				core.callLater(0, self._continueSortMarkersChunk,
							   treeInterceptor, keysToSort, keyIndex + 1, None, [], sortToken, 0)
				return
		processed = 0
		reachedEnd = False
		while processed < SORT_PARAGRAPHS_PER_CHUNK and iteration < MAX_SORT_ITERATIONS:
			processed += 1
			iteration += 1
			try:
				foundMarker, _ = self.engine.matchParagraph(scanInfo, markers)
				if foundMarker is not None:
					try:
						foundIdx = markers.index(foundMarker)
					except ValueError:
						foundIdx = None
					if foundIdx is not None and foundIdx not in orderedIndices:
						orderedIndices.append(foundIdx)
				if scanInfo.move(textInfos.UNIT_PARAGRAPH, 1) == 0:
					reachedEnd = True
					break
				scanInfo.expand(textInfos.UNIT_PARAGRAPH)
			except (COMError, RuntimeError, OSError) as e:
				logHandler.log.debug(f"Marker sort scan paragraph error: {e}")
				reachedEnd = True
				break
		if not reachedEnd and iteration < MAX_SORT_ITERATIONS:
			core.callLater(5, self._continueSortMarkersChunk,
						   treeInterceptor, keysToSort, keyIndex, scanInfo, orderedIndices, sortToken, iteration)
			return
		if orderedIndices:
			remaining = [i for i in range(len(markers)) if i not in orderedIndices]
			orderedIndices.extend(remaining)
			self.activeSiteMarkers[key] = [markers[i] for i in orderedIndices]
		core.callLater(0, self._continueSortMarkersChunk,
					   treeInterceptor, keysToSort, keyIndex + 1, None, [], sortToken, 0)
	def _handleGlobalJump(self, gesture, baseKey, direction):
		focusObj = api.getFocusObject()
		treeInterceptor = getattr(focusObj, "treeInterceptor", None) if focusObj else None
		if not treeInterceptor or not isinstance(treeInterceptor, browseMode.BrowseModeTreeInterceptor) or treeInterceptor.passThrough:
			if gesture: gesture.send()
			return
		if not self.getBrowserUrl():
			if gesture: gesture.send()
			return
		self._recentScanActivity[id(treeInterceptor)] = time.time()
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
			lastPosition = lastInfo.get('position')
			try:
				currentCaretOffset = oldSelection._startOffset
			except Exception:
				currentCaretOffset = None
			if lastPosition is not None and currentCaretOffset is not None and lastPosition == currentCaretOffset:
				skipPos = lastInfo.get('skipOffset')
			else:
				# Caret moved away from where the previous jump left it (manual
				# navigation happened in between) - the old skip position no
				# longer describes "the paragraph we just came from" and must
				# not be applied to this fresh scan.
				self.lastJumpInfo.pop(baseKey, None)
		scanToken = object()
		self._jumpScanToken[baseKey] = scanToken
		self._jumpComboboxResumeCount[baseKey] = 0
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
		if not self._isTreeInterceptorStillFocused(treeInt):
			logHandler.log.debug(f"SiteMarker: aborting jump scan for key '{baseKey}', tab/document changed.")
			return
		self._recentScanActivity[id(treeInt)] = time.time()
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
				if processed % 15 == 0:
					self._scrollRealObjectIntoView(textInfo)
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
				# Only pay for the ancestor-walk editable/combobox check once we
				# actually have a candidate match, instead of on every scanned
				# paragraph.
				if self._isParagraphEditable(textInfo):
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
					offsetDir = 1 if offsetVal > 0 else -1
					for _ in range(abs(offsetVal)):
						moveResult = offsetInfo.move(textInfos.UNIT_PARAGRAPH, offsetDir)
						offsetInfo.expand(textInfos.UNIT_PARAGRAPH)
						if moveResult == 0:
							break
					# Defensive re-settle: collapse to the true start and expand
					# once more so the final boundary is computed fresh from a
					# clean point, rather than trusting whatever boundary state
					# accumulated across the move+expand steps above.
					offsetInfo.collapse()
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
				if comErrorStreak >= 6 or self._isStaleBufferError(e):
					logHandler.log.debug(
						f"Jump scan aborted, buffer likely changed under us (page re-rendered): {e}"
					)
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
		if not reachedEnd:
			core.callLater(5, self._processJumpChunk,
						   treeInt, markersForThisKey, baseKey, direction,
						   textInfo, oldSelection, skipPos, vpStart, vpEnd, useViewport,
						   scanToken, loadMoreAttempts, noGrowthCount)
			return
		if loadMoreAttempts >= MAX_LOAD_MORE_ATTEMPTS or noGrowthCount >= MAX_NO_GROWTH_ATTEMPTS:
			logHandler.log.debug(
				f"SiteMarker: no next marker for key '{baseKey}' after {loadMoreAttempts} load-more "
				f"attempts ({noGrowthCount} without growth); giving up."
			)
			self._finishJumpNotFound(direction, treeInt)
			return
		if not useViewport:
			logHandler.log.debug(
				f"SiteMarker: reached end of loaded content for key '{baseKey}', "
				f"requesting more (attempt {loadMoreAttempts + 1})."
			)
			resumePoint = textInfo.copy()
			def afterScroll():
				self._processJumpChunk(
					treeInt, markersForThisKey, baseKey, direction,
					resumePoint, oldSelection, skipPos, vpStart, vpEnd, useViewport,
					scanToken, loadMoreAttempts + 1, noGrowthCount + 1
				)
			self._scrollAndWaitForUpdate(treeInt, afterScroll, scanToken)
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
	def _startAutoClickSearch(self, treeInt, clickMarkers, gesture, keystroke, expectedUrl=None, swapAttempts=0):
		if expectedUrl is None:
			expectedUrl = self.getBrowserUrl()
		scanToken = object()
		self._autoClickScanToken[keystroke] = scanToken
		self._autoClickComboboxResumeCount[keystroke] = 0
		try:
			textInfo = treeInt.makeTextInfo(textInfos.POSITION_FIRST)
			textInfo.collapse()
			textInfo.expand(textInfos.UNIT_PARAGRAPH)
		except Exception as e:
			logHandler.log.debug(f"AutoClick search init failed: {e}")
			return
		self._processAutoClickChunk(treeInt, clickMarkers, gesture, keystroke, textInfo, scanToken, 0, 0,
									expectedUrl, swapAttempts)
	def _processAutoClickChunk(self, treeInt, clickMarkers, gesture, keystroke,
							   textInfo, scanToken, loadMoreAttempts, noGrowthCount,
							   expectedUrl=None, swapAttempts=0):
		if self._autoClickScanToken.get(keystroke) is not scanToken:
			return
		if not self._isTreeInterceptorStillFocused(treeInt):
			if swapAttempts < 3:
				freshTreeInt = self._tryRecoverFreshTreeInterceptor(expectedUrl)
				if freshTreeInt is not None:
					logHandler.log.debug(
						f"SiteMarker: auto click document swapped under us (page still settling), "
						f"restarting scan on the fresh document for key '{keystroke}' (attempt {swapAttempts + 1})."
					)
					self._startAutoClickSearch(freshTreeInt, clickMarkers, gesture, keystroke,
											   expectedUrl, swapAttempts + 1)
					return
			logHandler.log.debug(f"SiteMarker: aborting auto click scan for key '{keystroke}', tab/document changed.")
			return
		self._recentScanActivity[id(treeInt)] = time.time()
		processed = 0
		reachedEnd = False
		comErrorStreak = 0
		totalScanned = loadMoreAttempts * SCAN_PARAGRAPHS_PER_CHUNK + processed
		while processed < SCAN_PARAGRAPHS_PER_CHUNK and totalScanned < MAX_SCAN_PARAGRAPHS_TOTAL:
			processed += 1
			totalScanned += 1
			try:
				marker, _ = self.engine.matchParagraph(textInfo, clickMarkers)
				# Only pay for the ancestor-walk editable/combobox check once we
				# actually have a candidate match, instead of on every scanned
				# paragraph.
				if marker and self._isParagraphEditable(textInfo):
					marker = None
				if marker:
					offsetVal = marker.get("offset", 0)
					if offsetVal != 0:
						offsetInfo = textInfo.copy()
						offsetDir = 1 if offsetVal > 0 else -1
						for _ in range(abs(offsetVal)):
							moveResult = offsetInfo.move(textInfos.UNIT_PARAGRAPH, offsetDir)
							offsetInfo.expand(textInfos.UNIT_PARAGRAPH)
							if moveResult == 0:
								break
						offsetInfo.collapse()
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
						if not self._isTreeInterceptorStillFocused(treeInt):
							logHandler.log.debug(f"SiteMarker: aborting auto click dispatch for key '{keystroke}', tab/document changed.")
							return
						# IMPORTANT: check combobox-likeness BEFORE calling updateCaret() at all.
						# updateCaret() moves the real browse-mode caret to this position; if it
						# overlaps a real focusable Chrome element (e.g. a search combobox),
						# Chrome pulls actual keyboard focus into that field as a side effect,
						# and any COM property access made afterwards can hang indefinitely
						# instead of raising. See the matching note in _verifyThenDispatchJump.
						if self._isLikelyComboboxTarget(infoVal):
							resumeCount = self._autoClickComboboxResumeCount.get(keystroke, 0) + 1
							self._autoClickComboboxResumeCount[keystroke] = resumeCount
							if resumeCount > MAX_COMBOBOX_RESUME_ATTEMPTS:
								logHandler.log.debug(
									f"SiteMarker: giving up on auto click key '{keystroke}' after {resumeCount} "
									f"consecutive combobox-like matches; stopping instead of resuming silently."
								)
								self._autoClickComboboxResumeCount[keystroke] = 0
								ui.message(_translate("No auto click target found."))
								return
							logHandler.log.debug(
								f"SiteMarker: resuming auto click scan past combobox-like match for "
								f"keystroke='{keystroke}' (attempt {resumeCount})"
							)
							resumeInfo = infoVal.copy()
							newScanToken = object()
							self._autoClickScanToken[keystroke] = newScanToken
							self._processAutoClickChunk(treeInt, clickMarkers, gesture, keystroke,
														resumeInfo, newScanToken, 0, 0, expectedUrl, swapAttempts)
							return
						self._autoClickComboboxResumeCount[keystroke] = 0
						self._executeAutoClick(markerVal, infoVal, treeInt, gesture, keystroke, skipSpeak=False)
					core.callLater(0, attemptAutoClick, marker, finalInfo.copy())
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
				if comErrorStreak >= 6 or self._isStaleBufferError(e):
					logHandler.log.debug(
						f"AutoClick scan aborted, buffer likely changed under us (page re-rendered): {e}"
					)
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
			core.callLater(0, self._processAutoClickChunk, treeInt, clickMarkers, gesture, keystroke,
						   textInfo, scanToken, loadMoreAttempts, noGrowthCount, expectedUrl, swapAttempts)
			return
		if loadMoreAttempts >= MAX_LOAD_MORE_ATTEMPTS or noGrowthCount >= MAX_NO_GROWTH_ATTEMPTS:
			ui.message(_translate("No auto click target found."))
			return
		resumePoint = textInfo.copy()
		def afterScroll():
			self._processAutoClickChunk(treeInt, clickMarkers, gesture, keystroke,
										resumePoint, scanToken, loadMoreAttempts + 1, noGrowthCount + 1,
										expectedUrl, swapAttempts)
		self._scrollAndWaitForUpdate(treeInt, afterScroll, scanToken)
	def _executeAutoClick(self, marker, targetInfo, treeInt, gesture, keystroke, retryCount=0, skipSpeak=False):
		if not self._isTreeInterceptorStillFocused(treeInt):
			logHandler.log.debug(f"SiteMarker: aborting auto click execution for key '{keystroke}', tab/document changed.")
			return
		try:
			targetInfo.updateCaret()
			if hasattr(treeInt, "selection"):
				try:
					treeInt._set_selection(targetInfo)
				except AttributeError:
					pass
				treeInt.selection = targetInfo
			if hasattr(treeInt, "activatePosition"):
				try:
					treeInt.activatePosition(targetInfo)
				except Exception as e:
					logHandler.log.debug(f"Native activation failed: {e}")
					focusable = targetInfo.focusableNVDAObjectAtStart
					if focusable:
						focusable.doAction()
			else:
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
		except Exception as e:
			logHandler.log.error(f"Retry check init failed: {e}")
			return
		self._continueAutoClickRetryCheck(state, treeInt, currentInfo, 0)

	def _continueAutoClickRetryCheck(self, state, treeInt, currentInfo, iteration):
		if self._autoClickRetryState is not state:
			return
		processed = 0
		while processed < SCAN_PARAGRAPHS_PER_CHUNK and iteration < MAX_SCAN_PARAGRAPHS_TOTAL:
			processed += 1
			iteration += 1
			try:
				if currentInfo._startOffset == state["targetInfoStartOffset"]:
					if currentInfo.text.strip() == state["textBefore"]:
						retryCount = state["retryCount"] + 1
						if retryCount <= MAX_CLICK_RETRIES:
							self._autoClickRetryState = None
							self._executeAutoClick(
								state["marker"], currentInfo.copy(), treeInt,
								state["gesture"], state["keystroke"], retryCount
							)
					return
				if currentInfo.move(textInfos.UNIT_PARAGRAPH, 1) == 0:
					return
				currentInfo.expand(textInfos.UNIT_PARAGRAPH)
			except Exception as e:
				logHandler.log.error(f"Retry check failed: {e}")
				return
		core.callLater(0, self._continueAutoClickRetryCheck, state, treeInt, currentInfo, iteration)
		self._autoClickRetryState = None
	def _scrollRealObjectIntoView(self, textInfo):
		try:
			realObj = textInfo.NVDAObjectAtStart
		except Exception:
			return
		if not realObj:
			return
		try:
			looksEditable = self._objectLooksEditableOrCombobox(realObj)
		except Exception:
			return
		if looksEditable:
			return
		# _objectLooksEditableOrCombobox already swallows role/state read errors and
		# reports "not editable" in that case, which is the wrong default here: a
		# role query that fails (COMError etc.) usually means the object is mid
		# focus-transition, and calling scrollIntoView() on it can drag real Chrome
		# keyboard focus into it (this is what was landing the caret inside the
		# "Search Facebook" combobox during scanning, before any marker match was
		# even confirmed). Do one more direct role probe; if it's not cleanly
		# readable, skip scrolling rather than risk it.
		try:
			_ = realObj.role
		except Exception as e:
			logHandler.log.debug(f"Skipping scrollIntoView, role unreadable: {e}")
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
	def _scrollAndWaitForUpdate(self, treeInt, callback, scanToken):
		self._sendPageDownKeystroke(PAGE_DOWN_BATCH_SIZE)
		self._pendingReveals[scanToken] = (callback, treeInt)
		core.callLater(LOAD_MORE_TIMEOUT_MS, self._revealTimeout, scanToken)
	def _revealTimeout(self, scanToken):
		entry = self._pendingReveals.pop(scanToken, None)
		if entry:
			callback, treeInt = entry
			callback()
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
