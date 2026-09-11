# markerAction.py

import logHandler
import api
import core
import UIAHandler
import winUser


class MarkerActionMixin:

	def _performRobustClick(self, targetObj):
		"""
		Multi-layered click mechanism for stubborn buttons (e.g. YouTube skip ad).
		Tries IA2 doAction, then UIA Invoke pattern, finally physical mouse click.
		Physical click is deferred via core.callLater to avoid main thread blockage.
		"""
		if not targetObj:
			return False

		# 1. Standard IA2 action
		try:
			if hasattr(targetObj, 'doAction'):
				targetObj.doAction()
				return True
		except Exception as e:
			logHandler.log.debug(f"SiteMarker: IA2 doAction failed: {e}")

		# 2. UIA Invoke Pattern (more reliable for modern web apps)
		try:
			if hasattr(targetObj, 'UIAElement'):
				invokePattern = targetObj.UIAElement.GetCurrentPattern(UIAHandler.UIA_InvokePatternId)
				if invokePattern:
					invokePattern.Invoke()
					return True
		except Exception as e:
			logHandler.log.debug(f"SiteMarker: UIA Invoke failed: {e}")

		# 3. Physical mouse routing as last resort (deferred to prevent blocking)
		try:
			api.setNavigatorObject(targetObj)
			left, top, width, height = targetObj.location
			if width > 0 and height > 0:
				targetX = left + (width // 2)
				targetY = top + (height // 2)
				winUser.setCursorPos(targetX, targetY)
				core.callLater(50, lambda: winUser.mouse_event(winUser.MOUSEEVENTF_LEFTDOWN, 0, 0, None, None))
				core.callLater(100, lambda: winUser.mouse_event(winUser.MOUSEEVENTF_LEFTUP, 0, 0, None, None))
				return True
		except Exception as e:
			logHandler.log.debug(f"SiteMarker: Physical mouse routing failed: {e}")

		return False

