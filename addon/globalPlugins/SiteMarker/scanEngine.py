# scanEngine.py

import time
import re
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
	MAX_FULL_SCAN_RETRIES,
	PRIME_DOCUMENT_MAX_PARAGRAPHS,
	PRIME_DOCUMENT_STEP_DELAY_MS,
	BUFFER_READY_MIN_LENGTH,
	AUTO_TRIGGER_RETRY_DELAY_MS,
	AUTO_TRIGGER_MAX_RETRIES,
)

# Per-attempt delay before re-scanning the document for an auto-click target that
# was not found yet (e.g. an ad skip button that has not rendered into the
# accessibility tree). Front-loaded so a target that appears a moment late is still
# found from a single keypress, instead of requiring the user to press again.
FULL_SCAN_RETRY_DELAY_SCHEDULE_MS = (300, 500, 900, 1500, 1500)

# If a synthetic load-more scroll lands real focus on an editable/combobox control
# this many times in a row (even after recovering each time), stop sending further
# scroll keystrokes for the rest of this scan rather than repeating the same theft.
# Deliberately 1, not higher: on pages where this happens at all (e.g. Facebook
# Messenger's own chat panel, where Page Down doesn't scroll the panel and instead
# re-focuses its search box every time), recovery itself reliably succeeds but a
# retry never fixes the underlying cause -- the same theft just repeats on the next
# attempt too. Waiting for more confirmations before giving up only adds further
# noisy combobox announcements for the user without changing the eventual outcome.
EDITABLE_THEFT_ABORT_THRESHOLD = 1

# Default search window (in lines, matching arrow-key granularity) for a
# pattern-anchored offset when the marker's offset value is 0 (no explicit
# window given).
DEFAULT_OFFSET_PATTERN_SEARCH_LINES = 30


class ScanEngineMixin:

	# ------------------------------------------------------------------
	# Buffer readiness check
	# ------------------------------------------------------------------
	def _isBufferReady(self, treeInt):
		try:
			info = treeInt.makeTextInfo(textInfos.POSITION_ALL)
			currentLength = len(info.text) if info and info.text else 0
		except Exception:
			return False
		if currentLength == 0:
			return False
		if currentLength > BUFFER_READY_MIN_LENGTH:
			# Comfortably past the length a still-rendering document would show
			# this early; no need to wait for a second poll.
			self._bufferReadySamples.pop(id(treeInt), None)
			return True
		# The document never reached BUFFER_READY_MIN_LENGTH at all -- this is
		# normal for a short, genuinely sparse page (a minimal search-engine
		# homepage, for example), which the old fixed-length-only check treated
		# as permanently "not ready" and reported as a load failure even once
		# fully rendered. Fall back to a two-poll stability check instead: once
		# the rendered text length stops changing between polls (each call here
		# is already spaced LOAD_MORE_TIMEOUT_MS/the retry delay apart by the
		# caller), the page has settled even though it's short.
		treeIntId = id(treeInt)
		previousLength = self._bufferReadySamples.get(treeIntId)
		self._bufferReadySamples[treeIntId] = currentLength
		if previousLength is not None and previousLength == currentLength:
			self._bufferReadySamples.pop(treeIntId, None)
			return True
		return False

	# ------------------------------------------------------------------
	# Auto-trigger on page load (Marker Properties' "Auto-jump/Auto-click on
	# page load" checkbox)
	# ------------------------------------------------------------------
	def _findAutoTriggerMarker(self):
		for baseKey, markersForKey in self.activeSiteMarkers.items():
			for marker in markersForKey:
				if marker.get("autoTrigger"):
					return marker, baseKey
		return None, None

	def _checkAutoTriggerMarker(self, treeInt, retryCount=0):
		# Scheduled exactly once per fresh treeInterceptor by
		# event_treeInterceptor_gainFocus (guarded there by
		# _primedTreeInterceptors, the same one-per-page-load guard used for
		# buffer priming), so no separate dedup state is needed here -- this
		# retry chain is the single attempt for this page load, not a new
		# one each call.
		if not self._isTreeInterceptorStillFocused(treeInt):
			logHandler.log.debug(
				f"SiteMarker: auto-trigger check for treeInt {id(treeInt)} aborted "
				f"at retry {retryCount} -- no longer the focused document."
			)
			return
		bufferReady = self._isBufferReady(treeInt)
		if not bufferReady and retryCount < AUTO_TRIGGER_MAX_RETRIES:
			core.callLater(
				AUTO_TRIGGER_RETRY_DELAY_MS, self._checkAutoTriggerMarker, treeInt, retryCount + 1
			)
			return
		if not bufferReady:
			# Retry budget exhausted without a confirmed-ready buffer. Rather
			# than give up silently -- which, from the user's side, is
			# indistinguishable from the feature having done nothing at all
			# -- still attempt the trigger on a best-effort basis: a site
			# with a configured auto-trigger marker is exactly the case the
			# user opted into, and an attempt against a not-yet-fully-loaded
			# page fails safely (the marker scan just won't find its target
			# yet) rather than dangerously, so trying is strictly better than
			# guaranteed silence.
			logHandler.log.debug(
				f"SiteMarker: auto-trigger buffer-ready wait exhausted after "
				f"{retryCount} retries for treeInt {id(treeInt)}; attempting anyway."
			)
			self._bufferReadySamples.pop(id(treeInt), None)
		self.refreshActiveLayout(force=False)
		marker, baseKey = self._findAutoTriggerMarker()
		if marker is None:
			logHandler.log.debug(
				f"SiteMarker: auto-trigger check for treeInt {id(treeInt)} found no marker "
				f"with autoTrigger set among {len(self.activeSiteMarkers)} active key(s)."
			)
			return
		logHandler.log.debug(
			f"SiteMarker: auto-trigger firing for baseKey='{baseKey}' "
			f"actionMode='{marker.get('actionMode')}' after {retryCount} retries "
			f"(bufferReady={bufferReady})."
		)
		# No gesture object exists for an automatic trigger -- passing None is
		# safe here since _handleGlobalJump/_handleAutoClick only ever call
		# gesture.send() behind an "if gesture:" guard, and a match is already
		# confirmed to exist for this exact baseKey above, so none of the
		# no-markers-configured fallback paths that would need a real gesture
		# can be reached.
		if marker.get("actionMode") == "autoClick":
			self._handleAutoClick(None, baseKey)
		else:
			self._handleGlobalJump(None, baseKey, 1)

	# ------------------------------------------------------------------
	# Jump scanning
	# ------------------------------------------------------------------
	def _handleGlobalJump(self, gesture, baseKey, direction, _retryCount=0):
		focusObj = api.getFocusObject()
		treeInterceptor = getattr(focusObj, "treeInterceptor", None) if focusObj else None
		if not treeInterceptor or not isinstance(treeInterceptor, browseMode.BrowseModeTreeInterceptor) or treeInterceptor.passThrough:
			if gesture: gesture.send()
			return
		expectedUrl = self.getBrowserUrl()
		if not expectedUrl:
			if gesture: gesture.send()
			return

		# Resolve whether this site has any marker configured for this key
		# before anything that depends on the document buffer being fully
		# loaded. F/Shift+F, D/Shift+D and Z/Shift+Z double as NVDA's own
		# browse-mode single-letter navigation keys (form field, ARIA landmark,
		# and this add-on's own third binding); a site the user hasn't
		# configured any marker for on this key must never have its native
		# NVDA behaviour swallowed. Checking this first also means a site with
		# no SiteMarker configuration at all never has to wait through the
		# buffer-readiness retry loop below -- previously every press on such
		# a site paid that cost, and a short, genuinely sparse page could pay
		# it every time (see _isBufferReady) and end by wrongly reporting the
		# page as not fully loaded when it simply had no markers to find.
		self.refreshActiveLayout(force=False)
		if not self.activeSiteMarkers or baseKey not in self.activeSiteMarkers:
			# The cached marker set for this URL may simply be stale (e.g. the user
			# just added or edited this exact marker via the GUI without navigating
			# away, so refreshActiveLayout's force=False caching guard above skipped
			# re-fetching). One forced re-fetch here catches that case, so a
			# just-saved marker works on the very next press instead of requiring a
			# dialog to be opened first (which was the only thing that previously
			# forced a refresh).
			self.refreshActiveLayout(force=True)
		if not self.activeSiteMarkers or baseKey not in self.activeSiteMarkers:
			if gesture:
				gesture.send()
			elif direction == 1:
				ui.message(_translate("No next marker."))
			else:
				ui.message(_translate("No previous marker."))
			return

		if not self._isBufferReady(treeInterceptor):
			if _retryCount < 5:
				logHandler.log.debug(
					f"SiteMarker: jump buffer not ready for key '{baseKey}', retry {_retryCount+1}/5"
				)
				core.callLater(400, self._handleGlobalJump, gesture, baseKey, direction, _retryCount + 1)
			else:
				self._bufferReadySamples.pop(id(treeInterceptor), None)
				ui.message(_("Page not fully loaded, try again."))
			return

		self._recentScanActivity[id(treeInterceptor)] = time.time()
		markersForThisKey = self.activeSiteMarkers[baseKey]
		treeInt = treeInterceptor
		oldSelection = treeInt.selection.copy() if hasattr(treeInt, "selection") else treeInt.makeTextInfo(textInfos.POSITION_CARET)
		# This is a brand new key press, not a continuation chunk of an
		# already-in-progress scan, so check whether the caret has moved away
		# from where the last dispatch for this key left it (e.g. the user
		# scrolled back up with the arrow keys). If so, the remembered
		# last-dispatched-text guard in _processJumpChunk is stale and must be
		# dropped, or a deliberate return trip back to that same marker gets
		# wrongly treated as a duplicate-node repeat and skipped over.
		self._dropStaleLastDispatchedTarget(baseKey, oldSelection)
		useViewport = any(m.get("scope", "document") == "viewport" for m in markersForThisKey)
		vpStart, vpEnd = None, None
		if useViewport:
			vpStart, vpEnd = self._getViewportRange(treeInt)

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
			textInfo, oldSelection, vpStart, vpEnd, useViewport,
			scanToken, 0, 0, expectedUrl, 0
		)

	def _applyOffset(self, textInfo, offsetVal, offsetPattern=""):
		"""
		Apply a line-based offset to the given textInfo and return the resulting info,
		expanded to the full paragraph it lands in. Ensures the final paragraph is
		non-empty for reliable landing.

		Offset is counted in UNIT_LINE steps, not UNIT_PARAGRAPH. This matches what
		upArrow/downArrow actually move by in browse mode, which is what a person
		calibrating an offset value experiences and counts against -- an offset
		measured in paragraphs instead disagreed with that, and reliably landed
		short of or past the intended target, since a "paragraph" as NVDA's text
		unit reports it does not correspond 1:1 with a single arrow-key press on a
		typical page.

		If offsetPattern is set, this switches from counting a fixed number of
		lines to searching for the nearest paragraph whose text matches
		offsetPattern (a regex), within a bounded window of abs(offsetVal) lines
		(or a default window if offsetVal is 0). A fixed offset count is
		fundamentally unreliable on pages where different items (e.g. posts in a
		social media feed) have differing internal structure between the matched
		anchor and the intended target -- the same offset lands somewhere
		different depending on how much incidental content a given post happens
		to contain. Searching for a distinguishing pattern instead is stable
		regardless of that structural variance. Falls back to the plain
		line-count behaviour if no match is found within the window, so an
		offsetPattern that doesn't match anything nearby fails safe instead of
		silently landing somewhere unrelated.
		"""
		if offsetPattern:
			direction = 1 if offsetVal >= 0 else -1
			maxSearch = abs(offsetVal) if offsetVal != 0 else DEFAULT_OFFSET_PATTERN_SEARCH_LINES
			try:
				compiledPattern = re.compile(offsetPattern)
			except re.error as e:
				logHandler.log.debug(f"SiteMarker: invalid offsetPattern '{offsetPattern}': {e}")
				compiledPattern = None
			if compiledPattern is not None:
				searchInfo = textInfo.copy()
				stepsTaken = 0
				while stepsTaken < maxSearch:
					if searchInfo.move(textInfos.UNIT_LINE, direction) == 0:
						break
					stepsTaken += 1
					checkInfo = searchInfo.copy()
					checkInfo.expand(textInfos.UNIT_PARAGRAPH)
					try:
						candidateText = checkInfo.text
					except Exception:
						continue
					if candidateText and compiledPattern.search(candidateText):
						return checkInfo.copy()
				logHandler.log.debug(
					f"SiteMarker: offsetPattern '{offsetPattern}' found no match within "
					f"{maxSearch} lines; falling back to plain offset count."
				)
		if offsetVal == 0:
			return textInfo.copy()
		offsetInfo = textInfo.copy()
		direction = 1 if offsetVal > 0 else -1
		remaining = abs(offsetVal)
		while remaining > 0:
			moved = offsetInfo.move(textInfos.UNIT_LINE, direction)
			if moved == 0:
				break
			remaining -= 1
		offsetInfo.expand(textInfos.UNIT_PARAGRAPH)
		# After reaching target offset, if paragraph is empty, step further to next non-empty
		safety = 0
		while safety < 10:
			try:
				if offsetInfo.text and offsetInfo.text.strip():
					break
			except Exception:
				pass
			# Empty paragraph, move one more step without counting offset
			if offsetInfo.move(textInfos.UNIT_LINE, direction) == 0:
				break
			offsetInfo.expand(textInfos.UNIT_PARAGRAPH)
			safety += 1
		return offsetInfo.copy()

	def _restartJumpScanOnFreshTreeInterceptor(self, treeInt, markersForThisKey, baseKey, direction,
												oldSelection, useViewport, expectedUrl=None, swapAttempts=0):
		if expectedUrl is None:
			expectedUrl = self.getBrowserUrl()
		scanToken = object()
		self._jumpScanToken[baseKey] = scanToken
		self._jumpComboboxResumeCount[baseKey] = 0
		vpStart, vpEnd = None, None
		if useViewport:
			vpStart, vpEnd = self._getViewportRange(treeInt)
		try:
			textInfo = treeInt.makeTextInfo(textInfos.POSITION_CARET)
			textInfo.collapse()
			textInfo.expand(textInfos.UNIT_PARAGRAPH)
		except Exception as e:
			logHandler.log.debug(f"SiteMarker: jump scan restart init failed: {e}")
			return
		self._processJumpChunk(
			treeInt, markersForThisKey, baseKey, direction,
			textInfo, oldSelection, vpStart, vpEnd, useViewport,
			scanToken, 0, 0, expectedUrl, swapAttempts
		)

	def _processJumpChunk(self, treeInt, markersForThisKey, baseKey, direction,
						  textInfo, oldSelection, vpStart, vpEnd, useViewport,
						  scanToken, loadMoreAttempts, noGrowthCount, expectedUrl=None, swapAttempts=0):
		if self._jumpScanToken.get(baseKey) is not scanToken:
			return
		if not self._isTreeInterceptorStillFocused(treeInt):
			if swapAttempts < 3:
				freshTreeInt = self._tryRecoverFreshTreeInterceptor(expectedUrl)
				if freshTreeInt is not None:
					logHandler.log.debug(
						f"SiteMarker: jump document swapped under us (page still settling), "
						f"restarting scan on the fresh document for key '{baseKey}' (attempt {swapAttempts + 1})."
					)
					self._restartJumpScanOnFreshTreeInterceptor(
						freshTreeInt, markersForThisKey, baseKey, direction,
						oldSelection, useViewport, expectedUrl, swapAttempts + 1
					)
					return
			logHandler.log.debug(f"SiteMarker: aborting jump scan for key '{baseKey}', tab/document changed.")
			return
		self._recentScanActivity[id(treeInt)] = time.time()
		if loadMoreAttempts == 0:
			# This is a brand new key press starting a fresh scan (not a continuation
			# chunk of an already-in-progress one), so any editable-focus-theft streak
			# left over from a previous, unrelated press of this key no longer applies.
			self._resetEditableTheftStreak()
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
				if totalScanned % 20 == 0:
					self._scrollRealObjectIntoView(textInfo)
				if useViewport and vpStart and vpEnd:
					if direction > 0 and textInfo.compareEndPoints(vpEnd, "startToStart") > 0:
						reachedEnd = True
						break
					if direction < 0 and textInfo.compareEndPoints(vpStart, "startToStart") < 0:
						reachedEnd = True
						break
				marker, matchObj = self.engine.matchParagraph(textInfo, markersForThisKey)
				if not marker:
					continue
				if self._isParagraphEditable(textInfo):
					continue
				try:
					matchedText = textInfo.text.strip()[:60] or "<unavailable>"
				except Exception:
					matchedText = "<unavailable>"
				logHandler.log.debug(
					f"SiteMarker: marker matched for key '{baseKey}' pattern='{marker.get('pattern', '')}' "
					f"matchMode={marker.get('matchMode', 0)} paragraphText='{matchedText}'"
				)
				if self._isSameAnchorAsLastDispatch(baseKey, textInfo):
					# This is the exact same anchor paragraph the previous dispatch for
					# this key already used -- most commonly seen with a negative-offset
					# marker, where the offset-applied landing point sits behind its own
					# anchor in document order, so the very next same-direction scan
					# starts from a caret that still has this anchor sitting ahead of it
					# and legitimately re-discovers it under _isMatchInRightDirection.
					# Recognised directly by anchor position rather than waiting to
					# compute the offset and compare the final target's rendered text,
					# so a single keypress doesn't silently burn through this
					# already-visited anchor before landing on whatever comes after it.
					logHandler.log.debug(
						f"SiteMarker: skipping re-encountered anchor for key '{baseKey}'; "
						f"same anchor as the immediately preceding dispatch, found only "
						f"{totalScanned} paragraph(s) away."
					)
					continue
				offsetVal = marker.get("offset", 0)
				finalInfo = self._applyOffset(textInfo, offsetVal, marker.get("offsetPattern", ""))

				# Direction is validated against the marker's own anchor match (textInfo),
				# not the offset-applied target (finalInfo). The anchor is what the scan
				# actually found moving forward/backward from the old caret -- that's the
				# right thing to check for "did this jump make real progress". The offset
				# is then a deliberate, fixed adjustment relative to that anchor and can
				# legitimately move the final landing point closer to (or slightly behind,
				# in raw document order) the old caret, e.g. a negative offset stepping a
				# few lines back up from an anchor found ahead of the caret.
				if not self._isMatchInRightDirection(oldSelection, direction, textInfo):
					continue
				if not self._isMatchTargetSafe(finalInfo):
					continue
				try:
					finalTargetText = finalInfo.text.strip() if finalInfo.text else ""
				except Exception:
					finalTargetText = ""
				if finalTargetText and finalTargetText == self._getLastDispatchedTargetText(baseKey):
					# The offset-applied target resolves to the exact same on-screen
					# content as the very last dispatch in this direction. Seen on
					# pages with duplicate/virtualized accessibility nodes for the same
					# semantic element (e.g. a Stories carousel where several avatar
					# captions share identical text, or a post's own heading appearing
					# twice in the tree) -- keep scanning past it instead of
					# re-dispatching to content already just visited. Not distance
					# limited: since this only ever compares against the single most
					# recent dispatch in this direction (never a longer history), and
					# that state is overwritten the moment a genuinely different target
					# dispatches, this can only suppress an immediate repeat of the
					# literal last thing spoken -- never a stale one from several jumps
					# ago -- so there is no accumulating false-positive risk from
					# removing the distance limit.
					logHandler.log.debug(
						f"SiteMarker: skipping repeat match for key '{baseKey}'; final target text matches "
						f"the immediately preceding dispatch, found only {totalScanned} paragraph(s) away."
					)
					continue
				core.callLater(10, self._verifyThenDispatchJump, finalInfo.copy(), direction,
							   treeInt, baseKey, markersForThisKey, oldSelection, vpStart, vpEnd, useViewport,
							   textInfo.copy())
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
						   textInfo, oldSelection, vpStart, vpEnd, useViewport,
						   scanToken, loadMoreAttempts, noGrowthCount, expectedUrl, swapAttempts)
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
					resumePoint, oldSelection, vpStart, vpEnd, useViewport,
					scanToken, loadMoreAttempts + 1, noGrowthCount + 1, expectedUrl, swapAttempts
				)
			self._scrollAndWaitForUpdate(treeInt, afterScroll, scanToken, baseKey)
		else:
			self._finishJumpNotFound(direction, treeInt)

	def _finishJumpNotFound(self, direction, treeInt):
		self._clearScanActivity(treeInt)
		if direction < 0 and self._isTreeInterceptorStillFocused(treeInt):
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

	# ------------------------------------------------------------------
	# Auto‑click scanning (using same offset logic)
	# ------------------------------------------------------------------
	def _handleAutoClick(self, gesture, keystroke, _retryCount=0):
		focusObj = api.getFocusObject()
		treeInterceptor = getattr(focusObj, "treeInterceptor", None) if focusObj else None
		if not treeInterceptor or not isinstance(treeInterceptor, browseMode.BrowseModeTreeInterceptor) or treeInterceptor.passThrough:
			if gesture: gesture.send()
			return
		if not self.getBrowserUrl():
			if gesture: gesture.send()
			return

		# Resolve marker configuration before the buffer-readiness wait, same
		# reasoning and ordering as _handleGlobalJump: a site with nothing
		# configured for this auto-click key should report that immediately
		# rather than first paying the retry-loop cost (and, on a short page,
		# risk of a false "not fully loaded" -- see _isBufferReady).
		self.refreshActiveLayout(force=False)
		if not self.activeSiteMarkers or keystroke not in self.activeSiteMarkers:
			# See _handleGlobalJump for why this second, forced refresh is needed:
			# a just-added or just-edited marker for this exact key can otherwise
			# stay invisible until some other action happens to force a refresh.
			self.refreshActiveLayout(force=True)
		if not self.activeSiteMarkers or keystroke not in self.activeSiteMarkers:
			ui.message(_translate("No auto click marker for this key."))
			return

		if not self._isBufferReady(treeInterceptor):
			if _retryCount < 5:
				logHandler.log.debug(
					f"SiteMarker: auto‑click buffer not ready for key '{keystroke}', retry {_retryCount+1}/5"
				)
				core.callLater(400, self._handleAutoClick, gesture, keystroke, _retryCount + 1)
			else:
				self._bufferReadySamples.pop(id(treeInterceptor), None)
				ui.message(_("Page not fully loaded, try again."))
			return

		clickMarkers = [m for m in self.activeSiteMarkers[keystroke] if m.get("actionMode") == "autoClick"]
		if not clickMarkers:
			ui.message(_translate("No auto click marker for this key."))
			return

		self._startAutoClickSearch(treeInterceptor, clickMarkers, gesture, keystroke, fullScanAttempt=0)

	def _startAutoClickSearch(self, treeInt, clickMarkers, gesture, keystroke, expectedUrl=None, swapAttempts=0, fullScanAttempt=0):
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
									expectedUrl, swapAttempts, fullScanAttempt)

	def _processAutoClickChunk(self, treeInt, clickMarkers, gesture, keystroke,
							   textInfo, scanToken, loadMoreAttempts, noGrowthCount,
							   expectedUrl=None, swapAttempts=0, fullScanAttempt=0):
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
											   expectedUrl, swapAttempts + 1, fullScanAttempt)
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
				if marker and self._isParagraphEditable(textInfo):
					marker = None
				if marker:
					offsetVal = marker.get("offset", 0)
					finalInfo = self._applyOffset(textInfo, offsetVal, marker.get("offsetPattern", ""))
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
						if self._isLikelyComboboxTarget(infoVal):
							resumeCount = self._autoClickComboboxResumeCount.get(keystroke, 0) + 1
							self._autoClickComboboxResumeCount[keystroke] = resumeCount
							if resumeCount > MAX_COMBOBOX_RESUME_ATTEMPTS:
								logHandler.log.debug(
									f"SiteMarker: giving up on auto click key '{keystroke}' after {resumeCount} "
									f"consecutive combobox-like matches; stopping instead of resuming silently."
								)
								self._autoClickComboboxResumeCount[keystroke] = 0
								self._clearScanActivity(treeInt)
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
														resumeInfo, newScanToken, 0, 0, expectedUrl, swapAttempts, fullScanAttempt)
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
				if totalScanned % 20 == 0:
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
						   textInfo, scanToken, loadMoreAttempts, noGrowthCount, expectedUrl, swapAttempts, fullScanAttempt)
			return
		# End of document reached without finding marker
		if fullScanAttempt < MAX_FULL_SCAN_RETRIES:
			# A target that isn't there yet (e.g. a YouTube skip-ad button that only
			# renders a second or two into the ad) used to wait a flat 1500ms before the
			# first retry, which was often longer than the user was willing to wait, so
			# they pressed the gesture a second time themselves. Retrying quickly at
			# first, then backing off, finds late-rendering targets within a single
			# keypress far more often while keeping the same total retry budget.
			delay = self._getFullScanRetryDelayMs(fullScanAttempt)
			logHandler.log.debug(
				f"SiteMarker: auto click full scan #{fullScanAttempt + 1} did not find target for '{keystroke}'; "
				f"scheduling retry in {delay} ms."
			)
			core.callLater(delay, self._retryFullAutoClickScan,
						   treeInt, clickMarkers, gesture, keystroke, expectedUrl, fullScanAttempt + 1, scanToken)
			return
		# All retries exhausted
		self._clearScanActivity(treeInt)
		ui.message(_translate("No auto click target found."))

	def _getFullScanRetryDelayMs(self, fullScanAttempt):
		schedule = FULL_SCAN_RETRY_DELAY_SCHEDULE_MS
		index = min(fullScanAttempt, len(schedule) - 1)
		return schedule[index]

	def _retryFullAutoClickScan(self, treeInt, clickMarkers, gesture, keystroke, expectedUrl, fullScanAttempt, originalScanToken):
		if self._autoClickScanToken.get(keystroke) is not originalScanToken:
			return
		if not self._isTreeInterceptorStillFocused(treeInt):
			logHandler.log.debug(f"SiteMarker: auto click retry aborted, tab/document changed.")
			return
		logHandler.log.debug(f"SiteMarker: retrying full auto click scan for '{keystroke}', attempt {fullScanAttempt + 1}/{MAX_FULL_SCAN_RETRIES}")
		self._startAutoClickSearch(treeInt, clickMarkers, gesture, keystroke, expectedUrl, swapAttempts=0, fullScanAttempt=fullScanAttempt)

	# ------------------------------------------------------------------
	# Click execution
	# ------------------------------------------------------------------
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
			focusable = targetInfo.focusableNVDAObjectAtStart
			if focusable:
				if self._performRobustClick(focusable):
					if not skipSpeak:
						speech.speakTextInfo(targetInfo, reason=controlTypes.OutputReason.CARET)
					ui.message(_translate("Clicked"))
				else:
					raise RuntimeError("All click methods failed")
			else:
				if hasattr(treeInt, "activatePosition"):
					treeInt.activatePosition(targetInfo)
					if not skipSpeak:
						speech.speakTextInfo(targetInfo, reason=controlTypes.OutputReason.CARET)
					ui.message(_translate("Clicked"))
				else:
					raise RuntimeError("No focusable object and no activatePosition")
			currentUrl = self.getBrowserUrl()
			paragraphText = targetInfo.text.strip()
			# The click-and-verify retry loop below exists specifically for YouTube's
			# ad-skip button, which can take a moment to become clickable after it
			# first appears, so a single click sometimes needs a follow-up to actually
			# register. On ordinary sites this same verification (checking whether the
			# URL or the caret's paragraph text changed) is not a reliable signal that
			# a click failed: a toggle-style control (e.g. Facebook's Messenger button)
			# can succeed on the first click while leaving both of those unchanged,
			# which wrongly triggered a second, unwanted click that toggled the control
			# back off. Restricting the retry loop to YouTube keeps it working for the
			# case it was built for without affecting autoclick anywhere else, where a
			# single click is now always the final action.
			isYouTube = bool(currentUrl and "youtube.com" in currentUrl.lower())
			if not isYouTube:
				self._autoClickRetryState = None
				self._clearScanActivity(treeInt)
				return
			self._autoClickRetryState = {
				"keystroke": keystroke,
				"gesture": gesture,
				"urlBefore": currentUrl,
				"textBefore": paragraphText,
				"marker": marker,
				"retryCount": retryCount,
				"targetInfoStartOffset": targetInfo._startOffset,
				"treeInt": treeInt
			}
			core.callLater(AUTO_CLICK_RETRY_DELAY, self._checkAutoClickRetry)
		except Exception as e:
			logHandler.log.error(f"Click action failed: {e}")
			ui.message(_translate("Click failed."))
			self._autoClickRetryState = None
			self._clearScanActivity(treeInt)

	def _checkAutoClickRetry(self):
		state = self._autoClickRetryState
		if not state:
			return
		retryCount = state["retryCount"]
		if retryCount >= MAX_CLICK_RETRIES:
			self._autoClickRetryState = None
			self._clearScanActivity(state["treeInt"])
			return
		currentUrl = self.getBrowserUrl()
		focusObj = api.getFocusObject()
		if not focusObj:
			self._autoClickRetryState = None
			self._clearScanActivity(state["treeInt"])
			return
		treeInt = getattr(focusObj, "treeInterceptor", None)
		if not treeInt or treeInt is not state["treeInt"]:
			self._autoClickRetryState = None
			self._clearScanActivity(state["treeInt"])
			return
		paragraphText = ""
		try:
			caretInfo = treeInt.makeTextInfo(textInfos.POSITION_CARET)
			caretInfo.expand(textInfos.UNIT_PARAGRAPH)
			paragraphText = caretInfo.text.strip()
		except Exception:
			pass
		if currentUrl != state["urlBefore"] or paragraphText != state["textBefore"]:
			self._autoClickRetryState = None
			self._clearScanActivity(treeInt)
			return
		state["retryCount"] += 1
		self._executeAutoClick(
			state["marker"],
			treeInt.makeTextInfo(textInfos.POSITION_CARET),
			treeInt,
			state["gesture"],
			state["keystroke"],
			retryCount=state["retryCount"],
			skipSpeak=True
		)

	# ------------------------------------------------------------------
	# Jump alignment and dispatch
	# ------------------------------------------------------------------
	def _alignTargetInfoForComplexScripts(self, targetInfo, originalInfo):
		"""
		Nudges the start of an offset-jump target onto a clean word boundary before it
		is announced, so we don't land mid-glyph-cluster on complex scripts (e.g. Thai
		combining marks), which used to produce truncated/garbled reads. The word check
		is only used to validate the boundary; the returned TextInfo is re-expanded to
		paragraph level so the full line still gets spoken, not just the first word.
		"""
		if not targetInfo:
			return originalInfo
		try:
			aligned = targetInfo.copy()
			aligned.expand(textInfos.UNIT_WORD)
			if not (aligned.text and aligned.text.strip()):
				return originalInfo
			aligned.collapse()
			aligned.expand(textInfos.UNIT_PARAGRAPH)
			if aligned.text and aligned.text.strip():
				return aligned
		except Exception as e:
			logHandler.log.debug(f"SiteMarker: word‑boundary alignment failed: {e}")
		return originalInfo

	def _verifyThenDispatchJump(self, infoToSelect, dirVal, treeInt, baseKey,
							   markersForThisKey, oldSelection, vpStart, vpEnd, useViewport,
							   anchorInfo=None):
		if not self._isTreeInterceptorStillFocused(treeInt):
			logHandler.log.debug(f"SiteMarker: aborting jump dispatch for key '{baseKey}', tab/document changed.")
			return
		try:
			dispatchTargetText = infoToSelect.text.strip() if infoToSelect.text else ""
		except Exception:
			dispatchTargetText = ""
		self._recentScanActivity[id(treeInt)] = time.time()
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
								   resumeInfo, oldSelection, vpStart, vpEnd, useViewport,
								   newScanToken, 0, 0)
			return
		self._jumpComboboxResumeCount[baseKey] = 0

		originalInfo = infoToSelect.copy()
		infoToSelect = self._alignTargetInfoForComplexScripts(infoToSelect, originalInfo)

		try:
			if not infoToSelect.text or not infoToSelect.text.strip():
				logHandler.log.debug("SiteMarker: aligned jump target has no text, falling back to original paragraph.")
				infoToSelect = originalInfo
		except Exception:
			infoToSelect = originalInfo

		try:
			infoToSelect.updateCaret()
			speech.speakTextInfo(infoToSelect, reason=controlTypes.OutputReason.CARET)
		except Exception as e:
			logHandler.log.error(f"Failed to update caret or speak: {e}")
			self._clearScanActivity(treeInt)
			return
		try:
			infoToSelect.collapse()
			if hasattr(treeInt, "selection"):
				try:
					treeInt._set_selection(infoToSelect)
				except AttributeError:
					pass
				treeInt.selection = infoToSelect
			if dispatchTargetText:
				self._setLastDispatchedTargetText(baseKey, dispatchTargetText, infoToSelect, anchorInfo)
		except Exception as e:
			logHandler.log.error(f"Failed to finalize selection: {e}")
		finally:
			# The jump has landed (or failed to finalize) at this point; either way there
			# is no longer an in-flight scan for this document, so stop treating any
			# subsequent, unrelated focus change as a hijack that needs correcting.
			self._clearScanActivity(treeInt)

	# ------------------------------------------------------------------
	# Marker sorting, scrolling, buffer priming, DOM check (unchanged)
	# ------------------------------------------------------------------
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

	def _scrollAndWaitForUpdate(self, treeInt, callback, scanToken, baseKey):
		if not self._isTreeInterceptorStillFocused(treeInt):
			logHandler.log.debug(
				"SiteMarker: skipping load-more scroll, document is no longer the real focus."
			)
			return
		theftCount = self._getEditableTheftStreak()
		if theftCount >= EDITABLE_THEFT_ABORT_THRESHOLD:
			# The synthetic Page Down keeps landing real focus on the same editable
			# control on this particular page (e.g. Messenger's own search box) no
			# matter how many times we recover from it -- recovering and retrying the
			# same scroll just re-triggers the same theft again. Stop sending it and
			# let the scan proceed without actually scrolling; the normal "no new
			# content" exhaustion check will then cleanly end the scan on its own
			# instead of looping through the same theft-and-recover cycle repeatedly.
			logHandler.log.debug(
				"SiteMarker: skipping further load-more scrolling, the last "
				f"{theftCount} attempts each landed focus on an editable control."
			)
			callback()
			return
		self._sendPageDownKeystroke(PAGE_DOWN_BATCH_SIZE)
		self._pendingReveals[scanToken] = (callback, treeInt, baseKey)
		core.callLater(LOAD_MORE_TIMEOUT_MS, self._revealTimeout, scanToken)

	def _revealTimeout(self, scanToken):
		entry = self._pendingReveals.pop(scanToken, None)
		if not entry:
			return
		callback, treeInt, baseKey = entry
		# The synthetic Page Down we just sent is a real hardware-level keystroke from
		# the OS's point of view. On sites with dynamically loading content (e.g. a chat
		# thread that is still streaming in new messages) it can occasionally land real
		# focus on an unrelated editable/combobox control instead of just scrolling the
		# document. Verify focus is still where we expect before resuming the scan on a
		# document that may no longer have real OS focus. Note: theft counting itself
		# happens centrally in _recoverFromUnexpectedEditableFocus, not here, since the
		# global event_gainFocus hook often notices and recovers from the same theft
		# before this delayed check even runs.
		try:
			focusObj = api.getFocusObject()
		except (COMError, RuntimeError, OSError) as e:
			logHandler.log.debug(f"SiteMarker: could not read focus after load-more scroll: {e}")
			callback()
			return
		looksEditable = False
		if focusObj is not None:
			try:
				looksEditable = self._objectLooksEditableOrCombobox(focusObj)
			except (COMError, RuntimeError, OSError):
				looksEditable = False
		if not looksEditable:
			callback()
			return
		logHandler.log.debug(
			"SiteMarker: load-more scroll left real focus on an editable/combobox object; "
			"recovering before resuming scan."
		)
		self._recoverFromUnexpectedEditableFocus(focusObj, treeInt)
		core.callLater(200, self._resumeLoadMoreAfterFocusRecovery, callback, treeInt)

	def _resumeLoadMoreAfterFocusRecovery(self, callback, treeInt):
		if not self._isTreeInterceptorStillFocused(treeInt):
			logHandler.log.debug(
				"SiteMarker: aborting load-more resume, document changed during focus recovery."
			)
			return
		callback()

	def _primeDocumentBuffer(self, treeInt, primeToken, count, passNum):
		if self._primeToken is not primeToken:
			return
		if count >= PRIME_DOCUMENT_MAX_PARAGRAPHS:
			logHandler.log.debug(f"SiteMarker: buffer priming finished ({count} paragraphs)")
			self._primeTextInfo = None
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
				_ = primeInfo.text
			except Exception:
				pass
			if primeInfo.move(textInfos.UNIT_PARAGRAPH, 1) == 0:
				self._primeTextInfo = None
				logHandler.log.debug(f"SiteMarker: buffer priming reached end at paragraph {count}")
				return
			self._primeTextInfo = primeInfo
		except Exception:
			self._primeTextInfo = None
			return
		core.callLater(PRIME_DOCUMENT_STEP_DELAY_MS, self._primeDocumentBuffer, treeInt, primeToken, count + 1, passNum)
