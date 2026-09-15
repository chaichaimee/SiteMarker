# gui.py

import wx
import addonHandler
import ui
import re
import api
import textInfos
import tones
import logHandler

addonHandler.initTranslation()

class SiteManagerDialog(wx.Dialog):
	def __init__(self, parent, engine, currentUrl=None, selectedSiteName=None):
		super().__init__(parent, title=_("Site Manager"), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
		self.engine = engine
		self.currentUrl = currentUrl
		self.selectedSiteName = selectedSiteName
		self.siteNames = []
		self._initUI()
		self.loadSiteList()
		self.CentreOnParent()
		self.Raise()

	def _initUI(self):
		mainSizer = wx.BoxSizer(wx.VERTICAL)
		lblTitle = wx.StaticText(self, label=_("&Registered Sites:"))
		mainSizer.Add(lblTitle, 0, wx.ALL, 5)
		self.sitesListCtrl = wx.ListBox(self, style=wx.LB_SINGLE)
		mainSizer.Add(self.sitesListCtrl, 1, wx.EXPAND | wx.ALL, 5)
		btnSizer = wx.BoxSizer(wx.HORIZONTAL)
		self.btnAdd = wx.Button(self, label=_("&Add New Site"))
		self.btnEdit = wx.Button(self, label=_("&Edit Site"))
		self.btnDelete = wx.Button(self, label=_("&Delete Site"))
		self.btnManageMarkers = wx.Button(self, label=_("&Manage Markers"))
		self.btnClose = wx.Button(self, wx.ID_CANCEL, label=_("Close"))
		btnSizer.Add(self.btnAdd, 0, wx.ALL, 5)
		btnSizer.Add(self.btnEdit, 0, wx.ALL, 5)
		btnSizer.Add(self.btnDelete, 0, wx.ALL, 5)
		btnSizer.Add(self.btnManageMarkers, 0, wx.ALL, 5)
		btnSizer.Add(self.btnClose, 0, wx.ALL, 5)
		mainSizer.Add(btnSizer, 0, wx.ALIGN_CENTER | wx.ALL, 5)
		self.SetSizer(mainSizer)
		self.SetMinSize((500, 400))
		self.Fit()
		self.btnAdd.Bind(wx.EVT_BUTTON, self.onAddSite)
		self.btnEdit.Bind(wx.EVT_BUTTON, self.onEditSite)
		self.btnDelete.Bind(wx.EVT_BUTTON, self.onDeleteSite)
		self.btnManageMarkers.Bind(wx.EVT_BUTTON, self.onManageMarkers)
		self.btnClose.Bind(wx.EVT_BUTTON, self.onClose)
		self.Bind(wx.EVT_CLOSE, self.onClose)
		self.sitesListCtrl.Bind(wx.EVT_KEY_DOWN, self.onListKeyDown)
		self.sitesListCtrl.Bind(wx.EVT_CONTEXT_MENU, self.onContextMenu)
		self.SetEscapeId(self.btnClose.GetId())

	def loadSiteList(self):
		self.sitesListCtrl.Clear()
		self.siteNames = sorted(self.engine.getAllSiteNames(), key=lambda s: s.lower())
		selectedIndex = -1
		for idx, siteName in enumerate(self.siteNames):
			siteConfig = self.engine.getSiteConfig(siteName)
			displayName = siteConfig.get("displayName", siteName)
			self.sitesListCtrl.Append(displayName)
			if self.selectedSiteName and siteName == self.selectedSiteName:
				selectedIndex = idx
		if selectedIndex >= 0:
			self.sitesListCtrl.SetSelection(selectedIndex)
		elif self.siteNames:
			self.sitesListCtrl.SetSelection(0)

	def getSelectedSiteIndex(self):
		selection = self.sitesListCtrl.GetSelection()
		if selection == wx.NOT_FOUND:
			return None
		return selection

	def getSelectedSiteName(self):
		index = self.getSelectedSiteIndex()
		if index is None or index >= len(self.siteNames):
			return None
		return self.siteNames[index]

	def onAddSite(self, event):
		dialogObj = None
		try:
			dialogObj = AddSiteDialog(self, self.engine, self.currentUrl)
			dialogObj.Raise()
			if dialogObj.ShowModal() == wx.ID_OK:
				self.loadSiteList()
		except Exception as e:
			logHandler.log.error(f"SiteMarker: Add Site dialog error: {e}")
			wx.MessageBox(_("Could not open the Add Site dialog."), _("Error"), wx.OK | wx.ICON_ERROR)
		finally:
			if dialogObj:
				try:
					dialogObj.Destroy()
				except RuntimeError:
					pass

	def onEditSite(self, event):
		siteName = self.getSelectedSiteName()
		if siteName is None:
			wx.MessageBox(_("Please select a site first."), _("Information"), wx.OK | wx.ICON_INFORMATION)
			return
		siteConfig = self.engine.getSiteConfig(siteName)
		dialogObj = None
		try:
			dialogObj = EditSiteDialog(self, self.engine, siteName, siteConfig)
			dialogObj.Raise()
			if dialogObj.ShowModal() == wx.ID_OK:
				self.loadSiteList()
		except Exception as e:
			logHandler.log.error(f"SiteMarker: Edit Site dialog error: {e}")
			wx.MessageBox(_("Could not open the Edit Site dialog."), _("Error"), wx.OK | wx.ICON_ERROR)
		finally:
			if dialogObj:
				try:
					dialogObj.Destroy()
				except RuntimeError:
					pass

	def onDeleteSite(self, event):
		siteName = self.getSelectedSiteName()
		if siteName is None:
			wx.MessageBox(_("Please select a site first."), _("Information"), wx.OK | wx.ICON_INFORMATION)
			return
		if wx.MessageBox(_("Are you sure you want to delete this site and all its markers?"), _("Confirm"), wx.YES_NO | wx.ICON_QUESTION) == wx.YES:
			if self.engine.deleteSiteConfiguration(siteName):
				ui.message(_("Site deleted successfully."))
				self.loadSiteList()
			else:
				wx.MessageBox(_("Failed to delete site."), _("Error"), wx.OK | wx.ICON_ERROR)

	def onManageMarkers(self, event):
		siteName = self.getSelectedSiteName()
		if siteName is None:
			wx.MessageBox(_("Please select a site first."), _("Information"), wx.OK | wx.ICON_INFORMATION)
			return
		siteConfig = self.engine.getSiteConfig(siteName)
		dialogObj = None
		try:
			dialogObj = MarkerManagerDialog(self, self.engine, siteName, siteConfig, None, self)
			dialogObj.Raise()
			if dialogObj.ShowModal() == wx.ID_OK:
				self.loadSiteList()
		except Exception as e:
			logHandler.log.error(f"SiteMarker: Marker Manager dialog error: {e}")
			wx.MessageBox(_("Could not open the Marker Manager dialog."), _("Error"), wx.OK | wx.ICON_ERROR)
		finally:
			if dialogObj:
				try:
					dialogObj.Destroy()
				except RuntimeError:
					pass

	def onClose(self, event):
		self.Destroy()

	def onListKeyDown(self, event):
		keyCode = event.GetKeyCode()
		if keyCode == wx.WXK_DELETE:
			self.onDeleteSite(None)
		elif keyCode == wx.WXK_F2:
			self.onEditSite(None)
		else:
			event.Skip()

	def onContextMenu(self, event):
		selectedIndex = self.getSelectedSiteIndex()
		contextMenu = wx.Menu()
		editItem = contextMenu.Append(wx.ID_ANY, _("&Edit Site\tF2"))
		self.Bind(wx.EVT_MENU, lambda e: self.onEditSite(None), editItem)
		deleteItem = contextMenu.Append(wx.ID_ANY, _("&Delete Site\tDel"))
		self.Bind(wx.EVT_MENU, lambda e: self.onDeleteSite(None), deleteItem)
		contextMenu.AppendSeparator()
		markersItem = contextMenu.Append(wx.ID_ANY, _("&Manage Markers"))
		self.Bind(wx.EVT_MENU, lambda e: self.onManageMarkers(None), markersItem)
		if selectedIndex is None:
			editItem.Enable(False)
			deleteItem.Enable(False)
			markersItem.Enable(False)
		self.sitesListCtrl.PopupMenu(contextMenu, event.GetPosition())
		contextMenu.Destroy()

class AddSiteDialog(wx.Dialog):
	def __init__(self, parent, engine, currentUrl):
		super().__init__(parent, title=_("Add Site Configuration"), style=wx.DEFAULT_DIALOG_STYLE)
		self.engine = engine
		self.currentUrl = currentUrl
		self._initUI()
		self.CentreOnParent()
		self.Raise()

	def _initUI(self):
		mainSizer = wx.BoxSizer(wx.VERTICAL)
		mainSizer.Add(wx.StaticText(self, label=_("Display Name:")), 0, wx.ALL, 5)
		self.txtDisplayName = wx.TextCtrl(self, value="")
		mainSizer.Add(self.txtDisplayName, 0, wx.EXPAND | wx.ALL, 5)
		mainSizer.Add(wx.StaticText(self, label=_("URL Pattern:")), 0, wx.ALL, 5)
		extractedDomain = self.currentUrl.split("//")[-1].split("/")[0] if self.currentUrl else ""
		self.txtUrl = wx.TextCtrl(self, value=extractedDomain)
		mainSizer.Add(self.txtUrl, 0, wx.EXPAND | wx.ALL, 5)
		mainSizer.Add(wx.StaticText(self, label=_("Match Type:")), 0, wx.ALL, 5)
		optionsShortList = [_("Domain Only"), _("Include Subdomains"), _("Contain Substring"), _("Exact Matching"), _("Regular Expression")]
		self.cmbMatchType = wx.ComboBox(self, choices=optionsShortList, style=wx.CB_READONLY)
		self.cmbMatchType.SetSelection(0)
		mainSizer.Add(self.cmbMatchType, 0, wx.EXPAND | wx.ALL, 5)
		# --- Add Focus Mode ComboBox ---
		mainSizer.Add(wx.StaticText(self, label=_("Focus Mode:")), 0, wx.ALL, 5)
		focusModeChoices = [_("Normal"), _("Don't enter form mode"), _("Disable focus")]
		self.cmbFocusMode = wx.ComboBox(self, choices=focusModeChoices, style=wx.CB_READONLY)
		self.cmbFocusMode.SetSelection(0)  # Default Normal
		mainSizer.Add(self.cmbFocusMode, 0, wx.EXPAND | wx.ALL, 5)
		# --- End Focus Mode ---
		# --- Add Skip Blank Lines checkbox ---
		self.chkSkipBlankLines = wx.CheckBox(
			self, label=_("Skip blank lines when navigating with Up/Down and Ctrl+Up/Down")
		)
		self.chkSkipBlankLines.SetValue(False)
		mainSizer.Add(self.chkSkipBlankLines, 0, wx.ALL, 5)
		# --- End Skip Blank Lines ---
		btnSizer = wx.BoxSizer(wx.HORIZONTAL)
		self.btnSave = wx.Button(self, wx.ID_OK, label=_("Save Site"))
		self.btnCancel = wx.Button(self, id=wx.ID_CANCEL, label=_("Cancel"))
		btnSizer.Add(self.btnSave, 0, wx.ALL, 5)
		btnSizer.Add(self.btnCancel, 0, wx.ALL, 5)
		mainSizer.Add(btnSizer, 0, wx.ALIGN_CENTER | wx.ALL, 5)
		self.SetSizer(mainSizer)
		self.Fit()
		self.btnSave.Bind(wx.EVT_BUTTON, self.onSave)
		self.SetEscapeId(self.btnCancel.GetId())
		self.txtDisplayName.SetFocus()

	def onSave(self, event):
		displayNameValue = self.txtDisplayName.GetValue().strip()
		patternValue = self.txtUrl.GetValue().strip()
		if not displayNameValue:
			wx.MessageBox(_("Display Name cannot be empty."), _("Error"), wx.OK | wx.ICON_ERROR)
			return
		if not patternValue:
			wx.MessageBox(_("URL Pattern cannot be empty."), _("Error"), wx.OK | wx.ICON_ERROR)
			return
		cleanName = re.sub(r'[\\/*?:"<>|]', "_", displayNameValue)
		siteConfigData = {
			"displayName": displayNameValue,
			"pattern": patternValue,
			"matchType": self.cmbMatchType.GetSelection(),
			"focusMode": self.cmbFocusMode.GetSelection(),  # Save focusMode value
			"skipBlankLines": self.chkSkipBlankLines.GetValue(),
			"markers": []
		}
		if not self.engine.saveSiteConfiguration(cleanName, siteConfigData):
			wx.MessageBox(
				_("Failed to save the site configuration to disk. Check that the configuration folder is writable."),
				_("Error"), wx.OK | wx.ICON_ERROR
			)
			return
		ui.message(_("Site created successfully."))
		self.EndModal(wx.ID_OK)

class EditSiteDialog(wx.Dialog):
	def __init__(self, parent, engine, siteName, siteConfig):
		super().__init__(parent, title=_("Edit Site Configuration"), style=wx.DEFAULT_DIALOG_STYLE)
		self.engine = engine
		self.siteName = siteName
		self.siteConfig = siteConfig.copy()
		self._initUI()
		self.CentreOnParent()
		self.Raise()

	def _initUI(self):
		mainSizer = wx.BoxSizer(wx.VERTICAL)
		mainSizer.Add(wx.StaticText(self, label=_("Display Name:")), 0, wx.ALL, 5)
		self.txtDisplayName = wx.TextCtrl(self, value=self.siteConfig.get("displayName", self.siteName))
		mainSizer.Add(self.txtDisplayName, 0, wx.EXPAND | wx.ALL, 5)
		mainSizer.Add(wx.StaticText(self, label=_("URL Pattern:")), 0, wx.ALL, 5)
		self.txtUrl = wx.TextCtrl(self, value=self.siteConfig.get("pattern", ""))
		mainSizer.Add(self.txtUrl, 0, wx.EXPAND | wx.ALL, 5)
		mainSizer.Add(wx.StaticText(self, label=_("Match Type:")), 0, wx.ALL, 5)
		optionsShortList = [_("Domain Only"), _("Include Subdomains"), _("Contain Substring"), _("Exact Matching"), _("Regular Expression")]
		self.cmbMatchType = wx.ComboBox(self, choices=optionsShortList, style=wx.CB_READONLY)
		self.cmbMatchType.SetSelection(self.siteConfig.get("matchType", 0))
		mainSizer.Add(self.cmbMatchType, 0, wx.EXPAND | wx.ALL, 5)
		# --- Add Focus Mode ComboBox ---
		mainSizer.Add(wx.StaticText(self, label=_("Focus Mode:")), 0, wx.ALL, 5)
		focusModeChoices = [_("Normal"), _("Don't enter form mode"), _("Disable focus")]
		self.cmbFocusMode = wx.ComboBox(self, choices=focusModeChoices, style=wx.CB_READONLY)
		currentFocusMode = self.siteConfig.get("focusMode", 0)
		self.cmbFocusMode.SetSelection(currentFocusMode if 0 <= currentFocusMode <= 2 else 0)
		mainSizer.Add(self.cmbFocusMode, 0, wx.EXPAND | wx.ALL, 5)
		# --- End Focus Mode ---
		# --- Add Skip Blank Lines checkbox ---
		self.chkSkipBlankLines = wx.CheckBox(
			self, label=_("Skip blank lines when navigating with Up/Down and Ctrl+Up/Down")
		)
		self.chkSkipBlankLines.SetValue(bool(self.siteConfig.get("skipBlankLines", False)))
		mainSizer.Add(self.chkSkipBlankLines, 0, wx.ALL, 5)
		# --- End Skip Blank Lines ---
		btnSizer = wx.BoxSizer(wx.HORIZONTAL)
		self.btnSave = wx.Button(self, wx.ID_OK, label=_("Save Changes"))
		self.btnCancel = wx.Button(self, id=wx.ID_CANCEL, label=_("Cancel"))
		btnSizer.Add(self.btnSave, 0, wx.ALL, 5)
		btnSizer.Add(self.btnCancel, 0, wx.ALL, 5)
		mainSizer.Add(btnSizer, 0, wx.ALIGN_CENTER | wx.ALL, 5)
		self.SetSizer(mainSizer)
		self.Fit()
		self.btnSave.Bind(wx.EVT_BUTTON, self.onSave)
		self.SetEscapeId(self.btnCancel.GetId())
		self.txtDisplayName.SetFocus()

	def onSave(self, event):
		displayNameValue = self.txtDisplayName.GetValue().strip()
		patternValue = self.txtUrl.GetValue().strip()
		if not displayNameValue:
			wx.MessageBox(_("Display Name cannot be empty."), _("Error"), wx.OK | wx.ICON_ERROR)
			return
		if not patternValue:
			wx.MessageBox(_("URL Pattern cannot be empty."), _("Error"), wx.OK | wx.ICON_ERROR)
			return
		newSiteName = re.sub(r'[\\/*?:"<>|]', "_", displayNameValue)
		updatedConfig = {
			"displayName": displayNameValue,
			"pattern": patternValue,
			"matchType": self.cmbMatchType.GetSelection(),
			"focusMode": self.cmbFocusMode.GetSelection(),  # Save focusMode value
			"skipBlankLines": self.chkSkipBlankLines.GetValue(),
			"markers": self.siteConfig.get("markers", [])
		}
		if newSiteName != self.siteName:
			self.engine.deleteSiteConfiguration(self.siteName)
		if not self.engine.saveSiteConfiguration(newSiteName, updatedConfig):
			wx.MessageBox(
				_("Failed to save the site configuration to disk. Check that the configuration folder is writable."),
				_("Error"), wx.OK | wx.ICON_ERROR
			)
			return
		ui.message(_("Site updated successfully."))
		self.EndModal(wx.ID_OK)

class MarkerManagerDialog(wx.Dialog):
	def __init__(self, parent, engine, siteName, siteConfig, webFocusObj=None,
				 parentDialog=None, highlightMarker=None, autoAddMarker=False,
				 currentUrl=None):
		super().__init__(parent, title=_("Marker Manager - ") + siteConfig.get("displayName", siteName),
						 style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
		self.engine = engine
		self.siteName = siteName
		self.siteConfig = siteConfig
		self.webFocusObj = webFocusObj
		self.parentDialog = parentDialog
		self.highlightMarker = highlightMarker
		self.autoAddMarker = autoAddMarker
		self.currentUrl = currentUrl
		self.markersData = siteConfig.get("markers", []).copy()
		self._initUI()
		self.loadMarkersList()
		self.CentreOnParent()
		self.Raise()
		if self.autoAddMarker:
			self.Bind(wx.EVT_SHOW, self.onShowAddMarker)
		elif self.highlightMarker:
			wx.CallAfter(self._highlightMarker)

	def onShowAddMarker(self, event):
		if event.IsShown() and self.autoAddMarker:
			self.autoAddMarker = False
			wx.CallAfter(self.onAddMarker, None)
		event.Skip()

	def _highlightMarker(self):
		for idx, marker in enumerate(self.markersData):
			if marker.get("name") == self.highlightMarker.get("name") and marker.get("pattern") == self.highlightMarker.get("pattern"):
				self.markersListCtrl.SetSelection(idx)
				self.markersListCtrl.SetFocus()
				tones.beep(1000, 50)
				ui.message(_("Marker found: {}").format(marker.get("displayName", "Unnamed")))
				return

	def _initUI(self):
		mainSizer = wx.BoxSizer(wx.VERTICAL)
		lblTitle = wx.StaticText(self, label=_("&Markers List:"))
		mainSizer.Add(lblTitle, 0, wx.ALL, 5)
		self.markersListCtrl = wx.ListBox(self, style=wx.LB_SINGLE)
		mainSizer.Add(self.markersListCtrl, 1, wx.EXPAND | wx.ALL, 5)
		btnSizer = wx.BoxSizer(wx.HORIZONTAL)
		self.btnAdd = wx.Button(self, label=_("&Add Marker"))
		self.btnEdit = wx.Button(self, label=_("&Edit"))
		self.btnDelete = wx.Button(self, label=_("&Delete"))
		self.btnClearAll = wx.Button(self, label=_("Clear &All"))
		self.btnBackToSites = wx.Button(self, label=_("&Back to Site Manager"))
		self.btnClose = wx.Button(self, wx.ID_CANCEL, label=_("Close"))
		btnSizer.Add(self.btnAdd, 0, wx.ALL, 5)
		btnSizer.Add(self.btnEdit, 0, wx.ALL, 5)
		btnSizer.Add(self.btnDelete, 0, wx.ALL, 5)
		btnSizer.Add(self.btnClearAll, 0, wx.ALL, 5)
		btnSizer.Add(self.btnBackToSites, 0, wx.ALL, 5)
		btnSizer.Add(self.btnClose, 0, wx.ALL, 5)
		mainSizer.Add(btnSizer, 0, wx.ALIGN_CENTER | wx.ALL, 5)
		self.SetSizer(mainSizer)
		self.SetMinSize((500, 400))
		self.Fit()
		self.btnAdd.Bind(wx.EVT_BUTTON, self.onAddMarker)
		self.btnEdit.Bind(wx.EVT_BUTTON, self.onEditMarker)
		self.btnDelete.Bind(wx.EVT_BUTTON, self.onDeleteMarker)
		self.btnClearAll.Bind(wx.EVT_BUTTON, self.onClearAllMarkers)
		self.btnBackToSites.Bind(wx.EVT_BUTTON, self.onBackToSites)
		self.btnClose.Bind(wx.EVT_BUTTON, self.onClose)
		self.Bind(wx.EVT_CLOSE, self.onClose)
		self.markersListCtrl.Bind(wx.EVT_KEY_DOWN, self.onListKeyDown)
		self.markersListCtrl.Bind(wx.EVT_CONTEXT_MENU, self.onContextMenu)
		self.SetEscapeId(self.btnClose.GetId())

	def loadMarkersList(self):
		self.markersListCtrl.Clear()
		for markerItem in self.markersData:
			displayName = markerItem.get("displayName", "")
			pattern = markerItem.get("pattern", "")
			label = displayName if displayName else pattern
			if not label:
				label = _("Unnamed")
			self.markersListCtrl.Append(label)

	def saveMarkersToConfig(self):
		self.siteConfig["markers"] = self.markersData
		if self.engine.saveSiteConfiguration(self.siteName, self.siteConfig):
			return True
		wx.MessageBox(
			_("Failed to save marker changes to disk. Check that the configuration folder is writable."),
			_("Error"), wx.OK | wx.ICON_ERROR
		)
		return False

	def getSelectedMarkerIndex(self):
		selection = self.markersListCtrl.GetSelection()
		if selection == wx.NOT_FOUND:
			return None
		return selection

	def onAddMarker(self, event):
		dialogObj = None
		try:
			dialogObj = MarkerEditDialog(self, None, self.webFocusObj, self.siteConfig)
			dialogObj.Raise()
			if dialogObj.ShowModal() == wx.ID_OK:
				self.markersData.append(dialogObj.getMarkerData())
				if self.saveMarkersToConfig():
					self.loadMarkersList()
					self.markersListCtrl.SetSelection(len(self.markersData) - 1)
					self.markersListCtrl.SetFocus()
		except Exception as e:
			logHandler.log.error(f"SiteMarker: Add Marker dialog error: {e}")
			wx.MessageBox(_("Could not open the marker editor."), _("Error"), wx.OK | wx.ICON_ERROR)
		finally:
			if dialogObj:
				try:
					dialogObj.Destroy()
				except RuntimeError:
					pass

	def onEditMarker(self, event):
		selectedIndex = self.getSelectedMarkerIndex()
		if selectedIndex is None:
			wx.MessageBox(_("Please select a marker first."), _("Information"), wx.OK | wx.ICON_INFORMATION)
			return
		markerData = self.markersData[selectedIndex]
		dialogObj = None
		try:
			dialogObj = MarkerEditDialog(self, markerData, self.webFocusObj, self.siteConfig)
			dialogObj.Raise()
			if dialogObj.ShowModal() == wx.ID_OK:
				self.markersData[selectedIndex] = dialogObj.getMarkerData()
				if self.saveMarkersToConfig():
					self.loadMarkersList()
					self.markersListCtrl.SetSelection(selectedIndex)
					self.markersListCtrl.SetFocus()
		except Exception as e:
			logHandler.log.error(f"SiteMarker: Edit Marker dialog error: {e}")
			wx.MessageBox(_("Could not open the marker editor."), _("Error"), wx.OK | wx.ICON_ERROR)
		finally:
			if dialogObj:
				try:
					dialogObj.Destroy()
				except RuntimeError:
					pass

	def onDeleteMarker(self, event):
		selectedIndex = self.getSelectedMarkerIndex()
		if selectedIndex is None:
			wx.MessageBox(_("Please select a marker first."), _("Information"), wx.OK | wx.ICON_INFORMATION)
			return
		if wx.MessageBox(_("Are you sure you want to delete this marker?"), _("Confirm"), wx.YES_NO | wx.ICON_QUESTION) == wx.YES:
			removedMarker = self.markersData.pop(selectedIndex)
			if self.saveMarkersToConfig():
				self.loadMarkersList()
				if selectedIndex < len(self.markersData):
					self.markersListCtrl.SetSelection(selectedIndex)
				elif len(self.markersData) > 0:
					self.markersListCtrl.SetSelection(0)
			else:
				self.markersData.insert(selectedIndex, removedMarker)

	def onClearAllMarkers(self, event):
		if len(self.markersData) == 0:
			wx.MessageBox(_("No markers to clear."), _("Information"), wx.OK | wx.ICON_INFORMATION)
			return
		if wx.MessageBox(_("Are you sure you want to delete ALL markers?"), _("Confirm Delete All"), wx.YES_NO | wx.ICON_WARNING) == wx.YES:
			previousMarkers = self.markersData
			self.markersData = []
			if self.saveMarkersToConfig():
				self.loadMarkersList()
				ui.message(_("All markers have been cleared."))
			else:
				self.markersData = previousMarkers

	def onBackToSites(self, event):
		self.Destroy()
		if self.parentDialog:
			self.parentDialog.Show()
			self.parentDialog.Raise()
			return
		app = wx.GetApp()
		topWindow = app.GetTopWindow() if app else None
		dlg = None
		try:
			dlg = SiteManagerDialog(topWindow, self.engine, self.currentUrl)
			dlg.Raise()
			dlg.ShowModal()
		except Exception as e:
			logHandler.log.error(f"SiteMarker: failed to reopen Site Manager: {e}")
		finally:
			if dlg:
				try:
					dlg.Destroy()
				except RuntimeError:
					pass

	def onClose(self, event):
		self.Destroy()

	def onListKeyDown(self, event):
		keyCode = event.GetKeyCode()
		if keyCode == wx.WXK_DELETE:
			self.onDeleteMarker(None)
		elif keyCode == wx.WXK_F2:
			self.onEditMarker(None)
		else:
			event.Skip()

	def onContextMenu(self, event):
		selectedIndex = self.getSelectedMarkerIndex()
		contextMenu = wx.Menu()
		editItem = contextMenu.Append(wx.ID_ANY, _("&Edit Marker\tF2"))
		self.Bind(wx.EVT_MENU, lambda e: self.onEditMarker(None), editItem)
		deleteItem = contextMenu.Append(wx.ID_ANY, _("&Delete Marker\tDel"))
		self.Bind(wx.EVT_MENU, lambda e: self.onDeleteMarker(None), deleteItem)
		contextMenu.AppendSeparator()
		clearAllItem = contextMenu.Append(wx.ID_ANY, _("Clear &All Markers"))
		self.Bind(wx.EVT_MENU, lambda e: self.onClearAllMarkers(None), clearAllItem)
		if selectedIndex is None:
			editItem.Enable(False)
			deleteItem.Enable(False)
		self.markersListCtrl.PopupMenu(contextMenu, event.GetPosition())
		contextMenu.Destroy()

class MarkerEditDialog(wx.Dialog):
	def __init__(self, parent, markerData=None, webFocusObj=None, siteConfig=None, initialText=None):
		super().__init__(parent, title=_("Marker Properties"), style=wx.DEFAULT_DIALOG_STYLE)
		self.markerData = markerData or {}
		self.webFocusObj = webFocusObj
		self.siteConfig = siteConfig or {}
		self._initUI(initialText)
		self.CentreOnParent()
		self.Raise()

	def _getSelectedText(self, initialText):
		if initialText is not None:
			return initialText
		try:
			focusObj = api.getFocusObject()
			treeInt = getattr(self.webFocusObj, "treeInterceptor", None) or getattr(focusObj, "treeInterceptor", None)
			if treeInt:
				try:
					selection = treeInt.makeTextInfo(textInfos.POSITION_SELECTION)
					if selection and not selection.isCollapsed and selection.text.strip():
						return selection.text.strip()
				except Exception:
					pass
				try:
					caretPos = treeInt.makeTextInfo(textInfos.POSITION_CARET)
					if caretPos:
						caretPos.expand(textInfos.UNIT_PARAGRAPH)
						if caretPos.text and caretPos.text.strip():
							return caretPos.text.strip()
				except Exception:
					pass
			for obj in (focusObj, self.webFocusObj):
				if obj:
					name = getattr(obj, "name", "")
					if name and name.strip():
						return name.strip()
					value = getattr(obj, "value", "")
					if value and value.strip():
						return value.strip()
			return ""
		except Exception:
			return ""

	def _initUI(self, initialText=None):
		mainSizer = wx.BoxSizer(wx.VERTICAL)
		selectedText = self._getSelectedText(initialText)
		# Set (rather than left implicit) by _updateKeyChoices() below, once the
		# combo choices for the marker's current actionMode are known -- see
		# that method for how an existing marker's non-standard keystroke value
		# is recognised as a custom shortcut here.
		self._customKeystroke = None

		mainSizer.Add(wx.StaticText(self, label=_("Pattern:")), 0, wx.ALL, 5)
		self.txtPattern = wx.TextCtrl(self, value=self.markerData.get("pattern", selectedText))
		mainSizer.Add(self.txtPattern, 0, wx.EXPAND | wx.ALL, 5)

		mainSizer.Add(wx.StaticText(self, label=_("Pattern Match:")), 0, wx.ALL, 5)
		modeChoices = [_("Contains Text (find partial match)"), _("Exact Paragraph (match whole paragraph)"), _("Regex Match (use regular expression)")]
		self.cmbMatchMode = wx.ComboBox(self, choices=modeChoices, style=wx.CB_READONLY)
		self.cmbMatchMode.SetSelection(self.markerData.get("matchMode", 0))
		mainSizer.Add(self.cmbMatchMode, 0, wx.EXPAND | wx.ALL, 5)

		mainSizer.Add(wx.StaticText(self, label=_("Mode:")), 0, wx.ALL, 5)
		actionModes = [_("Jump"), _("Auto Click")]
		self.cmbActionMode = wx.ComboBox(self, choices=actionModes, style=wx.CB_READONLY)
		currentActionMode = self.markerData.get("actionMode", "jump")
		self.cmbActionMode.SetSelection(0 if currentActionMode == "jump" else 1)
		mainSizer.Add(self.cmbActionMode, 0, wx.EXPAND | wx.ALL, 5)
		self.cmbActionMode.Bind(wx.EVT_COMBOBOX, self.onActionModeChange)

		self.keySizer = wx.BoxSizer(wx.VERTICAL)
		self.lblKey = wx.StaticText(self, label="")
		self.cmbKey = wx.ComboBox(self, style=wx.CB_READONLY)
		self.keySizer.Add(self.lblKey, 0, wx.ALL, 5)
		self.keySizer.Add(self.cmbKey, 0, wx.EXPAND | wx.ALL, 5)
		self.cmbKey.Bind(wx.EVT_COMBOBOX, self.onStandardKeyChanged)
		customKeyBtnSizer = wx.BoxSizer(wx.HORIZONTAL)
		self.btnCustomShortcut = wx.Button(self, label=_("Custom &Shortcut..."))
		self.btnCustomShortcut.Bind(wx.EVT_BUTTON, self.onCustomShortcut)
		customKeyBtnSizer.Add(self.btnCustomShortcut, 0, wx.ALL, 5)
		self.btnClearCustomShortcut = wx.Button(self, label=_("&Use Standard Key"))
		self.btnClearCustomShortcut.Bind(wx.EVT_BUTTON, self.onClearCustomShortcut)
		customKeyBtnSizer.Add(self.btnClearCustomShortcut, 0, wx.ALL, 5)
		self.keySizer.Add(customKeyBtnSizer, 0, wx.ALL, 0)
		mainSizer.Add(self.keySizer, 0, wx.EXPAND | wx.ALL, 5)

		self.chkAutoTrigger = wx.CheckBox(self, label="")
		self.chkAutoTrigger.SetValue(bool(self.markerData.get("autoTrigger", False)))
		mainSizer.Add(self.chkAutoTrigger, 0, wx.ALL, 5)

		mainSizer.Add(wx.StaticText(self, label=_("Search Scope:")), 0, wx.ALL, 5)
		scopeChoices = [_("Entire Document"), _("Viewport Only")]
		self.cmbScope = wx.ComboBox(self, choices=scopeChoices, style=wx.CB_READONLY)
		currentScope = self.markerData.get("scope", "document")
		self.cmbScope.SetSelection(0 if currentScope == "document" else 1)
		mainSizer.Add(self.cmbScope, 0, wx.EXPAND | wx.ALL, 5)

		mainSizer.Add(wx.StaticText(self, label=_("Display Name:")), 0, wx.ALL, 5)
		self.txtDisplayName = wx.TextCtrl(self, value=self.markerData.get("displayName", selectedText))
		mainSizer.Add(self.txtDisplayName, 0, wx.EXPAND | wx.ALL, 5)

		mainSizer.Add(wx.StaticText(self, label=_("Offset (lines to move after match, same as arrow up/down):")), 0, wx.ALL, 5)
		self.spinOffset = wx.SpinCtrl(self, min=-50, max=50, initial=self.markerData.get("offset", 0))
		mainSizer.Add(self.spinOffset, 0, wx.EXPAND | wx.ALL, 5)

		btnSizer = wx.BoxSizer(wx.HORIZONTAL)
		self.btnSave = wx.Button(self, wx.ID_OK, label=_("OK"))
		self.btnCancel = wx.Button(self, id=wx.ID_CANCEL, label=_("Cancel"))
		btnSizer.Add(self.btnSave, 0, wx.ALL, 5)
		btnSizer.Add(self.btnCancel, 0, wx.ALL, 5)
		mainSizer.Add(btnSizer, 0, wx.ALIGN_CENTER | wx.ALL, 5)

		self.SetSizer(mainSizer)
		self.Fit()
		self.btnSave.Bind(wx.EVT_BUTTON, self.onSave)
		self.SetEscapeId(self.btnCancel.GetId())
		self.txtPattern.SetFocus()

		self._updateKeyChoices()

	def onActionModeChange(self, event):
		# A custom shortcut captured for one action mode (jump vs auto-click)
		# doesn't carry a meaningful interpretation in the other, so switching
		# modes clears it rather than silently keeping a key that made sense
		# only in the mode the user is leaving.
		self._customKeystroke = None
		self._updateKeyChoices()

	def _updateKeyChoices(self):
		actionMode = "jump" if self.cmbActionMode.GetSelection() == 0 else "autoClick"
		if actionMode == "jump":
			self.lblKey.SetLabel(_("Jump Key:"))
			keys = ["j"]
			self.chkAutoTrigger.SetLabel(_("Auto-jump on page load"))
		else:
			self.lblKey.SetLabel(_("Click Key:"))
			keys = ["alt+j"]
			self.chkAutoTrigger.SetLabel(_("Auto-click on page load"))
		self.cmbKey.SetItems(keys)
		currentKeystroke = self.markerData.get("keystroke", keys[0]).lower()
		if currentKeystroke in keys:
			self.cmbKey.SetSelection(keys.index(currentKeystroke))
		else:
			self.cmbKey.SetSelection(0)
			# An existing marker whose saved keystroke doesn't match any of
			# the standard choices for this action mode already has its own
			# custom shortcut (set via the button below in an earlier edit) --
			# keep it rather than silently reverting to the first standard key.
			if self.markerData.get("keystroke"):
				self._customKeystroke = currentKeystroke
		self._updateCustomKeyLabel()
		self.keySizer.Layout()

	def _currentDisplayedKey(self):
		# What the user would currently hear as "the key" for this marker,
		# used both to render the button label and to announce a
		# from-to confirmation when it changes -- covers all three states:
		# a custom shortcut, a standard key picked from the combo, or (should
		# the combo ever be genuinely empty) a safe fallback.
		if self._customKeystroke:
			return self._customKeystroke
		selected = self.cmbKey.GetStringSelection()
		return selected if selected else self.cmbKey.GetItems()[0]

	def _updateCustomKeyLabel(self):
		hasCustom = bool(self._customKeystroke)
		if hasCustom:
			self.btnCustomShortcut.SetLabel(
				_("Custom &Shortcut: {key}...").format(key=self._customKeystroke)
			)
		else:
			self.btnCustomShortcut.SetLabel(_("Custom &Shortcut..."))
		# The combo box is deliberately never disabled: a disabled control
		# drops out of the Tab order entirely, which was the root cause of
		# the standard key silently "disappearing" from the dialog once a
		# custom shortcut was set -- a screen reader user tabbing through had
		# no way to even find it again, let alone confirm what it still said.
		# Picking a standard key from the (always reachable) combo box
		# implicitly clears any custom shortcut -- see onStandardKeyChanged.
		self.btnClearCustomShortcut.Enable(hasCustom)
		self.keySizer.Layout()

	def onCustomShortcut(self, event):
		from .customShortcuts import GestureCaptureDialog
		actionLabel = (
			_("this marker's jump shortcut")
			if self.cmbActionMode.GetSelection() == 0
			else _("this marker's auto-click shortcut")
		)
		previousKey = self._currentDisplayedKey()
		captureDlg = GestureCaptureDialog(self, actionLabel)
		try:
			result = captureDlg.ShowModal()
			captured = captureDlg.capturedGesture
		finally:
			captureDlg.Destroy()
		if result != wx.ID_OK or not captured:
			return
		# Marker keystrokes are stored without the "kb:" prefix, consistent
		# with the existing j/f/d/z / alt+j/c/x/z convention already used
		# throughout this add-on.
		self._customKeystroke = captured[3:] if captured.startswith("kb:") else captured
		self._updateCustomKeyLabel()
		# A wx.StaticText label update alone is silent to a screen reader
		# user tabbing through the dialog (plain static text isn't part of
		# the Tab order at all), so the change is also spoken directly --
		# this is the explicit from-to confirmation asked for.
		ui.message(_("Shortcut changed from {old} to {new}").format(
			old=previousKey, new=self._customKeystroke
		))

	def onClearCustomShortcut(self, event):
		self._customKeystroke = None
		self._updateCustomKeyLabel()
		ui.message(_("Shortcut reset to {key}").format(key=self._currentDisplayedKey()))

	def onStandardKeyChanged(self, event):
		# Picking a value from the combo box is the other way to set the key
		# (alongside the Custom Shortcut button); doing so should supersede
		# any custom shortcut currently set, the same way pressing "Use
		# Standard Key" does, rather than leaving a stale custom value that
		# silently wins over the user's new combo selection.
		if self._customKeystroke:
			self._customKeystroke = None
			self._updateCustomKeyLabel()
		event.Skip()

	def onSave(self, event):
		displayNameValue = self.txtDisplayName.GetValue().strip()
		patternValue = self.txtPattern.GetValue().strip()
		if not patternValue:
			wx.MessageBox(_("Pattern cannot be empty."), _("Error"), wx.OK | wx.ICON_ERROR)
			return
		if not displayNameValue:
			displayNameValue = patternValue
		self.EndModal(wx.ID_OK)

	def getMarkerData(self):
		actionMode = "jump" if self.cmbActionMode.GetSelection() == 0 else "autoClick"
		displayName = self.txtDisplayName.GetValue().strip()
		pattern = self.txtPattern.GetValue().strip()
		if not displayName:
			displayName = pattern
		scope = "document" if self.cmbScope.GetSelection() == 0 else "viewport"
		keystroke = self._customKeystroke if self._customKeystroke else self.cmbKey.GetStringSelection().lower()
		return {
			"name": displayName,
			"displayName": displayName,
			"pattern": pattern,
			"matchMode": self.cmbMatchMode.GetSelection(),
			"keystroke": keystroke,
			"actionMode": actionMode,
			"offset": self.spinOffset.GetValue(),
			"scope": scope,
			"autoTrigger": self.chkAutoTrigger.GetValue()
		}








