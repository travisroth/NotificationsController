# NotificationsController: historyDialog.py
# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""The notification history window: review, filter, and turn notifications into rules.

The list is in time order with the newest at the bottom, so notifications arriving while it is open
are added below without moving the item the user is on.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import addonHandler
import api
import gui
import ui
import wx
from gui import guiHelper
from gui.message import MessageDialog, ReturnCode

from . import settings
from .controller import Controller
from .models import ACTION_PART_TRIM, SOURCE_LIVE_REGION, SOURCES, NotificationRecord, siteDomain
from .ruleEditor import addRule, editRule, silenceSiteRule

if TYPE_CHECKING:
	from . import GlobalPlugin

addonHandler.initTranslation()

REFRESH_DELAY_MS = 400


def reportedText(record: NotificationRecord) -> str:
	"""What was reported, when a rule removed part of the text; empty otherwise."""
	if ACTION_PART_TRIM not in record.action.split("+"):
		return ""
	# Translators: In notification details, when removing the matched text left nothing to report.
	return record.presentedText or _("(nothing)")


def recordDetails(record: NotificationRecord) -> str:
	"""Every field of a notification, one per line, for the details box and for copying."""
	lines: list[tuple[str, str]] = [
		# Translators: A label in notification details.
		(_("Text"), record.text),
		# Translators: A label in notification details: the text reported after a rule removed part of it.
		(_("Reported as"), reportedText(record)),
		# Translators: A label in notification details.
		(_("Time"), time.strftime("%c", time.localtime(record.timestamp))),
		# Translators: A label in notification details.
		(_("App"), record.appLabel),
		# Translators: A label in notification details: NVDA's internal name for the app.
		(_("App module"), record.appName if record.appDisplayName else ""),
		# Translators: A label in notification details.
		(_("Kind"), settings.sourceLabels().get(record.source, record.source)),
		# Translators: A label in notification details.
		(_("Page"), record.url),
		# Translators: A label in notification details.
		(_("Category"), settings.categoryLabel(record.category)),
		# Translators: A label in notification details: what the add-on did with it.
		(_("Handled as"), settings.actionLabel(record.action)),
		# Translators: A label in notification details: the rule that matched it.
		(_("Rule"), record.matchedRuleName),
		# Translators: A label in notification details.
		(_("Politeness"), record.politeness),
		# Translators: A label in notification details: a technical identifier some apps send.
		(_("Activity ID"), record.activityId),
		# Translators: A label in notification details: the title of the foreground window at the time.
		(_("Window"), record.windowTitle),
	]
	if record.background:
		lines.append(
			(
				# Translators: A label in notification details.
				_("Note"),
				# Translators: Explains that the notification came from an app that was not in the foreground.
				_("Sent by a background app. NVDA does not report these unless a rule does."),
			),
		)
	if record.pinned:
		# Translators: In notification details, shows the entry is pinned.
		lines.append((_("Pinned"), _("yes")))
	if record.notificationKind is not None:
		# Translators: A label in notification details, technical.
		lines.append(
			(_("UIA kind and processing"), f"{record.notificationKind}, {record.notificationProcessing}")
		)
	text = "\n".join(f"{label}: {value}" for label, value in lines if value)
	if record.details:
		# Translators: A heading in notification details, before technical information about a toast.
		text += "\n" + _("Structure:") + "\n" + record.details
	return text


class HistoryList(wx.ListCtrl):
	"""A virtual list of notifications."""

	def __init__(self, parent: wx.Window, **kwargs):
		kwargs.setdefault("size", (750, 300))
		super().__init__(parent, style=wx.LC_REPORT | wx.LC_VIRTUAL | wx.LC_SINGLE_SEL, **kwargs)
		self.records: list[NotificationRecord] = []
		columns = [
			# Translators: A column in the notification history list.
			(_("Text"), 330),
			# Translators: A column in the notification history list.
			(_("App"), 120),
			# Translators: A column in the notification history list.
			(_("Time"), 90),
			# Translators: A column in the notification history list.
			(_("Website"), 120),
			# Translators: A column in the notification history list.
			(_("Category"), 100),
			# Translators: A column in the notification history list.
			(_("Handled as"), 140),
		]
		for i, (label, width) in enumerate(columns):
			self.InsertColumn(i, label, width=width)

	def OnGetItemText(self, item: int, column: int) -> str:
		if item >= len(self.records):
			return ""
		r = self.records[item]
		if column == 0:
			return r.text
		if column == 1:
			return r.appLabel
		if column == 2:
			return time.strftime("%X", time.localtime(r.timestamp))
		if column == 3:
			return r.domain
		if column == 4:
			return settings.categoryLabel(r.category)
		if column == 5:
			return settings.actionLabel(r.action)
		return ""

	def setRecords(self, records: list[NotificationRecord]) -> None:
		self.records = records
		self.SetItemCount(len(records))
		self.Refresh()

	def selectedIndex(self) -> int:
		return self.GetFirstSelected()

	def selectedRecord(self) -> NotificationRecord | None:
		index = self.GetFirstSelected()
		return self.records[index] if 0 <= index < len(self.records) else None

	def selectIndex(self, index: int) -> None:
		if not self.records:
			return
		index = max(0, min(index, len(self.records) - 1))
		self.Select(index)
		self.Focus(index)
		self.EnsureVisible(index)


class HistoryDialog(wx.Dialog):
	_instance: HistoryDialog | None = None

	@classmethod
	def showInstance(cls, controller: Controller, plugin: GlobalPlugin) -> HistoryDialog:
		if cls._instance is None:
			gui.mainFrame.prePopup()
			cls._instance = cls(gui.mainFrame, controller, plugin)
			cls._instance.Show()
			gui.mainFrame.postPopup()
		else:
			cls._instance.Raise()
			cls._instance.list.SetFocus()
		return cls._instance

	def __init__(self, parent: wx.Window, controller: Controller, plugin: GlobalPlugin):
		# Translators: The title of the notification history window.
		super().__init__(
			parent, title=_("Notification history"), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER
		)
		self.controller = controller
		self.plugin = plugin
		self._refreshPending = False
		self._appValues: list[str] = []
		self._domainValues: list[str] = []
		self._categoryValues: list[str] = []
		self._sourceValues = ["", *SOURCES]

		mainSizer = wx.BoxSizer(wx.VERTICAL)
		helper = guiHelper.BoxSizerHelper(self, orientation=wx.VERTICAL)

		filters = guiHelper.BoxSizerHelper(self, orientation=wx.HORIZONTAL)
		# Translators: A filter in the notification history window.
		self.appFilter = filters.addLabeledControl(_("&App:"), wx.Choice, choices=[])
		# Translators: A filter in the notification history window.
		self.domainFilter = filters.addLabeledControl(_("&Website:"), wx.Choice, choices=[])
		# Translators: A filter in the notification history window.
		self.categoryFilter = filters.addLabeledControl(_("Cate&gory:"), wx.Choice, choices=[])
		sourceLabels = settings.sourceLabels()
		self.sourceFilter = filters.addLabeledControl(
			# Translators: A filter in the notification history window.
			_("&Kind:"),
			wx.Choice,
			# Translators: A filter choice in the notification history window: show every kind.
			choices=[_("All kinds")] + [sourceLabels[s] for s in SOURCES],
		)
		self.sourceFilter.SetSelection(0)
		helper.addItem(filters)
		filters2 = guiHelper.BoxSizerHelper(self, orientation=wx.HORIZONTAL)
		# Translators: A search field in the notification history window.
		self.searchText = filters2.addLabeledControl(_("&Search:"), wx.TextCtrl, size=(250, -1))
		self.unreviewedCheck = filters2.addItem(
			# Translators: A filter checkbox in the notification history window.
			wx.CheckBox(self, label=_("&Unreviewed only")),
		)
		helper.addItem(filters2)

		# Translators: The label of the notification history list.
		self.list: HistoryList = helper.addLabeledControl(_("&Notifications:"), HistoryList)
		self.details = helper.addLabeledControl(
			# Translators: The label of the details of the selected notification.
			_("&Details:"),
			wx.TextCtrl,
			style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2,
			size=(750, 150),
		)

		buttons = guiHelper.ButtonHelper(wx.HORIZONTAL)
		# Translators: A button in the notification history window.
		self.createRuleButton = buttons.addButton(self, label=_("&Create rule..."))
		self.silenceSiteButton = buttons.addButton(
			self,
			# Translators: A button in the notification history window.
			label=_("Silence this site's &live regions..."),
		)
		# Translators: A button in the notification history window.
		self.pinButton = buttons.addButton(self, label=_("&Pin"))
		# Translators: A button in the notification history window.
		self.copyButton = buttons.addButton(self, label=_("C&opy"))
		helper.addItem(buttons)
		buttons2 = guiHelper.ButtonHelper(wx.HORIZONTAL)
		# Translators: A button in the notification history window.
		self.deleteButton = buttons2.addButton(self, label=_("D&elete"))
		# Translators: A button in the notification history window.
		self.deleteShownButton = buttons2.addButton(self, label=_("Delete all s&hown..."))
		# Translators: A button in the notification history window.
		self.reviewedButton = buttons2.addButton(self, label=_("Mark all shown as re&viewed"))
		# Translators: A button in the notification history window.
		rulesButton = buttons2.addButton(self, label=_("&Rules..."))
		helper.addItem(buttons2)
		# Translators: The button that closes the notification history window.
		helper.addDialogDismissButtons(wx.Button(self, wx.ID_CLOSE, label=_("Close")), separated=True)

		mainSizer.Add(
			helper.sizer, border=guiHelper.BORDER_FOR_DIALOGS, flag=wx.ALL | wx.EXPAND, proportion=1
		)
		self.SetSizer(mainSizer)
		mainSizer.Fit(self)
		self.SetEscapeId(wx.ID_CLOSE)

		for control in (self.appFilter, self.domainFilter, self.categoryFilter, self.sourceFilter):
			control.Bind(wx.EVT_CHOICE, lambda evt: self.refresh(keepSelection=False))
		self.searchText.Bind(wx.EVT_TEXT, lambda evt: self.refresh(keepSelection=False))
		self.unreviewedCheck.Bind(wx.EVT_CHECKBOX, lambda evt: self.refresh(keepSelection=False))
		self.list.Bind(wx.EVT_LIST_ITEM_SELECTED, self.onSelect)
		self.list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self.onActivate)
		self.list.Bind(wx.EVT_KEY_DOWN, self.onListKey)
		self.createRuleButton.Bind(wx.EVT_BUTTON, self.onCreateRule)
		self.silenceSiteButton.Bind(wx.EVT_BUTTON, self.onSilenceSite)
		self.pinButton.Bind(wx.EVT_BUTTON, self.onPin)
		self.copyButton.Bind(wx.EVT_BUTTON, self.onCopy)
		self.deleteButton.Bind(wx.EVT_BUTTON, self.onDelete)
		self.deleteShownButton.Bind(wx.EVT_BUTTON, self.onDeleteShown)
		self.reviewedButton.Bind(wx.EVT_BUTTON, self.onMarkReviewed)
		rulesButton.Bind(wx.EVT_BUTTON, lambda evt: self.plugin.showRules())
		self.Bind(wx.EVT_CLOSE, self.onClose)
		self.Bind(wx.EVT_BUTTON, lambda evt: self.Close(), id=wx.ID_CLOSE)

		self.controller.history.onChange.append(self.onHistoryChanged)
		self.updateFilterChoices()
		self.refresh(keepSelection=False)
		self.list.selectIndex(len(self.list.records) - 1)
		self.updateSelection()
		self.CentreOnScreen()
		self.list.SetFocus()

	# Filters and refreshing

	def _updateChoice(
		self, choice: wx.Choice, allLabel: str, values: list[str], labels: list[str]
	) -> list[str]:
		"""Refill a filter choice, keeping the chosen value. Returns the values behind the items."""
		current = ""
		oldValues = getattr(choice, "_ncValues", None)
		selection = choice.GetSelection()
		if oldValues and 0 <= selection < len(oldValues):
			current = oldValues[selection]
		newValues = ["", *values]
		if newValues == oldValues:
			return newValues
		choice.Set([allLabel, *labels])
		choice._ncValues = newValues
		choice.SetSelection(newValues.index(current) if current in newValues else 0)
		return newValues

	def updateFilterChoices(self) -> None:
		history = self.controller.history
		apps = history.apps()
		# Translators: A filter choice in the notification history window: show every app.
		self._appValues = self._updateChoice(self.appFilter, _("All apps"), apps, apps)
		domains = history.domains()
		# Translators: A filter choice in the notification history window: show every website.
		self._domainValues = self._updateChoice(self.domainFilter, _("All websites"), domains, domains)
		categories = history.categories()
		self._categoryValues = self._updateChoice(
			self.categoryFilter,
			# Translators: A filter choice in the notification history window: show every category.
			_("All categories"),
			categories,
			[settings.categoryLabel(c) for c in categories],
		)

	@staticmethod
	def _choiceValue(choice: wx.Choice, values: list[str]) -> str:
		index = choice.GetSelection()
		return values[index] if 0 <= index < len(values) else ""

	def filteredRecords(self) -> list[NotificationRecord]:
		records = self.controller.history.filter(
			app=self._choiceValue(self.appFilter, self._appValues),
			domain=self._choiceValue(self.domainFilter, self._domainValues),
			category=self._choiceValue(self.categoryFilter, self._categoryValues),
			source=self._choiceValue(self.sourceFilter, self._sourceValues),
			text=self.searchText.GetValue().strip(),
			unreviewedOnly=self.unreviewedCheck.GetValue(),
		)
		records.reverse()
		return records

	def refresh(self, keepSelection: bool = True) -> None:
		old = self.list.records
		selected = self.list.selectedRecord()
		records = self.filteredRecords()
		self.list.setRecords(records)
		if not records:
			self.updateSelection()
			return
		if keepSelection and selected is not None:
			if len(records) >= len(old) and records[: len(old)] == old:
				# Only new entries at the end: nothing moved.
				self.list.RefreshItems(0, len(records) - 1)
				return
			for index, record in enumerate(records):
				if record.id == selected.id:
					self.list.selectIndex(index)
					self.updateSelection()
					return
		self.list.selectIndex(len(records) - 1)
		self.updateSelection()

	def onHistoryChanged(self) -> None:
		if self._refreshPending:
			return
		self._refreshPending = True
		wx.CallLater(REFRESH_DELAY_MS, self._delayedRefresh)

	def _delayedRefresh(self) -> None:
		self._refreshPending = False
		if HistoryDialog._instance is not self:
			return
		self.updateFilterChoices()
		self.refresh()

	# Selection

	def onSelect(self, evt):
		self.updateSelection()
		record = self.list.selectedRecord()
		# With the unreviewed filter on, marking on selection would remove the item from under the user.
		if record and not record.reviewed and not self.unreviewedCheck.GetValue():
			record.reviewed = True
			self.controller.history.update([record])

	def updateSelection(self) -> None:
		record = self.list.selectedRecord()
		self.details.SetValue(recordDetails(record) if record else "")
		hasRecord = record is not None
		for button in (self.createRuleButton, self.pinButton, self.copyButton, self.deleteButton):
			button.Enable(hasRecord)
		self.silenceSiteButton.Enable(bool(record and record.source == SOURCE_LIVE_REGION and record.domain))
		if record and record.pinned:
			# Translators: A button in the notification history window.
			self.pinButton.SetLabel(_("Un&pin"))
		else:
			self.pinButton.SetLabel(_("&Pin"))
		hasShown = bool(self.list.records)
		self.deleteShownButton.Enable(hasShown)
		self.reviewedButton.Enable(hasShown)

	def selectRecord(self, recordId: int) -> None:
		for index, record in enumerate(self.list.records):
			if record.id == recordId:
				self.list.selectIndex(index)
				self.updateSelection()
				return

	def onActivate(self, evt):
		record = self.list.selectedRecord()
		if record:
			ui.message(record.text)

	def onListKey(self, evt: wx.KeyEvent):
		key = evt.GetKeyCode()
		if key == wx.WXK_DELETE:
			self.onDelete(evt)
		elif key == wx.WXK_F5:
			self.updateFilterChoices()
			self.refresh()
		elif key == ord("C") and evt.ControlDown():
			self.onCopy(evt)
		else:
			evt.Skip()

	# Actions

	def onCreateRule(self, evt):
		record = self.list.selectedRecord()
		if not record:
			return
		from .ruleEditor import ruleFromRecord

		edited = editRule(self, self.controller, ruleFromRecord(record), isNew=True)
		if edited:
			addRule(self.controller, edited, parent=self)

	def onSilenceSite(self, evt):
		record = self.list.selectedRecord()
		if not record or not record.domain:
			return
		site = siteDomain(record.domain)
		result = MessageDialog.ask(
			# Translators: Asks before adding a rule that silences a website's live regions, and whether they
			# should still be logged to history.
			_(
				"Add a rule that silences all live regions on {site}?\n\n"
				"Should they still be logged to history, so you can review them later?",
			).format(site=site),
			# Translators: The title of a question.
			_("Silence live regions"),
			parent=self,
			# Translators: A button: silence a website's live regions and keep logging them to history.
			yesLabel=_("Silence and keep &logging"),
			# Translators: A button: silence a website's live regions and stop logging them to history.
			noLabel=_("Silence and do&n't log"),
		)
		if result == ReturnCode.CANCEL:
			return
		addRule(self.controller, silenceSiteRule(site, log=result == ReturnCode.YES), parent=self)

	def onPin(self, evt):
		record = self.list.selectedRecord()
		if not record:
			return
		self.controller.history.setPinned([record], not record.pinned)
		self.updateSelection()
		if record.pinned:
			# Translators: Reported when a notification is pinned.
			ui.message(_("Pinned"))
		else:
			# Translators: Reported when a notification is unpinned.
			ui.message(_("Unpinned"))

	def onCopy(self, evt):
		record = self.list.selectedRecord()
		if record:
			api.copyToClip(recordDetails(record), notify=True)

	def onDelete(self, evt):
		record = self.list.selectedRecord()
		if not record:
			return
		index = self.list.selectedIndex()
		self.controller.history.delete([record.id])
		self.refresh(keepSelection=False)
		self.list.selectIndex(min(index, len(self.list.records) - 1))
		self.updateSelection()

	def onDeleteShown(self, evt):
		records = self.list.records
		if not records:
			return
		result = MessageDialog.confirm(
			# Translators: Asks before deleting the notifications shown in the history list.
			ngettext(
				"Delete the {count} notification shown?",
				"Delete the {count} notifications shown?",
				len(records),
			).format(count=len(records)),
			# Translators: The title of a confirmation.
			_("Delete notifications"),
			parent=self,
		)
		if result == ReturnCode.OK:
			self.controller.history.delete(r.id for r in records)
			self.refresh(keepSelection=False)

	def onMarkReviewed(self, evt):
		self.controller.history.setReviewed(self.list.records)
		# Translators: Reported when notifications are marked as reviewed.
		ui.message(_("Marked as reviewed"))

	def onClose(self, evt):
		try:
			self.controller.history.onChange.remove(self.onHistoryChanged)
		except ValueError:
			pass
		HistoryDialog._instance = None
		self.Destroy()
