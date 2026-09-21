# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.
# Imports here must run in a set order, around NVDA's test setup.
# ruff: noqa: I001, E402

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
from notificationsController.controller import Controller, StorageProblem
from notificationsController.settingsPanel import storageProblemMessages
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
		self.controller.load()
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
		self.assertTrue(self.controller.commitRules([Rule(id="r", name="n")]))
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


class TestAlert(unittest.TestCase):
	"""An app's own notification pop-up: an alert with no name, its text in descendants."""

	def test_alertTextFromDescendants(self):
		import controlTypes

		node = TestToast.node
		role = controlTypes.Role
		alert = node(
			role.ALERT,
			"",
			"",
			[
				node(role.SECTION, "", "", [node(role.STATICTEXT, "", "Sam Smith")]),
				node(role.STATICTEXT, "", "Are you joining the call?"),
				node(role.BUTTON, "", "Reply"),
				node(role.STATICTEXT, "", "Are you joining the call?"),
			],
		)
		self.assertTrue(capture.isReportableAlert(alert))
		record = capture.fromAlert(alert)
		self.assertEqual(record.source, "alert")
		self.assertEqual(record.text, "Sam Smith, Are you joining the call?")

	def test_namedAlertUsesName(self):
		import controlTypes

		alert = TestToast.node(controlTypes.Role.ALERT, "", "Saved", [])
		self.assertEqual(capture.alertText(alert), "Saved")

	def test_notAlertRoleIsNotReportable(self):
		import controlTypes

		self.assertFalse(capture.isReportableAlert(TestToast.node(controlTypes.Role.DIALOG, "", "x", [])))


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

	def spokeAfterPump(self, text: str) -> bool:
		spoken.clear()
		queueHandler.pumpAll()
		return any(text in str(s) for s in spoken)

	def test_reportQueuedBeforeUninstallGoesToNvda(self):
		self.hook.install()
		self.callExport("In flight", "polite")
		self.hook.uninstall()
		self.assertTrue(self.spokeAfterPump("In flight"))
		self.assertEqual(self.received, [])

	def test_chainedHooksRemovedFirstHookFirst(self):
		original = self.slot()
		later: list[tuple] = []
		second = capture.LiveRegionHook(lambda *args: later.append(args))
		self.hook.install()
		second.install()
		try:
			# The first hook goes, but stays in the second hook's chain, inactive.
			self.hook.uninstall()
			self.assertEqual(self.slot(), second.callbackAddress)
			self.callExport("One", "polite")
			queueHandler.pumpAll()
			self.assertEqual([a[0] for a in later], ["One"])
			self.assertEqual(self.received, [])
			# The second hook passes through the inactive first hook, straight to NVDA.
			spoken.clear()
			second.passthrough("Two", "polite")
			queueHandler.pumpAll()
			self.assertTrue(any("Two" in str(s) for s in spoken))
			self.assertEqual(self.received, [])
			# Removing the second hook puts the inactive first hook back in the slot; it still forwards.
			second.uninstall()
			self.assertEqual(self.slot(), self.hook.callbackAddress)
			self.callExport("Three", "polite")
			self.assertTrue(self.spokeAfterPump("Three"))
			self.assertEqual(self.received, [])
		finally:
			second.uninstall()
			NVDAHelper._setDllFuncPointer(
				self.dll,
				"_nvdaControllerInternal_reportLiveRegion",
				NVDAHelper.nvdaControllerInternal_reportLiveRegion,
			)
		self.assertNotEqual(original, 0)

	def test_chainedHooksRemovedLastHookFirst(self):
		original = self.slot()
		second = capture.LiveRegionHook(lambda *args: None)
		self.hook.install()
		second.install()
		second.uninstall()
		self.assertEqual(self.slot(), self.hook.callbackAddress)
		self.hook.uninstall()
		self.assertEqual(self.slot(), original)

	def test_uninstalledHookDropsHandlerAndCannotReinstall(self):
		self.hook.install()
		self.hook.uninstall()
		self.assertFalse(self.hook.active)
		self.assertIsNone(self.hook._handler)
		self.assertFalse(self.hook.install())


class TestDialogs(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.app = wx.App.Get() or wx.App()
		cls.frame = wx.Frame(None)
		cls.controller = Controller()
		cls.controller.load()
		cls.controller.history.clear()
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
		controller.load()
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
		controller.load()
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


class TestUiaProcessing(unittest.TestCase):
	"""Custom speech keeps NVDA's handling of notifications that supersede earlier ones."""

	def setUp(self):
		self.cancelled = 0
		self._savedCancel = speech.cancelSpeech

		def cancel():
			self.cancelled += 1

		speech.cancelSpeech = cancel

	def tearDown(self):
		speech.cancelSpeech = self._savedCancel

	def process(self, processing):
		controller = Controller()
		controller.load()
		controller.rules.setRules([Rule(id="r", action=Action(OUTPUT_SPEECH), matchType="any")])
		record = rec("Downloading 40%")
		record.notificationProcessing = processing
		controller.process(record, lambda: None)

	def test_mostRecentCancelsEarlierSpeech(self):
		import UIAHandler

		self.process(UIAHandler.NotificationProcessing_MostRecent)
		self.process(UIAHandler.NotificationProcessing_ImportantMostRecent)
		self.assertEqual(self.cancelled, 2)

	def test_otherProcessingDoesNotCancel(self):
		import UIAHandler

		self.process(UIAHandler.NotificationProcessing_All)
		self.process(None)
		self.assertEqual(self.cancelled, 0)


class TestAlertPendingFocus(unittest.TestCase):
	def test_focusAboutToEnterAlertIsNotReportable(self):
		import api
		import controlTypes
		import eventHandler

		alert = TestToast.node(controlTypes.Role.ALERT, "", "Saved", [])
		processed = []
		saved = (eventHandler.isPendingEvents, api.processPendingEvents, api.getFocusAncestors)
		eventHandler.isPendingEvents = lambda eventName=None, obj=None: eventName == "gainFocus"
		api.processPendingEvents = lambda *a, **k: processed.append(True)
		# Once the pending focus event is processed, focus is inside the alert.
		api.getFocusAncestors = lambda: [alert] if processed else []
		try:
			self.assertFalse(capture.isReportableAlert(alert))
			self.assertEqual(processed, [True])
		finally:
			eventHandler.isPendingEvents, api.processPendingEvents, api.getFocusAncestors = saved


class TestFailedRuleSave(unittest.TestCase):
	"""When the rules file cannot be written, nothing changes: not the rules in use, not the list."""

	@classmethod
	def setUpClass(cls):
		cls.app = wx.App.Get() or wx.App()
		cls.frame = wx.Frame(None)

	@classmethod
	def tearDownClass(cls):
		cls.frame.Destroy()

	def setUp(self):
		self.controller = Controller()
		self.controller.load()
		self.assertTrue(self.controller.commitRules([Rule(id="keep", name="Keep", enabled=True)]))
		# A rules path whose folder is a file cannot be written.
		blocker = os.path.join(_tempDir.name, "blocker")
		with open(blocker, "w") as f:
			f.write("x")
		self._savedPath = settings.rulesPath
		settings.rulesPath = lambda: os.path.join(blocker, "rules.json")
		from gui.message import MessageDialog

		self.alerts = []
		self._savedAlert = MessageDialog.alert
		MessageDialog.alert = classmethod(lambda cls, message, *a, **k: self.alerts.append(message))

	def tearDown(self):
		from gui.message import MessageDialog

		settings.rulesPath = self._savedPath
		MessageDialog.alert = self._savedAlert

	def assertUnchanged(self):
		self.assertEqual([(r.id, r.enabled) for r in self.controller.rules.rules], [("keep", True)])

	def test_commitRulesFails(self):
		self.assertFalse(self.controller.commitRules([]))
		self.assertUnchanged()

	def test_addRuleFails(self):
		from notificationsController.ruleEditor import addRule

		self.assertFalse(addRule(self.controller, Rule(id="new")))
		self.assertUnchanged()
		self.assertEqual(len(self.alerts), 1)

	def test_rulesDialogChangesFail(self):
		from notificationsController.rulesDialog import RulesDialog

		dialog = RulesDialog(self.frame, self.controller)
		try:
			dialog.select(0)
			dialog.onToggle(None)
			dialog.onDuplicate(None)
			self.assertFalse(dialog.save([], 0))
			self.assertUnchanged()
			self.assertEqual(dialog.list.GetItemCount(), 1)
			self.assertEqual(len(self.alerts), 3)
		finally:
			dialog.Close()


class TestStorageSettings(unittest.TestCase):
	def test_storageSettingsAreNotInProfiles(self):
		for key in settings.LEGACY_STORAGE_KEYS:
			self.assertNotIn(key, settings.confspec)

	def test_turningSavingOffDeletesAndOnMerges(self):
		from notificationsController.storage import StorageSettings

		controller = Controller()
		controller.load()
		controller.setStorage(StorageSettings(persistHistory=True))
		controller.history.clear()
		controller.process(rec("Saved"), lambda: None)
		controller.flush()
		path = settings.historyPath()
		self.assertTrue(os.path.isfile(path))
		controller.setStorage(StorageSettings(persistHistory=False))
		self.assertFalse(os.path.isfile(path))
		controller.process(rec("Memory only"), lambda: None)
		# Another session saved history meanwhile; turning saving back on keeps both.
		other = Controller()
		other.load()
		other.history.setPath(path)
		other.history.add(rec("From elsewhere"))
		other.flush()
		controller.setStorage(StorageSettings(persistHistory=True))
		texts = [r.text for r in controller.history.records]
		self.assertIn("From elsewhere", texts)
		self.assertIn("Memory only", texts)
		self.assertIn("Saved", texts)


class _ImmediateCallAfter:
	"""Run wx.CallAfter callbacks straight away, since these tests run no wx event loop."""

	def __enter__(self):
		self._saved = wx.CallAfter
		wx.CallAfter = lambda func, *args, **kwargs: func(*args, **kwargs)
		return self

	def __exit__(self, *exc):
		wx.CallAfter = self._saved


class TestSecondReview(unittest.TestCase):
	"""Storage and rule problems are reported, and storage fails safe."""

	@classmethod
	def setUpClass(cls):
		cls.app = wx.App.Get() or wx.App()
		cls.frame = wx.Frame(None)

	@classmethod
	def tearDownClass(cls):
		cls.frame.Destroy()

	def setUp(self):
		from gui.message import MessageDialog

		self.alerts = []
		self._savedAlert = MessageDialog.alert
		MessageDialog.alert = classmethod(lambda cls, message, *a, **k: self.alerts.append(message))
		self._savedStoragePath = settings.storagePath

	def tearDown(self):
		from gui.message import MessageDialog

		MessageDialog.alert = self._savedAlert
		settings.storagePath = self._savedStoragePath
		for name in ("settings.json", "settings.json.damaged"):
			path = os.path.join(settings.dataDir(), name)
			if os.path.isfile(path):
				os.remove(path)

	def unwritableStoragePath(self):
		blocker = os.path.join(_tempDir.name, "storageBlocker")
		with open(blocker, "w") as f:
			f.write("x")
		settings.storagePath = lambda: os.path.join(blocker, "settings.json")

	def test_damagedStorageSettingsFailClosed(self):
		os.makedirs(settings.dataDir(), exist_ok=True)
		path = settings.storagePath()
		with open(path, "w", encoding="utf-8") as f:
			f.write('{"persistHistory": fal')
		controller = Controller()
		controller.load()
		self.assertFalse(controller.storage.persistHistory)
		self.assertIsNone(controller.history.path)
		self.assertEqual(controller.storageProblems, {StorageProblem.SETTINGS_UNREADABLE})
		self.assertIn("memory only", storageProblemMessages(controller)[0])
		self.assertTrue(os.path.isfile(path + ".damaged"))
		# The damaged file stays, so the next start also fails closed rather than using defaults.
		self.assertTrue(os.path.isfile(path))

	def test_failedStorageSaveTurnsSavingOffNowAndSaysSo(self):
		from notificationsController.settingsPanel import NotificationsControllerPanel
		from notificationsController.storage import StorageSettings

		controller = Controller()
		controller.load()
		controller.setStorage(StorageSettings(persistHistory=True))
		self.assertTrue(controller.history.path)
		self.unwritableStoragePath()
		NotificationsControllerPanel.controller = controller
		panel = NotificationsControllerPanel(self.frame)
		try:
			panel.persistCheck.SetValue(False)
			with _ImmediateCallAfter():
				panel.onSave()
		finally:
			panel.Destroy()
		self.assertIsNone(controller.history.path)
		self.assertEqual(len(self.alerts), 1)
		self.assertIn("memory only now", self.alerts[0])
		self.assertIn("may be saved to disk again", self.alerts[0])
		self.assertEqual(controller.storageProblems, {StorageProblem.SETTINGS_NOT_SAVED})

	def memoryOnlyController(self):
		from notificationsController.storage import StorageSettings

		controller = Controller()
		controller.load()
		controller.setStorage(StorageSettings(persistHistory=False))
		controller.history.clear()
		return controller

	def test_failedSaveWhileTurningSavingOnSaysItIsSavingNow(self):
		from notificationsController.storage import StorageSettings

		controller = self.memoryOnlyController()
		self.unwritableStoragePath()
		problems = controller.setStorage(StorageSettings(persistHistory=True))
		self.assertEqual(problems, {StorageProblem.SETTINGS_NOT_SAVED})
		self.assertTrue(controller.history.path)
		message = storageProblemMessages(controller)[0]
		self.assertIn("being saved to disk now", message)
		self.assertIn("may change back", message)

	def test_failedDeleteIsReportedWithThePath(self):
		from notificationsController.history import History
		from notificationsController.storage import StorageSettings

		controller = Controller()
		controller.load()
		controller.setStorage(StorageSettings(persistHistory=True))
		saved = History.__dict__["deleteFile"]

		def fail(path):
			raise OSError("in use")

		History.deleteFile = staticmethod(fail)
		try:
			problems = controller.setStorage(StorageSettings(persistHistory=False))
		finally:
			History.deleteFile = saved
		self.assertEqual(problems, {StorageProblem.HISTORY_NOT_DELETED})
		# Saving still stopped.
		self.assertIsNone(controller.history.path)
		message = storageProblemMessages(controller)[0]
		self.assertIn("could not be deleted", message)
		self.assertIn(settings.historyPath(), message)
		# Saving the settings again tries the delete again, and clears the warning when it works.
		self.assertEqual(controller.setStorage(StorageSettings(persistHistory=False)), set())

	def test_unreadableHistoryWhenTurningSavingOnLeavesFileAlone(self):
		from notificationsController.history import History
		from notificationsController.storage import StorageSettings

		controller = self.memoryOnlyController()
		os.makedirs(settings.dataDir(), exist_ok=True)
		path = settings.historyPath()
		with open(path, "w", encoding="utf-8") as f:
			f.write('{"timestamp": 1.0, "source": "uia", "text": "keep me", "id": 1}\n')
		controller.process(rec("memory"), lambda: None)
		saved = History.__dict__["_readFile"]

		def fail(path):
			raise OSError("locked")

		History._readFile = staticmethod(fail)
		try:
			problems = controller.setStorage(StorageSettings(persistHistory=True))
		finally:
			History._readFile = saved
		self.assertEqual(problems, {StorageProblem.HISTORY_UNREADABLE})
		self.assertIsNone(controller.history.path)
		self.assertIn("has not been changed", storageProblemMessages(controller)[0])
		controller.history.clear()
		controller.flush()
		with open(path, encoding="utf-8") as f:
			self.assertIn("keep me", f.read())
		os.remove(path)

	def test_unreadableHistoryAtStartIsNotWritten(self):
		from notificationsController.history import History
		from notificationsController.storage import StorageSettings

		Controller().setStorage(StorageSettings(persistHistory=True))
		saved = History.__dict__["_readFile"]

		def fail(path):
			raise OSError("locked")

		History._readFile = staticmethod(fail)
		try:
			controller = Controller()
			controller.load()
		finally:
			History._readFile = saved
		self.assertIsNone(controller.history.path)
		self.assertIn(StorageProblem.HISTORY_UNREADABLE, controller.storageProblems)

	def test_timedOutRuleRefreshesOpenRulesWindow(self):
		from notificationsController import rules as rulesModule
		from notificationsController.rulesDialog import RulesDialog

		controller = Controller()
		controller.load()
		self.assertTrue(
			controller.commitRules([Rule(id="slow", name="Slow", matchType="regex", pattern="x")]),
		)
		dialog = RulesDialog(self.frame, controller)
		RulesDialog._instance = dialog

		def timeOut(compiled, text, timeout=rulesModule.REGEX_TIMEOUT):
			raise TimeoutError

		saved = rulesModule.regexSearch
		rulesModule.regexSearch = timeOut
		try:
			passed = []
			with _ImmediateCallAfter():
				controller.process(rec("x"), lambda: passed.append(1))
			self.assertEqual(passed, [1])
			self.assertIn("timed out", dialog.list.GetItemText(0))
		finally:
			rulesModule.regexSearch = saved
			dialog.Close()


class TestRemoveMatchedText(unittest.TestCase):
	HELP = r"\s*press\s+enter\s+to\s+explore\s+message\s+content(?:[ \t]*[.!?])*"

	@classmethod
	def setUpClass(cls):
		cls.app = wx.App.Get() or wx.App()
		cls.frame = wx.Frame(None)

	@classmethod
	def tearDownClass(cls):
		cls.frame.Destroy()

	def controllerWith(self, rule):
		controller = Controller()
		controller.load()
		controller.history.clear()
		controller.rules.setRules([rule])
		return controller

	def test_liveRegionReportedWithoutTheHelp(self):
		rule = Rule(
			id="help",
			name="Teams help",
			app="ms-teams",
			matchType="regex",
			pattern=self.HELP,
			action=Action(removeMatch=True),
		)
		controller = self.controllerWith(rule)
		passed = []
		spoken.clear()
		text = "Sam: lunch? Press Enter to explore message content."
		controller.process(rec(text, source=SOURCE_LIVE_REGION, appName="ms-teams"), lambda: passed.append(1))
		self.assertEqual(passed, [])
		said = " ".join(str(s) for s in spoken)
		self.assertIn("Sam: lunch?", said)
		self.assertNotIn("explore", said)
		logged = controller.history.latest()
		self.assertEqual(logged.text, text)
		self.assertEqual(logged.presentedText, "Sam: lunch?")
		from notificationsController.historyDialog import recordDetails

		self.assertIn("Reported as: Sam: lunch?", recordDetails(logged))

	def test_helpAloneIsSilent(self):
		rule = Rule(id="help", matchType="regex", pattern=self.HELP, action=Action(removeMatch=True))
		controller = self.controllerWith(rule)
		passed = []
		spoken.clear()
		controller.process(rec("Press Enter to explore message content."), lambda: passed.append(1))
		self.assertEqual((passed, spoken), ([], []))
		from notificationsController.historyDialog import recordDetails

		self.assertIn("(nothing)", recordDetails(controller.history.latest()))

	def test_ruleEditorCheckbox(self):
		from notificationsController.ruleEditor import RuleEditorDialog

		controller = self.controllerWith(Rule(id="x"))
		rule = Rule(id="r", matchType="contains", pattern="(edited)", action=Action(removeMatch=True))
		dialog = RuleEditorDialog(self.frame, controller, rule, isNew=False)
		try:
			self.assertTrue(dialog.removeMatchCheck.GetValue())
			self.assertTrue(dialog.ruleFromControls().action.removeMatch)
			# Any text has nothing to remove: the option is off and not saved.
			dialog.matchChoice.SetSelection(dialog._matchKeys.index("any"))
			dialog.updateEnabled()
			self.assertFalse(dialog.removeMatchCheck.IsEnabled())
			self.assertFalse(dialog.ruleFromControls().action.removeMatch)
		finally:
			dialog.Destroy()

	def test_labels(self):
		self.assertIn("matched text removed", settings.actionLabel("speechBraille+trim+sound"))
		self.assertIn("with sound", settings.actionLabel("speechBraille+trim+sound"))


if __name__ == "__main__":
	unittest.main(verbosity=2)
