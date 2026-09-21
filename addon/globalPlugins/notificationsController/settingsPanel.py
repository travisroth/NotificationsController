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
from .storage import MAX_DEDUPE_MS, MAX_ENTRIES, MIN_ENTRIES, StorageSettings

addonHandler.initTranslation()


class NotificationsControllerPanel(SettingsPanel):
	# Translators: The title of the add-on's panel in NVDA Settings.
	title = _("Notifications Controller")
	controller = None
	"""Set by the global plugin."""

	def makeSettings(self, sizer: wx.BoxSizer):
		conf = settings.conf()
		storage = self.controller.storage if self.controller else StorageSettings()
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
		if self.controller and self.controller.storageProblem:
			historyGroup.addItem(
				wx.StaticText(
					historyBox,
					# Translators: Shown in the add-on's settings when its storage settings file could not be
					# read or saved. {error} is the technical reason.
					label=_(
						"Warning: the history storage settings could not be read or saved ({error}). "
						"Until they are saved again, history is not kept on disk. Press OK to save them.",
					).format(error=self.controller.storageProblem),
				),
			)
		historyGroup.addItem(
			wx.StaticText(
				historyBox,
				# Translators: Explains that the settings after it are shared by all configuration profiles.
				label=_("These storage settings apply to all configuration profiles:"),
			),
		)
		self.persistCheck = historyGroup.addItem(
			wx.CheckBox(
				historyBox,
				# Translators: A checkbox in the add-on's settings. History is stored as plain text.
				label=_(
					"&Keep history after NVDA restarts (saved as plain text in your NVDA settings folder)"
				),
			),
		)
		self.persistCheck.SetValue(storage.persistHistory)
		self.retentionChoice = historyGroup.addLabeledControl(
			# Translators: The label of a choice in the add-on's settings.
			_("Delete notifications &older than:"),
			wx.Choice,
			choices=[settings.retentionLabel(h) for h in settings.RETENTION_CHOICES],
		)
		retention = storage.retentionHours
		choices = list(settings.RETENTION_CHOICES)
		self.retentionChoice.SetSelection(
			choices.index(retention) if retention in choices else choices.index(24)
		)
		self.maxEntriesSpin = historyGroup.addLabeledControl(
			# Translators: The label of a number field in the add-on's settings.
			_("&Maximum notifications to keep:"),
			nvdaControls.SelectOnFocusSpinCtrl,
			min=MIN_ENTRIES,
			max=MAX_ENTRIES,
			initial=storage.maxEntries,
		)
		self.keepImportantCheck = historyGroup.addItem(
			# Translators: A checkbox in the add-on's settings.
			wx.CheckBox(historyBox, label=_("Never automatically delete &important notifications")),
		)
		self.keepImportantCheck.SetValue(storage.keepImportant)
		self.dedupeSpin = historyGroup.addLabeledControl(
			# Translators: The label of a number field in the add-on's settings.
			_("Log a repeated notification only once within (millisecond&s):"),
			nvdaControls.SelectOnFocusSpinCtrl,
			min=0,
			max=MAX_DEDUPE_MS,
			initial=storage.dedupeMs,
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
		self.alertCheck = sourcesGroup.addItem(
			# Translators: A checkbox in the add-on's settings. Alerts include apps' own notification pop-ups.
			wx.CheckBox(sourcesBox, label=_("&Pop-up alerts")),
		)
		self.alertCheck.SetValue(conf["captureAlerts"])

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
		conf["captureUIA"] = self.uiaCheck.GetValue()
		conf["captureToasts"] = self.toastCheck.GetValue()
		conf["captureLiveRegions"] = self.liveRegionCheck.GetValue()
		conf["captureAlerts"] = self.alertCheck.GetValue()
		if not self.controller:
			return
		storage = StorageSettings(
			persistHistory=self.persistCheck.GetValue(),
			retentionHours=settings.RETENTION_CHOICES[self.retentionChoice.GetSelection()],
			maxEntries=self.maxEntriesSpin.GetValue(),
			keepImportant=self.keepImportantCheck.GetValue(),
			dedupeMs=self.dedupeSpin.GetValue(),
		)
		if self.controller.setStorage(storage):
			return
		if storage.persistHistory:
			# Translators: Shown when the add-on's history storage settings could not be saved.
			message = _(
				"The history storage settings could not be saved. They apply until NVDA restarts, "
				"and then the previous settings return. See the NVDA log for details.",
			)
		else:
			# Translators: Shown when turning off saving history could not be saved as a setting.
			message = _(
				"History is no longer being saved to disk, but this setting could not be saved. "
				"After NVDA restarts, history may be saved to disk again. "
				"See the NVDA log for details.",
			)
		# The settings dialog is closing; show the message once it has gone.
		# Translators: The title of an error message.
		wx.CallAfter(MessageDialog.alert, message, _("Error"))
