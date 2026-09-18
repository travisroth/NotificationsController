# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.
# Imports here must run in a set order, around NVDA's test setup.
# ruff: noqa: I001

"""Smoke tests that load the add-on inside NVDA's own unit test environment, without running NVDA.

They catch wrong NVDA API names, broken dialogs and a broken live region hook, which the plain unit
tests cannot see. They need an NVDA source checkout with its helper DLLs built.

Run from anywhere, with NVDA's virtual environment:

	C:\\code\\nvda\\.venv\\Scripts\\python.exe tests\\nvda\\smokeNvda.py

Set NVDA_SOURCE_DIR if the checkout is not at C:\\code\\nvda.
"""

import os
import sys
import tempfile
import types
import unittest

NVDA_TOP = os.environ.get("NVDA_SOURCE_DIR", r"C:\code\nvda")
ADDON_PLUGINS = os.path.join(
	os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
	"addon",
	"globalPlugins",
)

sys.path.insert(0, NVDA_TOP)
sys.path.insert(0, os.path.join(NVDA_TOP, "source"))
# NVDA's unit test package sets up config, speech, braille, a focus object and nvdaHelperLocal.
# It must be imported before any other NVDA module.
import tests.unit  # noqa: F401

import addonHandler

# The add-on is not installed here, so there are no add-on translations to load.
addonHandler.initTranslation = lambda: None
sys.path.insert(0, ADDON_PLUGINS)

import ctypes

import config
import globalVars
import NVDAHelper
import nvwave
import queueHandler
import speech
import wx

_tempDir = tempfile.TemporaryDirectory()
globalVars.appArgs.configPath = _tempDir.name

import notificationsController as nc
from notificationsController import capture, output, settings
from notificationsController.controller import Controller
from notificationsController.models import (
	OUTPUT_NONE,
	OUTPUT_SPEECH,
	SOURCE_LIVE_REGION,
	SOURCE_UIA,
	Action,
	NotificationRecord,
	Rule,
)

settings.registerConfig()

played: list[str] = []
nvwave.playWaveFile = lambda path, asynchronous=True, **kw: played.append(path)
spoken: list[object] = []
speech.speak = lambda seq, symbolLevel=None, priority=None: spoken.append(seq)
speech.speech.speak = speech.speak
if os.environ.get("SMOKE_LOG"):
	import logging

	from logHandler import log

	log.setLevel(logging.DEBUG)
	log.addHandler(logging.StreamHandler(sys.stderr))


def rec(text="Hello", source=SOURCE_UIA, appName="testapp"):
	import time

	return NotificationRecord(timestamp=time.time(), source=source, text=text, appName=appName)


class TestController(unittest.TestCase):
	def setUp(self):
		self.controller = Controller()
		self.controller.loadRules()
		self.controller.applySettings()
		self.controller.history.clear()
		self.passed = 0

	def passthrough(self):
		self.passed += 1

	def test_defaultsLogAndPassThrough(self):
		self.controller.process(rec(), self.passthrough)
		self.assertEqual(self.passed, 1)
		self.assertEqual(len(self.controller.history), 1)

	def test_silentRuleWithSound(self):
		self.controller.rules.setRules(
			[Rule(id="r", action=Action(OUTPUT_NONE, "builtin:chime"), matchType="any")]
		)
		played.clear()
		self.controller.process(rec(), self.passthrough)
		self.assertEqual(self.passed, 0)
		self.assertEqual(len(played), 1)
		self.assertTrue(played[0].endswith("chime.wav"))
		self.assertTrue(os.path.isfile(played[0]))

	def test_speechRule(self):
		self.controller.rules.setRules([Rule(id="r", action=Action(OUTPUT_SPEECH), matchType="any")])
		spoken.clear()
		self.controller.process(rec("Speak me"), self.passthrough)
		self.assertEqual(self.passed, 0)
		self.assertTrue(any("Speak me" in str(s) for s in spoken))

	def test_saveAndReloadRules(self):
		self.controller.rules.setRules([Rule(id="r", name="n")])
		self.controller.saveRules()
		other = Controller()
		other.loadRules()
		self.assertEqual([r.name for r in other.rules.rules], ["n"])

	def test_flushWritesHistory(self):
		self.controller.process(rec(), self.passthrough)
		self.controller.flush()
		self.assertTrue(os.path.isfile(settings.historyPath()))

	def test_disabledPassesThroughWithoutLogging(self):
		settings.conf()["enabled"] = False
		try:
			self.controller.process(rec(), self.passthrough)
		finally:
			settings.conf()["enabled"] = True
		self.assertEqual(self.passed, 1)
		self.assertEqual(len(self.controller.history), 0)

	def test_liveRegionOffInNvda(self):
		self.controller.rules.setRules([Rule(id="r", action=Action(OUTPUT_SPEECH), matchType="any")])
		config.conf["presentation"]["reportDynamicContentChanges"] = False
		try:
			spoken.clear()
			self.controller.process(rec(source=SOURCE_LIVE_REGION), self.passthrough)
		finally:
			config.conf["presentation"]["reportDynamicContentChanges"] = True
		self.assertEqual(spoken, [])
		self.assertEqual(self.passed, 1)


class TestCapture(unittest.TestCase):
	def test_recordsFromPlaceholderObject(self):
		import api

		obj = api.getFocusObject()
		record = capture.fromUIANotification(obj, 1, 2, "Text", "Activity")
		self.assertEqual(record.text, "Text")
		self.assertEqual(record.activityId, "Activity")
		record = capture.fromLiveRegionEvent(obj)
		self.assertEqual(record.source, SOURCE_LIVE_REGION)
		record = capture.fromToast(obj)
		self.assertIsInstance(record.details, str)
		record = capture.fromHelperLiveRegion("Live", "assertive", os.getpid())
		self.assertEqual(record.politeness, "assertive")


class TestToast(unittest.TestCase):
	"""A toast shaped like a real Windows 11 one, from a history entry."""

	@staticmethod
	def node(role, automationId, name, children=()):
		return types.SimpleNamespace(
			role=role,
			UIAAutomationId=automationId,
			name=name,
			description="",
			children=list(children),
			appModule=None,
		)

	def test_fromToastUsesTitleMessageAndSender(self):
		import controlTypes

		text, button, window = (
			controlTypes.Role.STATICTEXT,
			controlTypes.Role.BUTTON,
			controlTypes.Role.WINDOW,
		)
		toast = self.node(
			window,
			"NormalToastView",
			"New notification from Windows PowerShell, Notification tester, Test notification 1.. 1 of 1",
			[
				self.node(text, "SenderName", "Windows PowerShell"),
				self.node(button, "SettingsButton", "Settings for this notification"),
				self.node(button, "DismissButton", "Move this notification to Notification Center"),
				self.node(text, "Title", "Notification tester"),
				self.node(text, "MessageText", "Test notification 1"),
			],
		)
		record = capture.fromToast(toast)
		self.assertEqual(record.text, "Notification tester, Test notification 1")
		self.assertEqual(record.appDisplayName, "Windows PowerShell")
		self.assertIn("Spoken as: New notification from Windows PowerShell", record.details)


class TestLiveRegionHook(unittest.TestCase):
	"""Drive the real nvdaHelperLocal export through the hook and back to NVDA's own handler."""

	def setUp(self):
		dll = NVDAHelper.localLib
		self.dll = getattr(dll, "dll", dll)
		self._savedLocalLib = NVDAHelper.localLib
		# In NVDA, localLib is a module wrapping the DLL as .dll; NVDA's unit tests load the bare DLL.
		NVDAHelper.localLib = types.SimpleNamespace(dll=self.dll)
		NVDAHelper._setDllFuncPointer(
			self.dll,
			"_nvdaControllerInternal_reportLiveRegion",
			NVDAHelper.nvdaControllerInternal_reportLiveRegion,
		)
		self.received: list[tuple[str, str, int]] = []
		self.hook = capture.LiveRegionHook(lambda *args: self.received.append(args))

	def tearDown(self):
		self.hook.uninstall()
		NVDAHelper.localLib = self._savedLocalLib

	def slot(self) -> int:
		return ctypes.cast(
			self.dll._nvdaControllerInternal_reportLiveRegion,
			ctypes.POINTER(ctypes.c_void_p),
		).contents.value

	def callExport(self, text: str, politeness: str) -> int:
		"""Call whatever the helper's function pointer points at, as the in-process helper would."""
		func = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_wchar_p, ctypes.c_wchar_p)(self.slot())
		return func(text, politeness)

	def test_installRoutesToHandlerAndUninstallRestores(self):
		original = self.slot()
		self.assertTrue(self.hook.install())
		self.assertNotEqual(self.slot(), original)
		self.assertEqual(self.callExport("Breaking news", "polite"), 0)
		queueHandler.pumpAll()
		self.assertEqual(len(self.received), 1)
		self.assertEqual(self.received[0][:2], ("Breaking news", "polite"))
		self.hook.uninstall()
		self.assertEqual(self.slot(), original)

	def test_passthroughReachesNvda(self):
		self.hook.install()
		spoken.clear()
		self.hook.passthrough("Pass me", "assertive")
		queueHandler.pumpAll()
		self.assertTrue(any("Pass me" in str(s) for s in spoken))


class TestDialogs(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.app = wx.App.Get() or wx.App()
		cls.frame = wx.Frame(None)
		cls.controller = Controller()
		cls.controller.loadRules()
		cls.controller.applySettings()
		cls.controller.history.add(rec("From Teams", appName="ms-teams"))
		live = rec("Score update", source=SOURCE_LIVE_REGION, appName="chrome")
		live.url = "https://www.example.com/scores"
		live.domain = "www.example.com"
		live.details = "structure"
		cls.controller.history.add(live)
		cls.controller.rules.setRules([Rule(id="r", name="Rule one", domain="example.com")])

	@classmethod
	def tearDownClass(cls):
		cls.frame.Destroy()

	def test_historyDialog(self):
		from notificationsController.historyDialog import HistoryDialog, recordDetails

		dialog = HistoryDialog(self.frame, self.controller, plugin=None)
		try:
			self.assertEqual(len(dialog.list.records), 2)
			self.assertEqual(dialog.list.OnGetItemText(1, 0), "Score update")
			self.assertIn("example.com", recordDetails(dialog.list.records[1]))
			dialog.searchText.SetValue("teams")
			dialog.refresh(keepSelection=False)
			self.assertEqual(len(dialog.list.records), 1)
		finally:
			dialog.Close()

	def test_rulesDialog(self):
		from notificationsController.rulesDialog import RulesDialog

		dialog = RulesDialog(self.frame, self.controller)
		try:
			self.assertEqual(dialog.list.GetItemCount(), 1)
			self.assertEqual(dialog.list.GetItemText(0), "Rule one")
		finally:
			dialog.Close()

	def test_ruleEditorRoundTrip(self):
		from notificationsController.ruleEditor import RuleEditorDialog, ruleFromRecord

		record = self.controller.history.records[1]
		rule = ruleFromRecord(record)
		self.assertEqual(rule.domain, "example.com")
		dialog = RuleEditorDialog(self.frame, self.controller, rule, isNew=True)
		try:
			self.assertEqual(dialog.ruleFromControls(), rule)
		finally:
			dialog.Destroy()

	def test_silenceSiteRuleSilences(self):
		from notificationsController.ruleEditor import silenceSiteRule

		controller = Controller()
		controller.applySettings()
		controller.rules.setRules([silenceSiteRule("www.example.com")])
		live = rec("Ticker", source=SOURCE_LIVE_REGION, appName="chrome")
		live.domain = "news.example.com"
		passed = []
		spoken.clear()
		controller.process(live, lambda: passed.append(1))
		self.assertEqual(passed, [])
		self.assertEqual(spoken, [])
		self.assertEqual(live.action, "none")

	def test_emptyHistoryFiltersHaveAllItem(self):
		from notificationsController.historyDialog import HistoryDialog

		controller = Controller()
		controller.applySettings()
		controller.history.clear()
		dialog = HistoryDialog(self.frame, controller, plugin=None)
		try:
			for choice in (dialog.appFilter, dialog.domainFilter, dialog.categoryFilter):
				self.assertEqual(choice.GetCount(), 1)
				self.assertEqual(choice.GetSelection(), 0)
		finally:
			dialog.Close()

	def test_settingsPanel(self):
		from notificationsController.settingsPanel import NotificationsControllerPanel

		NotificationsControllerPanel.controller = self.controller
		panel = NotificationsControllerPanel(self.frame)
		try:
			panel.onSave()
		finally:
			panel.Destroy()


class TestPluginModule(unittest.TestCase):
	def test_describeRecord(self):
		self.assertIn("Hello", nc.describeRecord(rec()))

	def test_outputLabels(self):
		self.assertTrue(settings.actionLabel("speech+sound"))
		output.present("x", Action(OUTPUT_NONE))


if __name__ == "__main__":
	unittest.main(verbosity=2)
