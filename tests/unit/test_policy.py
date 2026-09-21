# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for deciding what happens to a notification, and for spotting duplicates."""

import unittest

from notificationsController.models import (
	ACTION_DISABLED_BY_NVDA,
	ACTION_DO_NOT_DISTURB,
	ACTION_PASSTHROUGH,
	CATEGORY_IMPORTANT,
	CATEGORY_SPAM,
	CATEGORY_UNCLASSIFIED,
	OUTPUT_DEFAULT,
	OUTPUT_NONE,
	OUTPUT_SPEECH,
	SOURCE_LIVE_REGION,
	SOURCE_TOAST,
	SOURCE_UIA,
	Action,
	NotificationRecord,
	Rule,
)
from notificationsController.policy import Deduper, decide


def rec(text="hi", timestamp=100.0, source=SOURCE_UIA, appName="app"):
	return NotificationRecord(timestamp=timestamp, source=source, text=text, appName=appName)


class TestDecide(unittest.TestCase):
	def test_noRuleIsPassthroughAndLogged(self):
		record = rec()
		d = decide(record, None)
		self.assertTrue(d.passthrough)
		self.assertIsNone(d.present)
		self.assertTrue(d.log)
		self.assertEqual(d.actionKey, ACTION_PASSTHROUGH)
		self.assertEqual(record.category, CATEGORY_UNCLASSIFIED)

	def test_ruleFillsRecord(self):
		record = rec()
		rule = Rule(id="r1", name="Quiet", category=CATEGORY_SPAM, action=Action(OUTPUT_NONE), log=False)
		d = decide(record, rule)
		self.assertFalse(d.passthrough)
		self.assertFalse(d.log)
		self.assertEqual((record.matchedRuleId, record.matchedRuleName), ("r1", "Quiet"))
		self.assertEqual(record.category, CATEGORY_SPAM)
		self.assertEqual(d.actionKey, "none")

	def test_defaultWithSound(self):
		d = decide(rec(), Rule(action=Action(OUTPUT_DEFAULT, "builtin:chime")))
		self.assertTrue(d.passthrough)
		self.assertEqual(d.sound, "builtin:chime")
		self.assertEqual(d.actionKey, "default+sound")

	def test_speechOnly(self):
		d = decide(rec(), Rule(action=Action(OUTPUT_SPEECH)))
		self.assertFalse(d.passthrough)
		self.assertEqual(d.present.output, OUTPUT_SPEECH)
		self.assertEqual(d.actionKey, "speech")

	def test_doNotDisturbSilencesAllButImportant(self):
		d = decide(rec(), None, doNotDisturb=True)
		self.assertFalse(d.passthrough)
		self.assertIsNone(d.present)
		self.assertEqual(d.sound, "")
		self.assertEqual(d.actionKey, ACTION_DO_NOT_DISTURB)
		d = decide(rec(), Rule(category=CATEGORY_IMPORTANT, action=Action(OUTPUT_SPEECH)), doNotDisturb=True)
		self.assertIsNotNone(d.present)

	def test_liveRegionsOffInNvda(self):
		d = decide(
			rec(source=SOURCE_LIVE_REGION),
			Rule(action=Action(OUTPUT_SPEECH, "builtin:blip")),
			liveRegionsOn=False,
		)
		self.assertTrue(d.passthrough)
		self.assertIsNone(d.present)
		self.assertEqual(d.sound, "")
		self.assertEqual(d.actionKey, ACTION_DISABLED_BY_NVDA)
		# Other sources are not affected.
		d = decide(rec(), Rule(action=Action(OUTPUT_SPEECH)), liveRegionsOn=False)
		self.assertIsNotNone(d.present)


class TestDeduper(unittest.TestCase):
	def test_sameTextSoonIsDuplicate(self):
		dd = Deduper(0.5)
		self.assertFalse(dd.isDuplicate(rec("a", 100.0)))
		self.assertTrue(dd.isDuplicate(rec("a", 100.3)))
		self.assertFalse(dd.isDuplicate(rec("a", 101.0)))

	def test_spacingDifferencesAreDuplicates(self):
		dd = Deduper(0.5)
		dd.isDuplicate(rec("Running", 100.0))
		self.assertTrue(dd.isDuplicate(rec("Running ", 100.0)))
		self.assertTrue(dd.isDuplicate(rec(" Running\n", 100.1)))

	def test_toastsFromDifferentSendersAreNotDuplicates(self):
		dd = Deduper(0.5)
		first = rec("Meeting starts now", 100.0, source=SOURCE_TOAST, appName="shellexperiencehost")
		first.appDisplayName = "Outlook"
		second = rec("Meeting starts now", 100.1, source=SOURCE_TOAST, appName="shellexperiencehost")
		second.appDisplayName = "Teams"
		self.assertFalse(dd.isDuplicate(first))
		self.assertFalse(dd.isDuplicate(second))

	def test_sameTextFromDifferentKindsIsNotDuplicate(self):
		dd = Deduper(0.5)
		dd.isDuplicate(rec("Saved", 100.0))
		self.assertFalse(dd.isDuplicate(rec("Saved", 100.1, source=SOURCE_LIVE_REGION)))

	def test_differentTextOrApp(self):
		dd = Deduper(0.5)
		dd.isDuplicate(rec("a", 100.0))
		self.assertFalse(dd.isDuplicate(rec("b", 100.1)))
		self.assertFalse(dd.isDuplicate(rec("b", 100.2, appName="other")))

	def test_disabled(self):
		dd = Deduper(0)
		dd.isDuplicate(rec("a", 100.0))
		self.assertFalse(dd.isDuplicate(rec("a", 100.0)))


if __name__ == "__main__":
	unittest.main()
