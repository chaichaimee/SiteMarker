# browseModeGestures.py

import addonHandler

addonHandler.initTranslation()


def registerGestures(plugin):
	# NOTE: Jump (j/f/d/z) and auto click (alt+j/c/x/z) gestures are already bound
	# and fully handled by GlobalPlugin.__gestures in __init__.py, which dispatches
	# to the async chunked scan pipeline (_handleGlobalJump / _processJumpChunk /
	# _handleAutoClick / _processAutoClickChunk). That pipeline does proper
	# COM-error handling, combobox detection, buffer priming and tab-identity
	# checks.
	#
	# This module used to ALSO monkey patch browseMode.BrowseModeTreeInterceptor
	# directly (a shared NVDA-wide class, not something owned by this add-on) to
	# bind a second, older copy of these same gestures to a helper function that
	# called self.engine.jumpToMarker(...), a method that does not exist on
	# MarkerEngine. That patch was never reverted in terminate(), so it stayed
	# active for every browse-mode document, in every app, for the lifetime of
	# the NVDA session (or until a full restart), which is both an unsafe,
	# uncleaned extension of NVDA core state and a source of duplicate/competing
	# gesture handling. It has been removed; this function is now an intentional
	# no-op kept only so existing callers/imports keep working.
	return
