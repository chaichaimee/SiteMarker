# __init__.py
# Copyright (C) 2026 Chai Chaimee
# Licensed under GNU General Public License. See COPYING.txt for details.

import wx
from enum import Enum
import addonHandler

addonHandler.initTranslation()
_translate = _

import globalPluginHandler
import controlTypes
import browseMode
import logHandler
from .markerEngine import MarkerEngine
from .gui import MarkerEditDialog, SiteManagerDialog, MarkerManagerDialog, AddSiteDialog
from . import browseModeGestures
from .scanEngine import ScanEngineMixin
from .contextHelpers import ContextHelpersMixin
from .markerAction import MarkerActionMixin

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
		self.lastJumpInfo = {}
		self._jumpComboboxResumeCount = {}
		self._autoClickComboboxResumeCount = {}
		self._recentScanActivity = {}
		self._autoClickRetryState = None
		self._refreshPending = False

		self._autoClickScanToken = {}
		self._jumpScanToken = {}
		self._sortScanToken = None
		self._pendingReveals = {}

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
		if hasattr(self.engine, "cleanUp"):
			self.engine.cleanUp()

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
