# customShortcuts.py

import json
import os
import wx
from gui import guiHelper
import globalVars
import logHandler
import addonHandler
from .constants import DEFAULT_CUSTOMIZABLE_GESTURES, CUSTOMIZABLE_ACTION_ORDER

addonHandler.initTranslation()
_translate = _

CONFIG_DIR_NAME = "SiteMarker"
CONFIG_FILE_NAME = "customGestures.json"


def _getConfigFilePath():
	# Single segment beyond the add-on's own root (ChaiChaimee\SiteMarker\...),
	# consistent with every other add-on data path in this ecosystem (5.12).
	configDir = os.path.join(globalVars.appArgs.configPath, "ChaiChaimee", CONFIG_DIR_NAME)
	try:
		os.makedirs(configDir, exist_ok=True)
	except OSError as e:
		logHandler.log.error(f"SiteMarker: could not create config directory {configDir}: {e}")
	return os.path.join(configDir, CONFIG_FILE_NAME)


def loadCustomGestures():
	"""Returns a dict of {scriptName: gestureIdentifier} for every script the
	user has explicitly overridden. A script with no entry here uses its
	DEFAULT_CUSTOMIZABLE_GESTURES value. Never raises -- a missing or corrupt
	file is treated the same as "no overrides yet" rather than blocking
	startup."""
	path = _getConfigFilePath()
	if not os.path.isfile(path):
		return {}
	try:
		with open(path, "r", encoding="utf-8") as f:
			data = json.load(f)
	except (OSError, ValueError) as e:
		logHandler.log.error(f"SiteMarker: failed to read custom gestures config, using defaults: {e}")
		return {}
	if not isinstance(data, dict):
		return {}
	# Only keep entries for scripts we actually still know about, and only
	# string values -- a hand-edited or partially-corrupt file shouldn't be
	# able to inject an unexpected gesture identifier or object.
	return {
		scriptName: gestureId
		for scriptName, gestureId in data.items()
		if scriptName in DEFAULT_CUSTOMIZABLE_GESTURES and isinstance(gestureId, str) and gestureId
	}


def saveCustomGestures(overrides):
	"""Atomically writes {scriptName: gestureIdentifier} overrides to disk.
	Only entries that actually differ from the factory default are kept, so
	resetting a single action back to default removes it from the file
	entirely instead of just duplicating the default value."""
	path = _getConfigFilePath()
	toSave = {
		scriptName: gestureId
		for scriptName, gestureId in overrides.items()
		if scriptName in DEFAULT_CUSTOMIZABLE_GESTURES
		and gestureId
		and gestureId != DEFAULT_CUSTOMIZABLE_GESTURES.get(scriptName)
	}
	tempPath = path + ".tmp"
	try:
		with open(tempPath, "w", encoding="utf-8") as f:
			json.dump(toSave, f, ensure_ascii=True, indent=2)
			f.flush()
			os.fsync(f.fileno())
		os.replace(tempPath, path)
		return True
	except OSError as e:
		logHandler.log.error(f"SiteMarker: failed to save custom gestures config: {e}")
		try:
			if os.path.isfile(tempPath):
				os.remove(tempPath)
		except OSError:
			pass
		return False


def buildEffectiveGestureMap():
	"""Returns {gestureIdentifier: scriptName}, ready to pass to
	GlobalPlugin.bindGestures(). Built fresh from defaults + saved overrides
	every time it's called (rather than cached) so a change saved from the
	dialog is picked up the moment the caller rebuilds bindings, with no
	separate cache-invalidation step to forget."""
	overrides = loadCustomGestures()
	effective = {}
	for scriptName in CUSTOMIZABLE_ACTION_ORDER:
		gestureId = overrides.get(scriptName, DEFAULT_CUSTOMIZABLE_GESTURES[scriptName])
		effective[gestureId] = scriptName
	return effective


# ------------------------------------------------------------------
# wx key event -> NVDA "kb:" gesture identifier
# ------------------------------------------------------------------
# Only ordinary keyboard gestures are supported here (this add-on has never
# used touch or braille input gestures), so a plain wx key-event mapping is
# sufficient and avoids depending on NVDA's own lower-level keyboard hook
# just to capture one key combination for display in a dialog.
_SPECIAL_KEY_NAMES = {
	wx.WXK_ESCAPE: "escape",
	wx.WXK_TAB: "tab",
	wx.WXK_RETURN: "enter",
	wx.WXK_SPACE: "space",
	wx.WXK_BACK: "backspace",
	wx.WXK_DELETE: "delete",
	wx.WXK_INSERT: "insert",
	wx.WXK_HOME: "home",
	wx.WXK_END: "end",
	wx.WXK_PAGEUP: "pageUp",
	wx.WXK_PAGEDOWN: "pageDown",
	wx.WXK_UP: "upArrow",
	wx.WXK_DOWN: "downArrow",
	wx.WXK_LEFT: "leftArrow",
	wx.WXK_RIGHT: "rightArrow",
	wx.WXK_NUMPAD_ENTER: "enter",
}
for _n in range(1, 25):
	_SPECIAL_KEY_NAMES[getattr(wx, f"WXK_F{_n}")] = f"f{_n}"


def gestureIdentifierFromKeyEvent(evt):
	"""Converts a wx key event into an NVDA "kb:" gesture identifier string,
	or returns None if the key alone isn't a usable gesture (a bare modifier
	key with nothing else held, for instance)."""
	keyCode = evt.GetKeyCode()
	if keyCode in (wx.WXK_SHIFT, wx.WXK_CONTROL, wx.WXK_ALT, wx.WXK_WINDOWS_LEFT, wx.WXK_WINDOWS_RIGHT):
		return None
	parts = []
	if evt.ControlDown():
		parts.append("control")
	if evt.AltDown():
		parts.append("alt")
	if evt.ShiftDown():
		parts.append("shift")
	if evt.CmdDown() and not evt.ControlDown():
		# On Windows wx maps CmdDown() to the Windows key when RawControlDown
		# isn't in play; GetModifiers()-based WXK_WINDOWS_* handling above
		# already covers the bare-modifier case, this covers it held with
		# another key.
		parts.append("windows")
	if keyCode in _SPECIAL_KEY_NAMES:
		keyName = _SPECIAL_KEY_NAMES[keyCode]
	elif ord("0") <= keyCode <= ord("9") or ord("A") <= keyCode <= ord("Z"):
		keyName = chr(keyCode).lower()
	else:
		return None
	parts.append(keyName)
	return "kb:" + "+".join(parts)


def describeGestureIdentifier(gestureId):
	"""Best-effort human-readable form of a "kb:..." identifier (e.g.
	"kb:shift+j" -> "shift+j"), for display or logging. Falls back to the
	raw identifier if it isn't in the expected "kb:" form."""
	if gestureId and gestureId.startswith("kb:"):
		return gestureId[3:]
	return gestureId or _translate("(not set)")


# ------------------------------------------------------------------
# GUI
# ------------------------------------------------------------------
class GestureCaptureDialog(wx.Dialog):
	"""Small modal dialog that captures a single keyboard gesture."""

	def __init__(self, parent, actionLabel):
		# Translators: title of the dialog used to capture a new shortcut key.
		super().__init__(parent, title=_translate("Assign Shortcut"))
		self.capturedGesture = None

		mainSizer = wx.BoxSizer(wx.VERTICAL)
		sHelper = guiHelper.BoxSizerHelper(self, orientation=wx.VERTICAL)
		promptText = _translate("Press the key combination for:\n{action}\n\nPress Escape to cancel.").format(
			action=actionLabel
		)
		sHelper.addItem(wx.StaticText(self, label=promptText))
		self.statusText = sHelper.addItem(wx.StaticText(self, label=_translate("Waiting for a key press...")))
		mainSizer.Add(sHelper.sizer, border=guiHelper.BORDER_FOR_DIALOGS, flag=wx.ALL)
		self.SetSizerAndFit(mainSizer)
		self.CenterOnScreen()

		self.Bind(wx.EVT_CHAR_HOOK, self.onCharHook)

	def onCharHook(self, evt):
		keyCode = evt.GetKeyCode()
		if keyCode == wx.WXK_ESCAPE:
			self.EndModal(wx.ID_CANCEL)
			return
		gestureId = gestureIdentifierFromKeyEvent(evt)
		if gestureId is None:
			# A bare modifier key on its own -- keep waiting rather than
			# treating it as a completed (and unusable) capture.
			return
		self.capturedGesture = gestureId
		self.EndModal(wx.ID_OK)

