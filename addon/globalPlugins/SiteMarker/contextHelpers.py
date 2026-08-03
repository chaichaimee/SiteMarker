# contextHelpers.py

import time
import api
import core
import controlTypes
import textInfos
from comtypes import COMError
import logHandler
import keyboardHandler
import browseMode
from .constants import (
	FOCUS_RECOVERY_WINDOW_SEC,
	VIEWPORT_SCAN_RANGE,
)


class ContextHelpersMixin:
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
			# A transient COMError here does not mean the target is a combobox; it
			# usually means the node is part of a virtualized/recycled list (e.g. a
			# chat message list) and briefly went stale. Trust the already-computed
			# object based check (_isRealObjectComboboxLike) instead of assuming the
			# worst, otherwise a single flaky read causes the jump to loop forever
			# re-resuming on the same paragraph without ever landing.
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
		# Chrome's virtual buffer reports these specific messages when the page
		# has re-rendered (SPA update) and the offset we were using no longer
		# exists in the buffer at all. This is not a transient glitch worth
		# retrying like other COMErrors; the position is permanently gone and
		# retrying against it just wastes time and spams the log.
		message = str(exc)
		return any(marker in message for marker in (
			"too big for buffer",
			"out of range",
			"is past end of buffer",
		))
	def _isTreeInterceptorStillFocused(self, treeInt):
		# Deferred (core.callLater) continuations must never act on a treeInterceptor
		# that is no longer the one the user is actually looking at. Chrome/UIA will
		# raise a background tab to the foreground if we set selection or activate a
		# position on it, which is exactly the "jump switched to another tab" bug.
		try:
			focusObj = api.getFocusObject()
		except Exception:
			return False
		currentTreeInt = getattr(focusObj, "treeInterceptor", None) if focusObj else None
		if currentTreeInt is None:
			realObj = self.getRealWebFocusObject()
			currentTreeInt = getattr(realObj, "treeInterceptor", None) if realObj else None
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
	def _recoverFromUnexpectedEditableFocus(self, obj, treeInt):
		# Our jump/auto click matching logic always skips editable and combobox
		# targets (see _isParagraphEditable / _isLikelyComboboxTarget), so it never
		# intentionally lands real focus on one. If real focus lands on an
		# editable/combobox object anyway while we were actively scanning its
		# document (or just finished, within FOCUS_RECOVERY_WINDOW_SEC), that is a
		# side effect of Chrome's own accessibility handling for that node, not
		# something the user asked for (e.g. the "Search Facebook" combobox that
		# was silently stealing focus during scanning). Automatically back out so
		# the user is not left stuck typing into a field they never chose to enter.
		if treeInt is None:
			return
		lastActivity = self._recentScanActivity.get(id(treeInt))
		if lastActivity is None or (time.time() - lastActivity) > FOCUS_RECOVERY_WINDOW_SEC:
			return
		try:
			looksEditable = self._objectLooksEditableOrCombobox(obj)
		except Exception:
			return
		if not looksEditable:
			return
		logHandler.log.debug(
			"SiteMarker: real focus landed on an editable/combobox object during a scan; "
			"restoring focus to the document automatically."
		)
		# Refresh (do not clear) the activity timestamp: Facebook can steal focus
		# back into this same field repeatedly in quick succession, and each
		# occurrence needs its own recovery, not just the first one.
		self._recentScanActivity[id(treeInt)] = time.time()
		core.callLater(0, lambda: self._sendRecoveryEscape(treeInt, obj, 0))
	def _sendRecoveryEscape(self, treeInt, obj, attempt):
		# Prefer moving real focus directly to the treeInterceptor's own root
		# object over simulating an Escape keypress. Escape's effect is decided
		# entirely by the web page's own key handler, and for a combobox living
		# inside a floating panel (e.g. the Messenger chat popup) it closed the
		# whole panel instead of just leaving the field. setFocus() bypasses the
		# page's keyboard handling and moves focus deterministically.
		try:
			rootObj = getattr(treeInt, "rootNVDAObject", None)
			if rootObj is not None:
				rootObj.setFocus()
				core.callLater(150, self._verifyRecoveryEscape, treeInt, obj, attempt)
				return
		except Exception as e:
			logHandler.log.debug(f"SiteMarker: setFocus recovery failed, falling back to escape: {e}")
		try:
			keyboardHandler.KeyboardInputGesture.fromName("escape").send()
		except Exception as e:
			logHandler.log.debug(f"SiteMarker: recovery escape failed: {e}")
			return
		core.callLater(150, self._verifyRecoveryEscape, treeInt, obj, attempt)
	def _verifyRecoveryEscape(self, treeInt, obj, attempt):
		try:
			currentFocus = api.getFocusObject()
		except Exception:
			return
		stillStuck = currentFocus is not None and (
			currentFocus is obj or getattr(currentFocus, "treeInterceptor", None) is None
		)
		if not stillStuck:
			return
		try:
			stillStuck = self._objectLooksEditableOrCombobox(currentFocus)
		except Exception:
			return
		if not stillStuck:
			return
		if attempt >= 2:
			logHandler.log.debug(
				"SiteMarker: escape did not clear editable focus after multiple attempts; giving up."
			)
			return
		logHandler.log.debug(
			f"SiteMarker: still focused on editable/combobox after escape, retrying (attempt {attempt + 1})."
		)
		self._sendRecoveryEscape(treeInt, currentFocus, attempt + 1)
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
		# Compare ignoring query string: an SPA route/query change on the same
		# site is still "the same page" for this purpose, only the base path
		# needs to match for the auto-click target to plausibly still be there.
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
		self._recoverFromUnexpectedEditableFocus(obj, treeInt)
	def event_treeInterceptor_gainFocus(self, treeInterceptor, nextHandler):
		self._pendingReveals.clear()
		self.refreshActiveLayout(force=False)
		treeIntId = id(treeInterceptor)
		if treeIntId not in self._primedTreeInterceptors:
			self._primedTreeInterceptors.add(treeIntId)
			primeToken = object()
			self._primeToken = primeToken
			logHandler.log.debug(f"SiteMarker: starting buffer priming pass 1 for treeInt {treeIntId}")
			core.callLater(300, self._primeDocumentBuffer, treeInterceptor, primeToken, 0, 1)
		nextHandler()
	def event_virtualBufferUpdated(self, treeInterceptor, nextHandler):
		now = time.time()
		if now - self._lastVirtualBufferUpdate > 0.3:
			self.refreshActiveLayout(force=True)
			self._lastVirtualBufferUpdate = now
		readyTokens = [
			token for token, (cb, treeInt) in self._pendingReveals.items()
			if treeInt == treeInterceptor
		]
		for token in readyTokens:
			entry = self._pendingReveals.pop(token, None)
			if entry:
				cb, _ = entry
				core.callLater(0, cb)
		nextHandler()
