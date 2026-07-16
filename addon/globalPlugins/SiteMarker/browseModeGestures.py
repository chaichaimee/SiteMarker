# browseModeGestures.py

import addonHandler
import browseMode
import textInfos
import speech

addonHandler.initTranslation()

def registerGestures(plugin):
	cls = browseMode.BrowseModeTreeInterceptor

	cls._BrowseModeTreeInterceptor__gestures["kb:j"] = "siteMarker_jump_j"
	cls._BrowseModeTreeInterceptor__gestures["kb:shift+j"] = "siteMarker_jump_j_back"
	cls._BrowseModeTreeInterceptor__gestures["kb:f"] = "siteMarker_jump_f"
	cls._BrowseModeTreeInterceptor__gestures["kb:shift+f"] = "siteMarker_jump_f_back"
	cls._BrowseModeTreeInterceptor__gestures["kb:d"] = "siteMarker_jump_d"
	cls._BrowseModeTreeInterceptor__gestures["kb:shift+d"] = "siteMarker_jump_d_back"
	cls._BrowseModeTreeInterceptor__gestures["kb:z"] = "siteMarker_jump_z"
	cls._BrowseModeTreeInterceptor__gestures["kb:shift+z"] = "siteMarker_jump_z_back"
	cls._BrowseModeTreeInterceptor__gestures["kb:alt+j"] = "siteMarker_autoClick_j"
	cls._BrowseModeTreeInterceptor__gestures["kb:alt+c"] = "siteMarker_autoClick_c"
	cls._BrowseModeTreeInterceptor__gestures["kb:alt+x"] = "siteMarker_autoClick_x"
	cls._BrowseModeTreeInterceptor__gestures["kb:alt+z"] = "siteMarker_autoClick_z"

	def script_siteMarker_jump_j(self, gesture):
		_jump(self, gesture, "j", plugin, isShift=False)
	script_siteMarker_jump_j.__doc__ = _("Jump forward to next marker J")

	def script_siteMarker_jump_j_back(self, gesture):
		_jump(self, gesture, "j", plugin, isShift=True)
	script_siteMarker_jump_j_back.__doc__ = _("Jump backward to previous marker J")

	def script_siteMarker_jump_f(self, gesture):
		_jump(self, gesture, "f", plugin, isShift=False)
	script_siteMarker_jump_f.__doc__ = _("Jump forward to next marker F")

	def script_siteMarker_jump_f_back(self, gesture):
		_jump(self, gesture, "f", plugin, isShift=True)
	script_siteMarker_jump_f_back.__doc__ = _("Jump backward to previous marker F")

	def script_siteMarker_jump_d(self, gesture):
		_jump(self, gesture, "d", plugin, isShift=False)
	script_siteMarker_jump_d.__doc__ = _("Jump forward to next marker D")

	def script_siteMarker_jump_d_back(self, gesture):
		_jump(self, gesture, "d", plugin, isShift=True)
	script_siteMarker_jump_d_back.__doc__ = _("Jump backward to previous marker D")

	def script_siteMarker_jump_z(self, gesture):
		_jump(self, gesture, "z", plugin, isShift=False)
	script_siteMarker_jump_z.__doc__ = _("Jump forward to next marker Z")

	def script_siteMarker_jump_z_back(self, gesture):
		_jump(self, gesture, "z", plugin, isShift=True)
	script_siteMarker_jump_z_back.__doc__ = _("Jump backward to previous marker Z")

	def script_siteMarker_autoClick_j(self, gesture):
		_autoClickAction(self, gesture, "alt+j", plugin)
	script_siteMarker_autoClick_j.__doc__ = _("Auto click on next marker assigned to Alt+J")

	def script_siteMarker_autoClick_c(self, gesture):
		_autoClickAction(self, gesture, "alt+c", plugin)
	script_siteMarker_autoClick_c.__doc__ = _("Auto click on next marker assigned to Alt+C")

	def script_siteMarker_autoClick_x(self, gesture):
		_autoClickAction(self, gesture, "alt+x", plugin)
	script_siteMarker_autoClick_x.__doc__ = _("Auto click on next marker assigned to Alt+X")

	def script_siteMarker_autoClick_z(self, gesture):
		_autoClickAction(self, gesture, "alt+z", plugin)
	script_siteMarker_autoClick_z.__doc__ = _("Auto click on next marker assigned to Alt+Z")

	setattr(cls, "script_siteMarker_jump_j", script_siteMarker_jump_j)
	setattr(cls, "script_siteMarker_jump_j_back", script_siteMarker_jump_j_back)
	setattr(cls, "script_siteMarker_jump_f", script_siteMarker_jump_f)
	setattr(cls, "script_siteMarker_jump_f_back", script_siteMarker_jump_f_back)
	setattr(cls, "script_siteMarker_jump_d", script_siteMarker_jump_d)
	setattr(cls, "script_siteMarker_jump_d_back", script_siteMarker_jump_d_back)
	setattr(cls, "script_siteMarker_jump_z", script_siteMarker_jump_z)
	setattr(cls, "script_siteMarker_jump_z_back", script_siteMarker_jump_z_back)
	setattr(cls, "script_siteMarker_autoClick_j", script_siteMarker_autoClick_j)
	setattr(cls, "script_siteMarker_autoClick_c", script_siteMarker_autoClick_c)
	setattr(cls, "script_siteMarker_autoClick_x", script_siteMarker_autoClick_x)
	setattr(cls, "script_siteMarker_autoClick_z", script_siteMarker_autoClick_z)

def _jump(treeInterceptor, gesture, baseKey, plugin, isShift=False):
	if getattr(plugin, "isInEditableContext", lambda: False)():
		gesture.send()
		return

	if not plugin.isInBrowserContext():
		gesture.send()
		return

	direction = -1 if isShift else 1
	plugin.refreshActiveLayout(force=False)

	if not plugin.activeSiteMarkers or baseKey not in plugin.activeSiteMarkers:
		gesture.send()
		return

	markerDataList = plugin.activeSiteMarkers[baseKey]
	focusTarget = getattr(treeInterceptor, "rootNVDAObject", None) or plugin.getRealWebFocusObject()
	
	if not focusTarget:
		gesture.send()
		return

	if isShift:
		try:
			currentCaret = treeInterceptor.makeTextInfo(textInfos.POSITION_CARET)
			backupCaret = currentCaret.copy()
			backupCaret.move("character", -1)
			backupCaret.select()
		except Exception:
			pass

	result = plugin.engine.jumpToMarker(markerDataList, focusTarget, direction=direction)

	if not result:
		if isShift:
			try:
				restoreCaret = treeInterceptor.makeTextInfo(textInfos.POSITION_CARET)
				restoreCaret.move("character", 1)
				restoreCaret.select()
			except Exception:
				pass
		gesture.send()

def _autoClickAction(treeInterceptor, gesture, keystroke, plugin):
	if not plugin.isInBrowserContext():
		gesture.send()
		return

	plugin.refreshActiveLayout(force=False)
	
	if not plugin.activeSiteMarkers or keystroke not in plugin.activeSiteMarkers:
		gesture.send()
		return

	clickMarkers = [m for m in plugin.activeSiteMarkers[keystroke] if m.get("actionMode") == "autoClick"]
	if not clickMarkers:
		gesture.send()
		return

	plugin._handleAutoClick(gesture, keystroke)