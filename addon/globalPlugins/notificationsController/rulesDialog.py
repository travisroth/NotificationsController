# NotificationsController: rulesDialog.py
# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""The rules window: the ordered list of rules, with adding, editing, reordering, import and export.

Changes are saved as soon as they are made.
"""

from __future__ import annotations

import copy
import json

import addonHandler
import gui
import ui
import wx
from gui import guiHelper
from gui.message import MessageDialog, ReturnCode

from . import settings
from .controller import Controller
from .models import MATCH_ANY, Rule
from .ruleEditor import editRule
from .rules import RuleSet, newRuleId, writeJsonAtomic

addonHandler.initTranslation()


def describeMatch(rule: Rule) -> str:
	if rule.matchType == MATCH_ANY:
		text = settings.matchTypeLabels()[MATCH_ANY]
	else:
		label = settings.matchTypeLabels().get(rule.matchType, rule.matchType)
		text = f"{label} {rule.pattern}"
	if rule.activityId:
		# Translators: Part of a rule's description in the rules list.
		text += " " + _("(activity {id})").format(id=rule.activityId)
	return text


def describeAction(rule: Rule) -> str:
	return settings.actionLabel(rule.action.describe())


class RulesDialog(wx.Dialog):
	_instance: RulesDialog | None = None

	@classmethod
	def showInstance(cls, controller: Controller) -> RulesDialog:
		if cls._instance is None:
			gui.mainFrame.prePopup()
			cls._instance = cls(gui.mainFrame, controller)
			cls._instance.Show()
			gui.mainFrame.postPopup()
		else:
			cls._instance.Raise()
			cls._instance.list.SetFocus()
		return cls._instance

	def __init__(self, parent: wx.Window, controller: Controller):
		# Translators: The title of the notification rules window.
		super().__init__(
			parent, title=_("Notification rules"), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER
		)
		self.controller = controller
		self._saving = False
		mainSizer = wx.BoxSizer(wx.VERTICAL)
		helper = guiHelper.BoxSizerHelper(self, orientation=wx.VERTICAL)

		if controller.rulesLoadError:
			helper.addItem(
				wx.StaticText(
					self,
					# Translators: Shown when the saved rules could not be read.
					label=_(
						"Your saved rules could not be read and were set aside as rules.json.damaged: {error}",
					).format(error=controller.rulesLoadError),
				),
			)
		helper.addItem(
			wx.StaticText(
				self,
				# Translators: Explains the order of rules.
				label=_("Rules are checked from the top down. The first enabled rule that matches is used."),
			),
		)
		self.list = helper.addLabeledControl(
			# Translators: The label of the list of rules.
			_("&Rules:"),
			wx.ListCtrl,
			style=wx.LC_REPORT | wx.LC_SINGLE_SEL,
			size=(750, 280),
		)
		columns = [
			# Translators: A column in the rules list.
			(_("Name"), 200),
			# Translators: A column in the rules list.
			(_("Enabled"), 70),
			# Translators: A column in the rules list.
			(_("App"), 110),
			# Translators: A column in the rules list.
			(_("Kind"), 110),
			# Translators: A column in the rules list.
			(_("Website"), 110),
			# Translators: A column in the rules list.
			(_("Text"), 160),
			# Translators: A column in the rules list.
			(_("Action"), 150),
			# Translators: A column in the rules list.
			(_("Category"), 100),
		]
		for i, (label, width) in enumerate(columns):
			self.list.InsertColumn(i, label, width=width)

		buttons = guiHelper.ButtonHelper(wx.HORIZONTAL)
		# Translators: A button in the rules window.
		addButton = buttons.addButton(self, label=_("&Add..."))
		# Translators: A button in the rules window.
		self.editButton = buttons.addButton(self, label=_("&Edit..."))
		# Translators: A button in the rules window.
		self.duplicateButton = buttons.addButton(self, label=_("Dup&licate"))
		# Translators: A button in the rules window that turns a rule on or off.
		self.toggleButton = buttons.addButton(self, label=_("Disa&ble"))
		# Translators: A button in the rules window.
		self.deleteButton = buttons.addButton(self, label=_("&Delete"))
		helper.addItem(buttons)
		buttons2 = guiHelper.ButtonHelper(wx.HORIZONTAL)
		# Translators: A button in the rules window.
		self.upButton = buttons2.addButton(self, label=_("Move &up"))
		# Translators: A button in the rules window.
		self.downButton = buttons2.addButton(self, label=_("Move d&own"))
		# Translators: A button in the rules window.
		importButton = buttons2.addButton(self, label=_("&Import..."))
		# Translators: A button in the rules window.
		self.exportButton = buttons2.addButton(self, label=_("E&xport..."))
		helper.addItem(buttons2)
		# Translators: The button that closes the rules window.
		helper.addDialogDismissButtons(wx.Button(self, wx.ID_CLOSE, label=_("Close")), separated=True)

		mainSizer.Add(
			helper.sizer, border=guiHelper.BORDER_FOR_DIALOGS, flag=wx.ALL | wx.EXPAND, proportion=1
		)
		self.SetSizer(mainSizer)
		mainSizer.Fit(self)
		self.SetEscapeId(wx.ID_CLOSE)

		addButton.Bind(wx.EVT_BUTTON, self.onAdd)
		self.editButton.Bind(wx.EVT_BUTTON, self.onEdit)
		self.duplicateButton.Bind(wx.EVT_BUTTON, self.onDuplicate)
		self.toggleButton.Bind(wx.EVT_BUTTON, self.onToggle)
		self.deleteButton.Bind(wx.EVT_BUTTON, self.onDelete)
		self.upButton.Bind(wx.EVT_BUTTON, lambda evt: self.move(-1))
		self.downButton.Bind(wx.EVT_BUTTON, lambda evt: self.move(1))
		importButton.Bind(wx.EVT_BUTTON, self.onImport)
		self.exportButton.Bind(wx.EVT_BUTTON, self.onExport)
		self.list.Bind(wx.EVT_LIST_ITEM_SELECTED, lambda evt: self.updateButtons())
		self.list.Bind(wx.EVT_LIST_ITEM_DESELECTED, lambda evt: self.updateButtons())
		self.list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self.onEdit)
		self.list.Bind(wx.EVT_KEY_DOWN, self.onListKey)
		self.Bind(wx.EVT_CLOSE, self.onClose)
		self.Bind(wx.EVT_BUTTON, lambda evt: self.Close(), id=wx.ID_CLOSE)
		controller.onRulesChanged.append(self.onRulesChanged)

		self.fill()
		if self.rules:
			self.select(0)
		self.CentreOnScreen()
		self.list.SetFocus()

	@property
	def rules(self) -> list[Rule]:
		return self.controller.rules.rules

	def fill(self) -> None:
		self.list.DeleteAllItems()
		sources = settings.sourceLabels()
		errors = self.controller.rules.errors()
		for index, rule in enumerate(self.rules):
			name = rule.name
			if rule.id in errors:
				# Translators: Marks a rule whose regular expression is not valid, in the rules list.
				name = _("{name} (error: {error})").format(name=name, error=errors[rule.id])
			# Translators: Whether a rule is on, in the rules list.
			enabled = _("yes") if rule.enabled else _("no")
			values = [
				name,
				enabled,
				rule.app,
				sources.get(rule.source, rule.source),
				rule.urlPrefix or rule.domain,
				describeMatch(rule),
				describeAction(rule),
				settings.categoryLabel(rule.category),
			]
			self.list.InsertItem(index, values[0])
			for column, value in enumerate(values[1:], start=1):
				self.list.SetItem(index, column, value)
		self.updateButtons()

	def selectedIndex(self) -> int:
		return self.list.GetFirstSelected()

	def select(self, index: int) -> None:
		if 0 <= index < self.list.GetItemCount():
			self.list.Select(index)
			self.list.Focus(index)
			self.list.EnsureVisible(index)
		self.updateButtons()

	def updateButtons(self) -> None:
		index = self.selectedIndex()
		has = index >= 0
		for button in (self.editButton, self.duplicateButton, self.toggleButton, self.deleteButton):
			button.Enable(has)
		self.upButton.Enable(has and index > 0)
		self.downButton.Enable(has and index < len(self.rules) - 1)
		self.exportButton.Enable(bool(self.rules))
		if has and not self.rules[index].enabled:
			# Translators: A button in the rules window that turns a rule on.
			self.toggleButton.SetLabel(_("Ena&ble"))
		else:
			self.toggleButton.SetLabel(_("Disa&ble"))

	def save(self, selectIndex: int | None = None) -> None:
		self._saving = True
		try:
			self.controller.saveRules()
		except OSError as e:
			MessageDialog.alert(
				# Translators: Shown when rules could not be saved.
				_("The rules could not be saved: {error}").format(error=e),
				# Translators: The title of an error message.
				_("Error"),
				parent=self,
			)
		finally:
			self._saving = False
		self.fill()
		if selectIndex is not None:
			self.select(selectIndex)

	def onRulesChanged(self) -> None:
		"""Rules changed elsewhere, such as a rule added from the history window."""
		if self._saving:
			return
		index = self.selectedIndex()
		self.fill()
		self.select(min(index, len(self.rules) - 1) if index >= 0 else 0)

	# Actions

	def onAdd(self, evt):
		rule = editRule(self, self.controller, Rule(id=newRuleId()), isNew=True)
		if rule:
			index = max(self.selectedIndex(), 0)
			self.rules.insert(index, rule)
			self.save(index)

	def onEdit(self, evt):
		index = self.selectedIndex()
		if index < 0:
			return
		rule = editRule(self, self.controller, self.rules[index], isNew=False)
		if rule:
			self.rules[index] = rule
			self.save(index)

	def onDuplicate(self, evt):
		index = self.selectedIndex()
		if index < 0:
			return
		rule = copy.deepcopy(self.rules[index])
		rule.id = newRuleId()
		# Translators: The name of a copy of a rule.
		rule.name = _("Copy of {name}").format(name=rule.name)
		self.rules.insert(index + 1, rule)
		self.save(index + 1)

	def onToggle(self, evt):
		index = self.selectedIndex()
		if index < 0:
			return
		rule = self.rules[index]
		rule.enabled = not rule.enabled
		self.save(index)
		if rule.enabled:
			# Translators: Reported when a rule is turned on.
			ui.message(_("Enabled"))
		else:
			# Translators: Reported when a rule is turned off.
			ui.message(_("Disabled"))

	def onDelete(self, evt):
		index = self.selectedIndex()
		if index < 0:
			return
		result = MessageDialog.confirm(
			# Translators: Asks before deleting a rule.
			_("Delete the rule {name}?").format(name=self.rules[index].name),
			# Translators: The title of a confirmation.
			_("Delete rule"),
			parent=self,
		)
		if result == ReturnCode.OK:
			del self.rules[index]
			self.save(min(index, len(self.rules) - 1))

	def move(self, step: int) -> None:
		index = self.selectedIndex()
		newIndex = index + step
		if index < 0 or not 0 <= newIndex < len(self.rules):
			return
		self.rules[index], self.rules[newIndex] = self.rules[newIndex], self.rules[index]
		self.save(newIndex)
		# Translators: Reported after moving a rule, with its new position.
		ui.message(_("Position {n}").format(n=newIndex + 1))

	def onListKey(self, evt: wx.KeyEvent):
		key = evt.GetKeyCode()
		if key == wx.WXK_DELETE:
			self.onDelete(evt)
		elif key == wx.WXK_SPACE:
			self.onToggle(evt)
		elif evt.AltDown() and key == wx.WXK_UP:
			self.move(-1)
		elif evt.AltDown() and key == wx.WXK_DOWN:
			self.move(1)
		else:
			evt.Skip()

	def onImport(self, evt):
		with wx.FileDialog(
			self,
			# Translators: The title of the dialog for importing rules.
			message=_("Import rules"),
			# Translators: The file type filter for rule files.
			wildcard=_("Rule files (*.json)") + "|*.json",
			style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
		) as dialog:
			if dialog.ShowModal() != wx.ID_OK:
				return
			path = dialog.GetPath()
		try:
			with open(path, encoding="utf-8") as f:
				imported = RuleSet.rulesFromJson(json.load(f))
		except (OSError, ValueError) as e:
			MessageDialog.alert(
				# Translators: Shown when a rules file could not be imported.
				_("The file could not be imported: {error}").format(error=e),
				_("Error"),
				parent=self,
			)
			return
		existingIds = {r.id for r in self.rules}
		for rule in imported:
			if rule.id in existingIds:
				rule.id = newRuleId()
		self.rules.extend(imported)
		self.save(len(self.rules) - 1)
		# Translators: Reported after importing rules.
		ui.message(ngettext("Imported {n} rule", "Imported {n} rules", len(imported)).format(n=len(imported)))

	def onExport(self, evt):
		with wx.FileDialog(
			self,
			# Translators: The title of the dialog for exporting rules.
			message=_("Export rules"),
			defaultFile="notificationsController-rules.json",
			wildcard=_("Rule files (*.json)") + "|*.json",
			style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
		) as dialog:
			if dialog.ShowModal() != wx.ID_OK:
				return
			path = dialog.GetPath()
		try:
			writeJsonAtomic(path, RuleSet.rulesToJson(self.rules))
		except OSError as e:
			MessageDialog.alert(
				# Translators: Shown when rules could not be exported.
				_("The rules could not be exported: {error}").format(error=e),
				_("Error"),
				parent=self,
			)

	def onClose(self, evt):
		try:
			self.controller.onRulesChanged.remove(self.onRulesChanged)
		except ValueError:
			pass
		RulesDialog._instance = None
		self.Destroy()
