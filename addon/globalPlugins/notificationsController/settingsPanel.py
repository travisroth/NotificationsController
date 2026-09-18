# NotificationsController: settingsPanel.py
# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""The add-on's panel in NVDA Settings."""

from __future__ import annotations

import addonHandler
import wx
from gui import guiHelper, nvdaControls
from gui.message import MessageDialog, ReturnCode
from gui.settingsDialogs import SettingsPanel

from . import settings

addonHandler.initTranslation()


class NotificationsControllerPanel(SettingsPanel):
	# Translators: The title of the add-on's panel in NVDA Settings.
	title = _("Notifications Controller")
	controller = None
	"""Set by the global plugin."""

	def makeSettings(self, sizer: wx.BoxSizer):
		conf = settings.conf()
		helper = guiHelper.BoxSizerHelper(self, sizer=sizer)

		self.enabledCheck = helper.addItem(
			# Translators: A checkbox in the add-on's settings.
			wx.CheckBox(self, label=_("&Use rules for notifications (when off, NVDA handles them as usual)")),
		)
		self.enabledCheck.SetValue(conf["enabled"])
		self.doNotDisturbCheck = helper.addItem(
			# Translators: A checkbox in the add-on's settings.
			wx.CheckBox(self, label=_("&Do not disturb: report only important notifications")),
		)
		self.doNotDisturbCheck.SetValue(conf["doNotDisturb"])

		# Translators: A group of settings about notification history.
		historyGroup = guiHelper.BoxSizerHelper(
			self, sizer=wx.StaticBoxSizer(wx.VERTICAL, self, _("History"))
		)
		historyBox = historyGroup.sizer.GetStaticBox()
		helper.addItem(historyGroup)
		self.logCheck = historyGroup.addItem(
			# Translators: A checkbox in the add-on's settings.
			wx.CheckBox(historyBox, label=_("&Log notifications to history")),
		)
		self.logCheck.SetValue(conf["logEnabled"])
		self.persistCheck = historyGroup.addItem(
			wx.CheckBox(
				historyBox,
				# Translators: A checkbox in the add-on's settings. History is stored as plain text.
				label=_(
					"&Keep history after NVDA restarts (saved as plain text in your NVDA settings folder)"
				),
			),
		)
		self.persistCheck.SetValue(conf["persistHistory"])
		self.retentionChoice = historyGroup.addLabeledControl(
			# Translators: The label of a choice in the add-on's settings.
			_("Delete notifications &older than:"),
			wx.Choice,
			choices=[settings.retentionLabel(h) for h in settings.RETENTION_CHOICES],
		)
		retention = conf["retentionHours"]
		choices = list(settings.RETENTION_CHOICES)
		self.retentionChoice.SetSelection(
			choices.index(retention) if retention in choices else choices.index(24)
		)
		self.maxEntriesSpin = historyGroup.addLabeledControl(
			# Translators: The label of a number field in the add-on's settings.
			_("&Maximum notifications to keep:"),
			nvdaControls.SelectOnFocusSpinCtrl,
			min=100,
			max=100000,
			initial=conf["maxEntries"],
		)
		self.keepImportantCheck = historyGroup.addItem(
			# Translators: A checkbox in the add-on's settings.
			wx.CheckBox(historyBox, label=_("Never automatically delete &important notifications")),
		)
		self.keepImportantCheck.SetValue(conf["keepImportant"])
		self.dedupeSpin = historyGroup.addLabeledControl(
			# Translators: The label of a number field in the add-on's settings.
			_("Log a repeated notification only once within (millisecond&s):"),
			nvdaControls.SelectOnFocusSpinCtrl,
			min=0,
			max=10000,
			initial=conf["dedupeMs"],
		)
		# Translators: A button in the add-on's settings that deletes every notification in history.
		clearButton = historyGroup.addItem(wx.Button(historyBox, label=_("&Clear history...")))
		clearButton.Bind(wx.EVT_BUTTON, self.onClearHistory)

		# Translators: A group of settings choosing which kinds of notification the add-on handles.
		sourcesGroup = guiHelper.BoxSizerHelper(self, sizer=wx.StaticBoxSizer(wx.VERTICAL, self, _("Handle")))
		sourcesBox = sourcesGroup.sizer.GetStaticBox()
		helper.addItem(sourcesGroup)
		self.uiaCheck = sourcesGroup.addItem(
			# Translators: A checkbox in the add-on's settings.
			wx.CheckBox(sourcesBox, label=_("App &notifications (UI Automation)")),
		)
		self.uiaCheck.SetValue(conf["captureUIA"])
		self.toastCheck = sourcesGroup.addItem(
			# Translators: A checkbox in the add-on's settings.
			wx.CheckBox(sourcesBox, label=_("Windows &toasts")),
		)
		self.toastCheck.SetValue(conf["captureToasts"])
		self.liveRegionCheck = sourcesGroup.addItem(
			# Translators: A checkbox in the add-on's settings.
			wx.CheckBox(sourcesBox, label=_("Web page li&ve regions")),
		)
		self.liveRegionCheck.SetValue(conf["captureLiveRegions"])

	def onClearHistory(self, evt):
		result = MessageDialog.confirm(
			# Translators: Asks before deleting all notification history.
			_("Delete every notification in history, including pinned ones? This cannot be undone."),
			# Translators: The title of a confirmation.
			_("Clear history"),
			parent=self,
		)
		if result == ReturnCode.OK and self.controller:
			self.controller.history.clear()
			self.controller.flush()

	def onSave(self):
		conf = settings.conf()
		conf["enabled"] = self.enabledCheck.GetValue()
		conf["doNotDisturb"] = self.doNotDisturbCheck.GetValue()
		conf["logEnabled"] = self.logCheck.GetValue()
		conf["persistHistory"] = self.persistCheck.GetValue()
		conf["retentionHours"] = settings.RETENTION_CHOICES[self.retentionChoice.GetSelection()]
		conf["maxEntries"] = self.maxEntriesSpin.GetValue()
		conf["keepImportant"] = self.keepImportantCheck.GetValue()
		conf["dedupeMs"] = self.dedupeSpin.GetValue()
		conf["captureUIA"] = self.uiaCheck.GetValue()
		conf["captureToasts"] = self.toastCheck.GetValue()
		conf["captureLiveRegions"] = self.liveRegionCheck.GetValue()
		if self.controller:
			self.controller.applySettings()
