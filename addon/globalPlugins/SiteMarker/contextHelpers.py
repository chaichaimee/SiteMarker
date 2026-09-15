# contextHelpers.py

import time
import api
import core
import controlTypes
import textInfos
from comtypes import COMError
import logHandler
import browseMode
import winUser
import UIAHandler
from .constants import (
	FOCUS_RECOVERY_WINDOW_SEC,
	VIEWPORT_SCAN_RANGE,
	TREE_INTERCEPTOR_POLL_INTERVAL_MS,
)


class ContextHelpersMixin:
	def event_foreground(self, foregroundObj, nextHandler):
		"""
		Watchdog Trigger: Evaluates if the newly focused foreground window belongs to a supported web browser.
		If true, it wakes up the plugin; otherwise, it puts it to sleep to prevent high-frequency UIA freezing.
		"""
		try:
			targetAppModule = getattr(foregroundObj, "appModule", None)
			targetAppName = getattr(targetAppModule, "appModuleName", "").lower() if targetAppModule else ""
			windowClass = getattr(foregroundObj, "windowClassName", "")

			# Toggle state based on known browser app names or window classes
			if windowClass in ("Chrome_WidgetWin_1", "MozillaWindowClass") or targetAppName in (
				"chrome.exe", "msedge.exe", "firefox.exe", 
				"brave.exe", "opera.exe", "vivaldi.exe", "thorium.exe"
			):
				self._isTargetActive = True
				# Belt-and-suspenders trigger point for buffer priming and
				# auto-trigger-on-page-load: event_treeInterceptor_gainFocus
				# (below) was assumed to be the reliable source for "a
				# document just became active" but logging added to confirm
				# that assumption showed it never firing at all in a real
				# session, across repeated tests, on this browser -- while
				# event_foreground (this method) is confirmed firing every
				# time (this is exactly how _isTargetActive itself gets set).
				# Routing through the same shared, dedup-guarded helper as
				# event_treeInterceptor_gainFocus means whichever event
				# actually fires first wins and the other becomes a safe
				# no-op, rather than picking one and hoping.
				self._primeAndCheckAutoTrigger(source="event_foreground")
			else:
				self._isTargetActive = False
		except (RuntimeError, COMError, AttributeError) as error:
			logHandler.log.debug(f"SiteMarker: Error evaluating foreground watchdog: {error}")
			self._isTargetActive = False
		finally:
			# Guarantee execution of downstream addons regardless of failure
			nextHandler()

	def _isInBrowser(self):
		return self.getBrowserUrl() is not None

	def isInEditableContext(self):
		focusObj = api.getFocusObject()
		if not focusObj:
			return False
		return focusObj.role in (controlTypes.Role.EDITABLETEXT, controlTypes.Role.COMBOBOX)

	def _objectLooksEditableOrCombobox(self, targetObj):
		if not targetObj:
			return False
		try:
			role = targetObj.role
		except Exception:
			return False
		comboRoles = [controlTypes.Role.EDITABLETEXT, controlTypes.Role.COMBOBOX]
		searchboxRole = getattr(controlTypes.Role, "SEARCHBOX", None)
		if searchboxRole is not None:
			comboRoles.append(searchboxRole)
		if role in comboRoles:
			return True
		try:
			states = targetObj.states
		except Exception:
			states = set()
		if controlTypes.State.EDITABLE in states:
			return True
		autoCompleteState = getattr(controlTypes.State, "AUTOCOMPLETE", None)
		if autoCompleteState is not None and autoCompleteState in states:
			return True
		return False

	def _objectHasSearchAttributes(self, targetObj):
		try:
			attrs = getattr(targetObj, "IA2Attributes", None)
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
			return False
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
			return not self._isMatchTargetSafe(textInfo)
		except Exception:
			return not self._isMatchTargetSafe(textInfo)
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

	def _isStaleBufferError(self, exc):
		message = str(exc)
		return any(marker in message for marker in (
			"too big for buffer",
			"out of range",
			"is past end of buffer",
		))

	def _isTreeInterceptorStillFocused(self, treeInt):
		"""
		Returns True only if the given treeInterceptor is still the current one.

		Deliberately does NOT fall back to comparing base URLs: an SPA can replace the
		treeInterceptor object while keeping the same URL, in which case the old object is
		dead and any textInfo derived from it is a stale COM reference. Continuing to use it
		produces COMError/RPC_E_DISCONNECTED and can crash NVDA, so identity is the only
		thing that counts here.
		"""
		try:
			focusObj = api.getFocusObject()
		except Exception:
			return False

		try:
			currentTreeInt = getattr(focusObj, "treeInterceptor", None) if focusObj else None
		except Exception:
			currentTreeInt = None
		if currentTreeInt is None:
			try:
				realObj = self.getRealWebFocusObject()
			except Exception:
				realObj = None
			try:
				currentTreeInt = getattr(realObj, "treeInterceptor", None) if realObj else None
			except Exception:
				currentTreeInt = None

		return currentTreeInt is treeInt

	def _isMatchInRightDirection(self, oldSelection, direction, textInfo):
		origin = oldSelection.copy()
		origin.collapse(end=direction > 0)
		origin.expand(textInfos.UNIT_PARAGRAPH)
		origin.collapse(end=direction > 0)
		if direction > 0:
			origin.move(textInfos.UNIT_CHARACTER, -1)
		cmp = origin.compareEndPoints(textInfo, "startToStart")
		return direction * cmp < 0

	def _getViewportRange(self, treeInt):
		try:
			caret = treeInt.makeTextInfo(textInfos.POSITION_CARET)
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
		except Exception as e:
			logHandler.log.debug(f"SiteMarker: viewport range computation failed, document likely re-rendered: {e}")
			return None, None

	def _recoverFromUnexpectedEditableFocus(self, focusedObj, treeInt):
		if treeInt is None:
			return
		lastActivity = self._recentScanActivity.get(id(treeInt))
		if lastActivity is None or (time.time() - lastActivity) > FOCUS_RECOVERY_WINDOW_SEC:
			return
		try:
			looksEditable = self._objectLooksEditableOrCombobox(focusedObj)
		except Exception:
			return
		if not looksEditable:
			return
		logHandler.log.debug(
			"SiteMarker: real focus landed on an editable/combobox object during a scan; "
			"restoring focus to the document automatically."
		)
		self._recentScanActivity[id(treeInt)] = time.time()
		# Counted here rather than at each individual caller (the global
		# event_gainFocus hook and scanEngine's own post-scroll check both call this
		# same function) because this is the one point guaranteed to run exactly once
		# per real theft-and-recovery event, regardless of which caller noticed it
		# first. Counting separately at each caller was unreliable: whichever caller
		# ran second would often find focus already looking fine again (because the
		# other caller's recovery had already taken effect moments earlier) and would
		# incorrectly reset its own count back to zero instead of registering that a
		# theft had in fact just occurred.
		if not hasattr(self, '_editableTheftStreak'):
			self._editableTheftStreak = 0
		self._editableTheftStreak += 1
		# The actual objects (not just their ids) are captured here and passed
		# through to the callback below. This keeps them alive for the full 200ms
		# delay, which matters: a bare id() with nothing holding the original
		# object alive can be silently reused by an unrelated object elsewhere
		# (e.g. one from a completely different background tab) once the original
		# is garbage collected, and a later id()-only comparison would then wrongly
		# match that unrelated object. The callback still never touches these held
		# references directly for anything other than an `is` identity check --
		# all live state is re-acquired fresh from api.getFocusObject() inside it,
		# which is what avoids operating on a stale COM reference.
		core.callLater(200, self._verifyAndRecoverFocus, treeInt, focusedObj)

	def _getEditableTheftStreak(self):
		return getattr(self, '_editableTheftStreak', 0)

	def _resetEditableTheftStreak(self):
		self._editableTheftStreak = 0

	def _clearScanActivity(self, treeInt):
		# Called from the natural completion points of a jump/autoclick operation
		# (success, fallback, or give-up) so the focus-recovery guard goes cold the
		# instant there is no longer an in-flight scan, instead of staying armed for
		# the full FOCUS_RECOVERY_WINDOW_SEC regardless of whether anything is still
		# actually pending. This is what keeps normal focus changes that merely
		# happen to occur soon after a finished scan from being treated as a hijack.
		if treeInt is None:
			return
		self._recentScanActivity.pop(id(treeInt), None)

	def _getLastDispatchedTargetTexts(self):
		# The single most recently dispatched final landing target's own text,
		# keyed by baseKey only (not by direction). Used to catch a fresh scan's
		# match resolving to the exact same on-screen target as the previous
		# dispatch in EITHER direction -- observed on Facebook, both for its
		# Stories carousel (several adjacent buffer paragraphs share the
		# identical Thai-language share-caption text) and for ordinary post headers
		# appearing more than once in the accessibility tree. Not keyed by
		# direction: a shift+j pressed immediately after a j landing back on the
		# literal same content that j just dispatched to is always either a
		# genuine duplicate DOM node for the same conceptual element (this
		# should be skipped) or, at worst, skips forward to what would very
		# likely have been the actual previous target anyway -- keying by
		# direction let this specific cross-direction case slip through
		# entirely, since a forward dispatch was never compared against a
		# subsequent backward one. Comparing only the single most recent
		# dispatch, by the target's own rendered text (not a name+location
		# identity, which for many elements collapses to the same value across
		# completely unrelated content and caused matches to be wrongly
		# skipped) is enough on its own: this state is overwritten the moment a
		# genuinely different target dispatches, so it can only ever suppress
		# an immediate repeat of the literal last thing spoken, never a stale
		# one from several jumps ago -- no distance limit is needed to keep it
		# from misfiring on unrelated content further down the document.
		if not hasattr(self, '_lastDispatchedTargetTexts'):
			self._lastDispatchedTargetTexts = {}
		return self._lastDispatchedTargetTexts

	def _getLastDispatchedTargetPositions(self):
		# Companion store to _getLastDispatchedTargetTexts: remembers where the
		# caret was actually left right after the last dispatch for a given
		# baseKey. A later scan uses this to tell whether the user's caret is
		# still sitting at that exact spot (an immediate repeat press, where the
		# text-based duplicate guard above is meant to apply) or has since moved
		# elsewhere under the user's own steam -- arrow keys, a click, or a jump
		# on a different key. In the latter case the remembered text no longer
		# describes "the thing we just landed next to"; it is stale bookkeeping
		# that would otherwise wrongly block a deliberate return trip back to
		# that same marker.
		if not hasattr(self, '_lastDispatchedTargetPositions'):
			self._lastDispatchedTargetPositions = {}
		return self._lastDispatchedTargetPositions

	def _getLastDispatchedAnchorPositions(self):
		# Companion store to _getLastDispatchedTargetTexts/_getLastDispatchedTargetPositions,
		# but remembers the marker's raw anchor match (the paragraph the scan actually
		# found, before any offset was applied) rather than the offset-shifted final
		# landing point. This matters specifically for negative-offset markers: the
		# offset can move the final landing point behind the anchor in document order,
		# so the very next same-direction scan starts from a caret that still has the
		# same anchor sitting ahead of it and will legitimately re-discover it under
		# _isMatchInRightDirection. Comparing the anchor position directly lets that
		# re-discovery be recognised and skipped immediately, instead of relying only
		# on the offset-applied target's rendered text happening to match the previous
		# dispatch -- text equality is a reasonable second-line guard for genuinely
		# duplicate DOM nodes, but is not a reliable way to recognise "this is the
		# exact same anchor I already used", especially on pages where unrelated
		# paragraphs legitimately share identical boilerplate text.
		if not hasattr(self, '_lastDispatchedAnchorPositions'):
			self._lastDispatchedAnchorPositions = {}
		return self._lastDispatchedAnchorPositions

	def _getLastDispatchedTargetText(self, baseKey):
		return self._getLastDispatchedTargetTexts().get(baseKey)

	def _isSameAnchorAsLastDispatch(self, baseKey, textInfo):
		lastAnchor = self._getLastDispatchedAnchorPositions().get(baseKey)
		if lastAnchor is None:
			return False
		try:
			return textInfo.compareEndPoints(lastAnchor, "startToStart") == 0
		except Exception:
			return False

	def _setLastDispatchedTargetText(self, baseKey, text, position=None, anchorPosition=None):
		self._getLastDispatchedTargetTexts()[baseKey] = text
		if position is None:
			self._getLastDispatchedTargetPositions().pop(baseKey, None)
		else:
			try:
				self._getLastDispatchedTargetPositions()[baseKey] = position.copy()
			except Exception:
				self._getLastDispatchedTargetPositions().pop(baseKey, None)
		if anchorPosition is None:
			self._getLastDispatchedAnchorPositions().pop(baseKey, None)
			return
		try:
			self._getLastDispatchedAnchorPositions()[baseKey] = anchorPosition.copy()
		except Exception:
			self._getLastDispatchedAnchorPositions().pop(baseKey, None)

	def _dropStaleLastDispatchedTarget(self, baseKey, currentCaret):
		# Called at the very start of a fresh key press, before the text-based
		# duplicate guard in _processJumpChunk can run. If the caret is no
		# longer where the last dispatch for this key left it, the remembered
		# target text is stale and must be discarded -- otherwise a deliberate
		# return trip to the same marker (e.g. the user pressed j, then moved
		# away with the up arrow, then pressed j again) gets misread as the
		# same duplicate-node case this guard exists to catch, and the scan
		# skips past the real target looking for a "different" one that may
		# not exist.
		lastPosition = self._getLastDispatchedTargetPositions().get(baseKey)
		if lastPosition is None:
			return
		try:
			stillAtLastDispatch = currentCaret.compareEndPoints(lastPosition, "startToStart") == 0
		except Exception:
			stillAtLastDispatch = False
		if not stillAtLastDispatch:
			# A strict position mismatch here is not always a genuine caret move by
			# the user: a live/virtualized feed (observed on Facebook) can silently
			# replace a paragraph's underlying accessibility node on re-render while
			# the visible content at the caret stays exactly the same, which makes
			# the old stored TextInfo compare as "different" even though nothing the
			# user actually did moved the caret. Treating that kind of node churn as
			# a deliberate move away was silently wiping the duplicate-dispatch guard
			# between presses, letting the very next scan re-announce the same
			# marker a second time. Falling back to a text-level check on the
			# caret's own paragraph -- reusing the same rendered text already stored
			# by the guard -- distinguishes that case from an actual user-driven
			# move (arrow keys, a click, a jump on a different key), where the
			# caret's paragraph text will genuinely differ.
			lastText = self._getLastDispatchedTargetText(baseKey)
			currentText = ""
			if lastText:
				try:
					currentParagraph = currentCaret.copy()
					currentParagraph.expand(textInfos.UNIT_PARAGRAPH)
					currentText = currentParagraph.text.strip() if currentParagraph.text else ""
				except Exception:
					currentText = ""
			stillAtLastDispatch = bool(lastText) and currentText == lastText
		if not stillAtLastDispatch:
			self._getLastDispatchedTargetTexts().pop(baseKey, None)
			self._getLastDispatchedTargetPositions().pop(baseKey, None)
			self._getLastDispatchedAnchorPositions().pop(baseKey, None)

	def _verifyAndRecoverFocus(self, expectedTreeInt, unwantedObj):
		"""
		Re-checks, from scratch, whether the object that stole real OS focus during a
		scan is still focused before doing anything about it. Runs once, 200ms after
		the theft was first noticed, and never sends synthetic keyboard input: letting
		the OS resolve window/control z-order on its own is what avoids the ghost-focus
		and double-speak races that repeated escape injection used to cause.
		"""
		try:
			currentFocus = api.getFocusObject()
		except Exception:
			return
		if currentFocus is None or currentFocus is not unwantedObj:
			# Focus already moved on (possibly because the scan itself finished and
			# landed normally); nothing left to recover.
			return
		try:
			stillEditable = self._objectLooksEditableOrCombobox(currentFocus)
		except Exception:
			return
		if not stillEditable:
			return
		try:
			currentTreeInt = getattr(currentFocus, "treeInterceptor", None)
		except Exception:
			currentTreeInt = None
		if currentTreeInt is None:
			try:
				realObj = self.getRealWebFocusObject()
			except Exception:
				realObj = None
			try:
				currentTreeInt = getattr(realObj, "treeInterceptor", None) if realObj else None
			except Exception:
				currentTreeInt = None
		if currentTreeInt is None or currentTreeInt is not expectedTreeInt:
			return
		try:
			rootObj = getattr(currentTreeInt, "rootNVDAObject", None)
			if rootObj is not None:
				rootObj.setFocus()
		except Exception as e:
			logHandler.log.debug(f"SiteMarker: focus recovery setFocus failed: {e}")

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

	def _tryRecoverFreshTreeInterceptor(self, expectedUrl):
		if not expectedUrl:
			return None
		try:
			focusObj = api.getFocusObject()
		except Exception:
			return None
		treeInt = getattr(focusObj, "treeInterceptor", None) if focusObj else None
		if treeInt is None:
			realObj = self.getRealWebFocusObject()
			treeInt = getattr(realObj, "treeInterceptor", None) if realObj else None
		if not treeInt or not isinstance(treeInt, browseMode.BrowseModeTreeInterceptor):
			return None
		currentUrl = self.getBrowserUrl()
		if not currentUrl:
			return None
		if currentUrl.split("?", 1)[0] != expectedUrl.split("?", 1)[0]:
			return None
		return treeInt

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
			return
		if not force and currentUrl == self.lastProcessedUrl and self.activeSiteMarkers:
			return
		rawMarkers = self.engine.getMarkersForUrl(currentUrl)
		self.activeSiteMarkers = {k.strip().lower(): v for k, v in rawMarkers.items() if k.strip()}
		self.lastProcessedUrl = currentUrl
		self._getLastDispatchedTargetTexts().clear()
		self._getLastDispatchedTargetPositions().clear()
		self._getLastDispatchedAnchorPositions().clear()
		self._syncCustomMarkerGestureBindings()
		focusObj = api.getFocusObject()
		treeInt = getattr(focusObj, "treeInterceptor", None) if focusObj else None
		if treeInt:
			self._sortMarkersByDocumentOrder(treeInt)

	def event_gainFocus(self, focusedObj, nextHandler):
		if not self._isTargetActive:
			return nextHandler()

		try:
			treeInt = getattr(focusedObj, "treeInterceptor", None)
		except Exception:
			treeInt = None
		try:
			if treeInt is not None and isinstance(treeInt, browseMode.BrowseModeTreeInterceptor):
				self.refreshActiveLayout(force=False)
		except Exception as e:
			logHandler.log.error(f"SiteMarker: event_gainFocus layout refresh failed: {e}", exc_info=True)
		finally:
			nextHandler()

		try:
			self._recoverFromUnexpectedEditableFocus(focusedObj, treeInt)
		except Exception as e:
			logHandler.log.debug(f"SiteMarker: focus-theft recovery check failed: {e}")

	def _primeAndCheckAutoTrigger(self, source=""):
		# Shared by event_foreground, event_treeInterceptor_gainFocus, and
		# _pollForTreeInterceptorChange (see the comment in event_foreground
		# for why more than one call site exists).
		# Resolves the current document's treeInterceptor independently
		# rather than requiring the caller to already have one in hand, since
		# event_foreground's foregroundObj is the window, not necessarily the
		# focused document.
		try:
			focusObj = api.getFocusObject()
			treeInt = getattr(focusObj, "treeInterceptor", None) if focusObj else None
		except (RuntimeError, COMError, AttributeError) as e:
			logHandler.log.debug(f"SiteMarker: _primeAndCheckAutoTrigger ({source}) could not resolve treeInterceptor: {e}")
			return
		if treeInt is None:
			return
		if treeInt in self._primedTreeInterceptors:
			return
		self._addPrimedTreeInterceptor(treeInt)
		treeIntId = id(treeInt)  # for log messages only -- see _addPrimedTreeInterceptor
		primeToken = object()
		self._primeToken = primeToken
		logHandler.log.debug(
			f"SiteMarker: starting buffer priming pass 1 for treeInt {treeIntId} (via {source})"
		)
		core.callLater(300, self._primeDocumentBuffer, treeInt, primeToken, 0, 1)
		# Auto-jump/auto-click-on-page-load (Marker Properties): checked at
		# most once per fresh treeInterceptor, piggybacking on the same
		# "first time seeing this page" guard as buffer priming above,
		# rather than a separate dedup flag.
		logHandler.log.debug(
			f"SiteMarker: scheduling auto-trigger check for treeInt {treeIntId} (via {source})"
		)
		core.callLater(300, self._checkAutoTriggerMarker, treeInt, 0)

	def startTreeInterceptorPoll(self):
		# Fallback for buffer priming / auto-trigger detection when neither
		# event_foreground nor event_treeInterceptor_gainFocus can be relied
		# on to reach this add-on (log-confirmed: in-page navigation that
		# doesn't change the foreground window, e.g. following a bookmark
		# while already in the browser, fires neither reliably in some
		# environments -- most likely another loaded global plugin breaking
		# the shared event chain before it reaches here). Polling for a
		# treeInterceptor change is slower to react than a real event but
		# doesn't depend on any other add-on's event handler behaving
		# correctly. Started once from GlobalPlugin.__init__.
		self._treeInterceptorPollActive = True
		self._lastPolledTreeInterceptor = None
		self._pollForTreeInterceptorChange()

	def stopTreeInterceptorPoll(self):
		# The liveness flag, not just cancelling the next scheduled call, is
		# what actually stops this: _pollForTreeInterceptorChange reschedules
		# itself, so a cancelled single callLater wouldn't be enough to stop
		# the chain once another one is already pending (5.14).
		self._treeInterceptorPollActive = False

	def _pollForTreeInterceptorChange(self):
		if not self._treeInterceptorPollActive:
			return
		try:
			if self._isTargetActive:
				focusObj = api.getFocusObject()
				treeInt = getattr(focusObj, "treeInterceptor", None) if focusObj else None
				if treeInt is not None and treeInt is not self._lastPolledTreeInterceptor:
					self._lastPolledTreeInterceptor = treeInt
					self._primeAndCheckAutoTrigger(source="poll")
		except (RuntimeError, COMError, AttributeError) as e:
			logHandler.log.debug(f"SiteMarker: treeInterceptor poll error: {e}")
		core.callLater(TREE_INTERCEPTOR_POLL_INTERVAL_MS, self._pollForTreeInterceptorChange)

	def event_treeInterceptor_gainFocus(self, treeInterceptorObj, nextHandler):
		# Unconditional diagnostic: confirms whether NVDA is actually firing
		# this event at all for this browser/session. Kept even though
		# event_foreground is now the confirmed-reliable trigger (see above),
		# since this may still fire correctly on other browsers/setups and
		# the diagnostic costs nothing to leave in place.
		logHandler.log.debug(
			f"SiteMarker: event_treeInterceptor_gainFocus fired for treeInt "
			f"{id(treeInterceptorObj)}, _isTargetActive={self._isTargetActive}"
		)
		if not self._isTargetActive:
			return nextHandler()

		try:
			# Invalidate every in-flight scan so its next chunk callback sees a token
			# mismatch and aborts instead of continuing to run against the document
			# that just lost focus (which, combined with the identity-only check in
			# _isTreeInterceptorStillFocused, is what actually stops stale-COM crashes
			# after an SPA swap).
			self._jumpScanToken.clear()
			self._autoClickScanToken.clear()
			self._sortScanToken = None
			self._pendingReveals.clear()
			self.refreshActiveLayout(force=False)
			self._primeAndCheckAutoTrigger(source="event_treeInterceptor_gainFocus")
		except Exception as e:
			logHandler.log.error(f"SiteMarker: event_treeInterceptor_gainFocus failed: {e}", exc_info=True)
		finally:
			nextHandler()

	def event_virtualBufferUpdated(self, treeInterceptorObj, nextHandler):
		if not self._isTargetActive:
			return nextHandler()

		try:
			now = time.time()
			if now - self._lastVirtualBufferUpdate > 0.3:
				self.refreshActiveLayout(force=True)
				self._lastVirtualBufferUpdate = now
			readyTokens = [
				token for token, entry in self._pendingReveals.items()
				if entry[1] == treeInterceptorObj
			]
			for token in readyTokens:
				entry = self._pendingReveals.pop(token, None)
				if entry:
					cb = entry[0]
					core.callLater(0, cb)
		except Exception as e:
			logHandler.log.error(f"SiteMarker: event_virtualBufferUpdated failed: {e}", exc_info=True)
		finally:
			nextHandler()







