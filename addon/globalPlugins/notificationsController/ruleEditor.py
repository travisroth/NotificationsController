# NotificationsController: ruleEditor.py
# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""The dialog for adding and editing a rule."""

from __future__ import annotations

import copy
import os

import addonHandler
import gui
import ui
import wx
from gui import guiHelper
from gui.message import MessageDialog, ReturnCode

from . import output, settings
from .controller import Controller
from .models import (
	BUILTIN_CATEGORIES,
	CATEGORY_IMPORTANT,
	CATEGORY_INFORMATIONAL,
	CATEGORY_SPAM,
	MATCH_ANY,
	MATCH_REGEX,
	MATCH_STARTS_WITH,
	OUTPUT_NONE,
	SOUND_BUILTIN_PREFIX,
	SOURCE_ANY,
	SOURCE_LIVE_REGION,
	SOURCE_TOAST,
	Action,
	NotificationRecord,
	Rule,
	siteDomain,
)
from .rules import CompiledRule, newRuleId, normalizeText, validateRegex

addonHandler.initTranslation()

PATTERN_WORDS = 6
"""How many words of a notification to put in a new rule's pattern."""


def ruleFromRecord(record: NotificationRecord) -> Rule:
	"""A new rule prefilled to match a notification like this one."""
	rule = Rule(id=newRuleId())
	if record.source == SOURCE_TOAST and record.appDisplayName:
		rule.app = record.appDisplayName
	else:
		rule.app = record.appName
	rule.source = record.source
	rule.domain = siteDomain(record.domain)
	if record.activityId:
		rule.activityId = record.activityId
		rule.matchType = MATCH_ANY
	else:
		rule.matchType = MATCH_STARTS_WITH
		rule.pattern = " ".join(normalizeText(record.text).split(" ")[:PATTERN_WORDS])
	return rule


def silenceSiteRule(domain: str) -> Rule:
	"""A rule that silences every live region on a website."""
	domain = siteDomain(domain)
	return Rule(
		id=newRuleId(),
		# Translators: The name of a rule that silences a website's live regions.
		name=_("Silence live regions on {site}").format(site=domain),
		source=SOURCE_LIVE_REGION,
		domain=domain,
		matchType=MATCH_ANY,
		action=Action(output=OUTPUT_NONE),
		category=CATEGORY_SPAM,
	)


def defaultRuleName(rule: Rule) -> str:
	parts = [p for p in (rule.app, rule.domain, rule.pattern or rule.activityId) if p]
	# Translators: The name given to a rule when the user leaves the name empty and it has nothing else to show.
	return ": ".join(parts) if parts else _("All notifications")


class RuleEditorDialog(wx.Dialog):
	def __init__(self, parent: wx.Window | None, controller: Controller, rule: Rule, isNew: bool):
		if isNew:
			# Translators: The title of the dialog for adding a rule.
			title = _("Add rule")
		else:
			# Translators: The title of the dialog for editing a rule.
			title = _("Edit rule")
		super().__init__(parent or gui.mainFrame, title=title)
		self.controller = controller
		self.rule = copy.deepcopy(rule)
		self._sourceKeys = list(settings.sourceLabels())
		self._matchKeys = list(settings.matchTypeLabels())
		self._outputKeys = list(settings.outputLabels())
		self._politenessKeys = list(settings.politenessLabels())
		soundLabels = settings.builtinSoundLabels()
		self._soundKeys = ["", *soundLabels, "custom"]
		soundChoices = [
			# Translators: A sound choice in the rule editor.
			_("No sound"),
			*soundLabels.values(),
			# Translators: A sound choice in the rule editor.
			_("Custom sound file"),
		]

		mainSizer = wx.BoxSizer(wx.VERTICAL)
		helper = guiHelper.BoxSizerHelper(self, orientation=wx.VERTICAL)

		# Translators: A field in the rule editor.
		self.nameText = helper.addLabeledControl(_("&Name:"), wx.TextCtrl)
		# Translators: A checkbox in the rule editor.
		self.enabledCheck = helper.addItem(wx.CheckBox(self, label=_("&Enabled")))

		# Translators: A group in the rule editor: which notifications the rule applies to.
		matchGroup = guiHelper.BoxSizerHelper(self, sizer=wx.StaticBoxSizer(wx.VERTICAL, self, _("Match")))
		matchBox = matchGroup.sizer.GetStaticBox()
		helper.addItem(matchGroup)
		history = controller.history
		self.appCombo = matchGroup.addLabeledControl(
			# Translators: A field in the rule editor.
			_("&App (empty for any app):"),
			wx.ComboBox,
			choices=history.appNames(),
		)
		self.sourceChoice = matchGroup.addLabeledControl(
			# Translators: A field in the rule editor.
			_("&Kind of notification:"),
			wx.Choice,
			choices=list(settings.sourceLabels().values()),
		)
		self.domainCombo = matchGroup.addLabeledControl(
			# Translators: A field in the rule editor.
			_("&Website (empty for any site or app):"),
			wx.ComboBox,
			choices=sorted({siteDomain(d) for d in history.domains()}),
		)
		self.subdomainsCheck = matchGroup.addItem(
			# Translators: A checkbox in the rule editor.
			wx.CheckBox(matchBox, label=_("Include subdo&mains, such as www and app")),
		)
		self.urlPrefixText = matchGroup.addLabeledControl(
			# Translators: A field in the rule editor.
			_("&URL starts with (optional):"),
			wx.TextCtrl,
		)
		self.matchChoice = matchGroup.addLabeledControl(
			# Translators: A field in the rule editor.
			_("&Text:"),
			wx.Choice,
			choices=list(settings.matchTypeLabels().values()),
		)
		# Translators: A field in the rule editor.
		self.patternText = matchGroup.addLabeledControl(_("&Pattern:"), wx.TextCtrl)
		self.caseCheck = matchGroup.addItem(
			# Translators: A checkbox in the rule editor.
			wx.CheckBox(matchBox, label=_("&Case sensitive")),
		)
		self.activityText = matchGroup.addLabeledControl(
			# Translators: A field in the rule editor. Activity ID is a technical identifier some apps send.
			_("Activit&y ID (optional):"),
			wx.TextCtrl,
		)
		self.politenessChoice = matchGroup.addLabeledControl(
			# Translators: A field in the rule editor, about live regions.
			_("Live region pol&iteness:"),
			wx.Choice,
			choices=list(settings.politenessLabels().values()),
		)

		# Translators: A group in the rule editor: what to do with matching notifications.
		actionGroup = guiHelper.BoxSizerHelper(self, sizer=wx.StaticBoxSizer(wx.VERTICAL, self, _("Action")))
		actionBox = actionGroup.sizer.GetStaticBox()
		helper.addItem(actionGroup)
		self.outputChoice = actionGroup.addLabeledControl(
			# Translators: A field in the rule editor.
			_("Speech and &braille:"),
			wx.Choice,
			choices=list(settings.outputLabels().values()),
		)
		# Translators: A field in the rule editor.
		self.soundChoice = actionGroup.addLabeledControl(_("S&ound:"), wx.Choice, choices=soundChoices)
		soundFileSizer = guiHelper.BoxSizerHelper(actionBox, orientation=wx.HORIZONTAL)
		self.soundFileText = soundFileSizer.addLabeledControl(
			# Translators: A field in the rule editor.
			_("Sound fi&le:"),
			wx.TextCtrl,
			size=(300, -1),
		)
		# Translators: A button in the rule editor.
		self.browseButton = soundFileSizer.addItem(wx.Button(actionBox, label=_("B&rowse...")))
		self.browseButton.Bind(wx.EVT_BUTTON, self.onBrowse)
		actionGroup.addItem(soundFileSizer)
		# Translators: A button in the rule editor that plays the chosen sound.
		self.playButton = actionGroup.addItem(wx.Button(actionBox, label=_("Play soun&d")))
		self.playButton.Bind(wx.EVT_BUTTON, self.onPlay)
		self._categoryLabels = [settings.categoryLabel(c) for c in BUILTIN_CATEGORIES]
		self.categoryCombo = actionGroup.addLabeledControl(
			# Translators: A field in the rule editor. Users can pick a category or type their own.
			_("Cate&gory:"),
			wx.ComboBox,
			choices=self._categoryLabels,
		)
		self.logCheck = actionGroup.addItem(
			# Translators: A checkbox in the rule editor.
			wx.CheckBox(actionBox, label=_("Log to hi&story")),
		)

		# Translators: A group in the rule editor for trying the rule on past notifications.
		testGroup = guiHelper.BoxSizerHelper(self, sizer=wx.StaticBoxSizer(wx.VERTICAL, self, _("Test")))
		testBox = testGroup.sizer.GetStaticBox()
		helper.addItem(testGroup)
		# Translators: A button in the rule editor.
		testButton = testGroup.addItem(wx.Button(testBox, label=_("Test against &history")))
		testButton.Bind(wx.EVT_BUTTON, self.onTest)
		self.testList = testGroup.addLabeledControl(
			# Translators: The label of the list of history entries a rule would match.
			_("Matching noti&fications:"),
			wx.ListBox,
			size=(500, 120),
		)

		helper.addDialogDismissButtons(wx.OK | wx.CANCEL, separated=True)
		self.Bind(wx.EVT_BUTTON, self.onOk, id=wx.ID_OK)
		for control in (self.sourceChoice, self.matchChoice, self.soundChoice):
			control.Bind(wx.EVT_CHOICE, lambda evt: self.updateEnabled())
		self.domainCombo.Bind(wx.EVT_TEXT, lambda evt: self.updateEnabled())

		mainSizer.Add(helper.sizer, border=guiHelper.BORDER_FOR_DIALOGS, flag=wx.ALL)
		mainSizer.Fit(self)
		self.SetSizer(mainSizer)
		self.loadRule()
		self.updateEnabled()
		self.CentreOnScreen()
		self.nameText.SetFocus()

	# Rule to controls and back

	def loadRule(self) -> None:
		r = self.rule
		self.nameText.SetValue(r.name)
		self.enabledCheck.SetValue(r.enabled)
		self.appCombo.SetValue(r.app)
		self.sourceChoice.SetSelection(self._index(self._sourceKeys, r.source))
		self.domainCombo.SetValue(r.domain)
		self.subdomainsCheck.SetValue(r.includeSubdomains)
		self.urlPrefixText.SetValue(r.urlPrefix)
		self.matchChoice.SetSelection(self._index(self._matchKeys, r.matchType))
		self.patternText.SetValue(r.pattern)
		self.caseCheck.SetValue(r.caseSensitive)
		self.activityText.SetValue(r.activityId)
		self.politenessChoice.SetSelection(self._index(self._politenessKeys, r.politeness))
		self.outputChoice.SetSelection(self._index(self._outputKeys, r.action.output))
		sound = r.action.sound
		if sound and not sound.startswith(SOUND_BUILTIN_PREFIX):
			self.soundChoice.SetSelection(self._soundKeys.index("custom"))
			self.soundFileText.SetValue(sound)
		else:
			self.soundChoice.SetSelection(self._index(self._soundKeys, sound))
		self.categoryCombo.SetValue(settings.categoryLabel(r.category))
		self.logCheck.SetValue(r.log)

	@staticmethod
	def _index(keys: list[str], key: str) -> int:
		return keys.index(key) if key in keys else 0

	def _category(self) -> str:
		value = self.categoryCombo.GetValue().strip()
		for key, label in zip(BUILTIN_CATEGORIES, self._categoryLabels):
			if value.casefold() in (label.casefold(), key):
				return key
		return value or CATEGORY_INFORMATIONAL

	def _sound(self) -> str:
		key = self._soundKeys[self.soundChoice.GetSelection()]
		if key == "custom":
			return self.soundFileText.GetValue().strip()
		return key

	def ruleFromControls(self) -> Rule:
		r = copy.deepcopy(self.rule)
		r.name = self.nameText.GetValue().strip()
		r.enabled = self.enabledCheck.GetValue()
		r.app = self.appCombo.GetValue().strip()
		r.source = self._sourceKeys[self.sourceChoice.GetSelection()]
		r.domain = self.domainCombo.GetValue().strip()
		r.includeSubdomains = self.subdomainsCheck.GetValue()
		r.urlPrefix = self.urlPrefixText.GetValue().strip()
		r.matchType = self._matchKeys[self.matchChoice.GetSelection()]
		r.pattern = self.patternText.GetValue()
		r.caseSensitive = self.caseCheck.GetValue()
		r.activityId = self.activityText.GetValue().strip()
		r.politeness = self._politenessKeys[self.politenessChoice.GetSelection()]
		r.action.output = self._outputKeys[self.outputChoice.GetSelection()]
		r.action.sound = self._sound()
		r.category = self._category()
		r.log = self.logCheck.GetValue()
		return r

	def updateEnabled(self) -> None:
		source = self._sourceKeys[self.sourceChoice.GetSelection()]
		matchType = self._matchKeys[self.matchChoice.GetSelection()]
		custom = self._soundKeys[self.soundChoice.GetSelection()] == "custom"
		self.subdomainsCheck.Enable(bool(self.domainCombo.GetValue().strip()))
		self.patternText.Enable(matchType != MATCH_ANY)
		self.caseCheck.Enable(matchType != MATCH_ANY)
		self.politenessChoice.Enable(source in (SOURCE_ANY, SOURCE_LIVE_REGION))
		self.soundFileText.Enable(custom)
		self.browseButton.Enable(custom)

	# Buttons

	def onBrowse(self, evt):
		with wx.FileDialog(
			self,
			# Translators: The title of the dialog for choosing a sound file.
			message=_("Choose a sound"),
			# Translators: The file type filter when choosing a sound file.
			wildcard=_("Wave files (*.wav)") + "|*.wav",
			style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
		) as dialog:
			if dialog.ShowModal() == wx.ID_OK:
				self.soundFileText.SetValue(dialog.GetPath())

	def onPlay(self, evt):
		sound = self._sound()
		if not sound:
			# Translators: Reported when there is no sound to play.
			ui.message(_("No sound"))
			return
		output.playSound(sound)

	def onTest(self, evt):
		rule = self.ruleFromControls()
		rule.enabled = True
		compiled = CompiledRule(rule)
		if compiled.error:
			# Translators: Reported when a rule's regular expression is not valid.
			ui.message(_("Regular expression error: {error}").format(error=compiled.error))
			return
		records = self.controller.history.records
		try:
			matches = [r for r in reversed(records) if compiled.matches(r)]
		except TimeoutError:
			# Translators: Reported when testing a rule whose regular expression is too slow to use.
			message = _(
				"The regular expression takes too long on some notifications. "
				"Simplify it; a rule like this would be turned off.",
			)
			self.testList.Set([])
			self._alert(message)
			return
		self.testList.Set([f"{r.text} ({r.appLabel or r.domain})" for r in matches[:500]])
		# Translators: Reports how many history entries a rule matches.
		ui.message(_("{count} of {total} notifications match").format(count=len(matches), total=len(records)))

	def onOk(self, evt):
		rule = self.ruleFromControls()
		if rule.matchType == MATCH_REGEX:
			error = validateRegex(rule.pattern, rule.caseSensitive)
			if error:
				# Translators: Shown when a rule's regular expression is not valid.
				self._alert(_("The regular expression is not valid: {error}").format(error=error))
				self.patternText.SetFocus()
				return
		if rule.matchType != MATCH_ANY and not rule.pattern.strip():
			# Translators: Shown when a rule has no text to match.
			self._alert(_("Enter the text to match, or choose Any text."))
			self.patternText.SetFocus()
			return
		sound = rule.action.sound
		if sound and not sound.startswith(SOUND_BUILTIN_PREFIX) and not os.path.isfile(sound):
			# Translators: Shown when a rule's sound file does not exist.
			self._alert(_("The sound file was not found."))
			self.soundFileText.SetFocus()
			return
		if (
			rule.source == SOURCE_LIVE_REGION
			and not rule.isSiteScoped
			and not rule.app
			and not self._confirm(
				# Translators: Asks before saving a live region rule that is not limited to a website.
				_(
					"This rule applies to live regions on every website. "
					"Rules for live regions are usually limited to one website. Save it anyway?",
				),
			)
		):
			self.domainCombo.SetFocus()
			return
		if (
			not rule.app
			and not rule.isSiteScoped
			and not rule.activityId
			and rule.matchType == MATCH_ANY
			and rule.action.output == OUTPUT_NONE
			and rule.category != CATEGORY_IMPORTANT
			and not self._confirm(
				# Translators: Asks before saving a rule that silences every notification.
				_("This rule silences every notification of this kind from every app. Save it anyway?"),
			)
		):
			return
		if not rule.name:
			rule.name = defaultRuleName(rule)
		self.rule = rule
		self.EndModal(wx.ID_OK)

	def _alert(self, message: str) -> None:
		# Translators: The title of a message about a problem with a rule.
		MessageDialog.alert(message, _("Rule"), parent=self)

	def _confirm(self, message: str) -> bool:
		# Translators: The title of a confirmation about a rule.
		return MessageDialog.confirm(message, _("Rule"), parent=self) == ReturnCode.OK


def editRule(parent: wx.Window | None, controller: Controller, rule: Rule, isNew: bool) -> Rule | None:
	"""Show the rule editor. Returns the edited rule, or None if cancelled."""
	dialog = RuleEditorDialog(parent, controller, rule, isNew)
	if parent is None:
		gui.mainFrame.prePopup()
	try:
		result = dialog.ShowModal()
		edited = dialog.rule
	finally:
		dialog.Destroy()
		if parent is None:
			gui.mainFrame.postPopup()
	return edited if result == wx.ID_OK else None


def reportSaveFailure(parent: wx.Window | None) -> None:
	MessageDialog.alert(
		# Translators: Shown when rules could not be saved. The rules in use are unchanged.
		_("The rules could not be saved, so the change was not made. See the NVDA log for details."),
		# Translators: The title of an error message.
		_("Error"),
		parent=parent,
	)


def addRule(controller: Controller, rule: Rule, index: int = 0, parent: wx.Window | None = None) -> bool:
	"""Add a rule, save, and announce it. Reports the error and changes nothing when saving fails."""
	rules = list(controller.rules.rules)
	rules.insert(index, rule)
	if not controller.commitRules(rules):
		reportSaveFailure(parent)
		return False
	# Translators: Reported when a rule has been added.
	ui.message(_("Rule added"))
	return True


def editNewRuleFromRecord(
	controller: Controller,
	record: NotificationRecord | None,
	parent: wx.Window | None = None,
) -> Rule | None:
	"""Open the editor for a new rule, prefilled from a notification when one is given, and add it."""
	rule = ruleFromRecord(record) if record else Rule(id=newRuleId())
	edited = editRule(parent, controller, rule, isNew=True)
	if edited and addRule(controller, edited, parent=parent):
		return edited
	return None
