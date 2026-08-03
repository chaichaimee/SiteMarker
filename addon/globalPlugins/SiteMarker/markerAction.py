# markerAction.py

import re
import time
import wx
import api
import ui
import core
import logHandler
import textInfos
from .constants import TAP_THRESHOLD
from .gui import MarkerEditDialog, MarkerManagerDialog, SiteManagerDialog, AddSiteDialog

_translate = _

class MarkerActionMixin:
	def script_handleSiteMarkerAction(self, gesture):
		if not self._isInBrowser():
			gesture.send()
			return

		currentTime = time.time()
		if currentTime - self.lastTapTime > TAP_THRESHOLD:
			self.tapCount = 0
		self.tapCount += 1
		self.lastTapTime = currentTime

		if self._multiTapTimer is not None:
			self._multiTapTimer.Stop()
			self._multiTapTimer = None

		def dispatchAction():
			myTapCount = self.tapCount
			self.tapCount = 0

			import gui as nvdaGui
			currentUrl = self.getBrowserUrl()

			if myTapCount == 1:
				if not currentUrl:
					ui.message(_translate("Cannot capture browser URL."))
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
								dlg = MarkerEditDialog(nvdaGui.mainFrame, existingMarker, initialText=paraText)
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
								dlg = MarkerManagerDialog(nvdaGui.mainFrame, self.engine, siteName, siteConfig,
														  webFocusObj, autoAddMarker=True, currentUrl=currentUrl)
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

			elif myTapCount == 2:
				if not currentUrl:
					ui.message(_translate("Cannot capture browser URL."))
					return
				siteName, siteConfig = self.getCurrentSiteConfig()
				currentSiteName = siteName if siteName else None
				def openSiteDialog():
					dlg = None
					try:
						if currentSiteName and siteConfig:
							dlg = SiteManagerDialog(nvdaGui.mainFrame, self.engine, currentUrl, selectedSiteName=currentSiteName)
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

			elif myTapCount >= 3:
				if not currentUrl:
					ui.message(_translate("Cannot capture browser URL."))
					return
				siteName, siteConfig = self.getCurrentSiteConfig()
				if not siteName or not siteConfig:
					ui.message(_translate("No site configuration found. Add a site first."))
					return
				def openMarkerManagerTriple():
					dlg = None
					try:
						dlg = MarkerManagerDialog(nvdaGui.mainFrame, self.engine, siteName, siteConfig, None,
												  currentUrl=currentUrl)
						dlg.Raise()
						dlg.ShowModal()
					except Exception as e:
						logHandler.log.error(f"Triple-tap marker manager error: {e}")
					finally:
						if dlg:
							try:
								dlg.Destroy()
							except RuntimeError:
								pass
						self.refreshActiveLayout(force=True)
				wx.CallAfter(openMarkerManagerTriple)

		self._multiTapTimer = core.callLater(int(TAP_THRESHOLD * 1000), dispatchAction)
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
