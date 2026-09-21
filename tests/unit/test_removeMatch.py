# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for rules that remove the text they match and report the rest."""

import unittest

from notificationsController import rules as rulesModule
from notificationsController.models import (
	ACTION_PASSTHROUGH,
	MATCH_ANY,
	MATCH_CONTAINS,
	MATCH_EQUALS,
	MATCH_REGEX,
	MATCH_STARTS_WITH,
	OUTPUT_BRAILLE,
	OUTPUT_DEFAULT,
	OUTPUT_NONE,
	OUTPUT_SPEECH_BRAILLE,
	SOURCE_LIVE_REGION,
	Action,
	NotificationRecord,
	Rule,
)
from notificationsController.policy import decide
from notificationsController.rules import CompiledRule, RuleSet, tidyRemainder

# The help Teams adds to its chat live regions, as the ms-teams add-on matches it.
TEAMS_HELP = (
	r"\s*press\s+enter\s+to\s+explore\s+message\s+content"
	r"(?:,?\s*then\s+use\s+escape\s+to\s+shift\s+focus\s+back(?:\s+to\s+the\s+message)?)?"
	r"(?:[ \t]*[.!?])*"
)


def record(text, **kwargs):
	kwargs.setdefault("source", SOURCE_LIVE_REGION)
	kwargs.setdefault("appName", "ms-teams")
	return NotificationRecord(timestamp=0.0, text=text, **kwargs)


def removing(matchType, pattern, caseSensitive=False, output=OUTPUT_DEFAULT):
	return Rule(
		id="r",
		matchType=matchType,
		pattern=pattern,
		caseSensitive=caseSensitive,
		action=Action(output=output, removeMatch=True),
	)


def remove(matchType, pattern, text, caseSensitive=False):
	return CompiledRule(removing(matchType, pattern, caseSensitive)).removeMatched(text)


class TestRemoval(unittest.TestCase):
	def test_startsWithRemovesOnlyTheStart(self):
		self.assertEqual(
			remove(MATCH_STARTS_WITH, "new message:", "New message: lunch? new message:"),
			"lunch? new message",
		)

	def test_containsRemovesEveryOccurrenceIgnoringCase(self):
		self.assertEqual(remove(MATCH_CONTAINS, "(edited)", "Hi (EDITED) there (edited)"), "Hi there")

	def test_containsCaseSensitive(self):
		self.assertEqual(
			remove(MATCH_CONTAINS, "Draft", "draft Draft saved", caseSensitive=True), "draft saved"
		)

	def test_containsTreatsPatternLiterally(self):
		self.assertEqual(remove(MATCH_CONTAINS, "a+b", "x a+b y aab"), "x y aab")

	def test_equalsLeavesNothing(self):
		self.assertEqual(remove(MATCH_EQUALS, "7 results", "7  results"), "")
		self.assertEqual(remove(MATCH_EQUALS, "7 results", "8 results"), "8 results")

	def test_regexStripsTeamsHelp(self):
		text = "Sam Smith: are you joining? Press Enter to explore message content, then use Escape to shift focus back to the message."
		self.assertEqual(remove(MATCH_REGEX, TEAMS_HELP, text), "Sam Smith: are you joining?")

	def test_regexHelpAloneLeavesNothing(self):
		self.assertEqual(remove(MATCH_REGEX, TEAMS_HELP, "Press Enter to explore message content."), "")

	def test_anyTextRemovesNothing(self):
		self.assertEqual(remove(MATCH_ANY, "", "Hello  there"), "Hello there")

	def test_tidy(self):
		self.assertEqual(tidyRemainder(" , Hello ,"), "Hello")
		self.assertEqual(tidyRemainder("Saved ."), "Saved.")
		self.assertEqual(tidyRemainder(" . ! "), "")
		self.assertEqual(tidyRemainder("- — 7"), "7")


class TestRuleSetRemoval(unittest.TestCase):
	def test_timeoutTurnsRuleOffAndGivesNone(self):
		rule = removing(MATCH_REGEX, "x")
		rules = RuleSet([rule])
		errors = []
		rules.onRuleError = lambda r, error: errors.append(r.id)

		def timeOut(compiled, text, timeout=rulesModule.REGEX_TIMEOUT):
			raise TimeoutError

		original = rulesModule.regexRemove
		rulesModule.regexRemove = timeOut
		try:
			self.assertIsNone(rules.removeMatched(rule, "x"))
		finally:
			rulesModule.regexRemove = original
		self.assertEqual(errors, ["r"])
		self.assertIn("r", rules.errors())

	def test_unknownRuleGivesNone(self):
		self.assertIsNone(RuleSet([]).removeMatched(removing(MATCH_CONTAINS, "x"), "x"))


class TestDecision(unittest.TestCase):
	def test_defaultOutputBecomesSpeechAndBraille(self):
		r = record("Hello (edited)")
		rule = removing(MATCH_CONTAINS, "(edited)")
		d = decide(r, rule, remainder="Hello")
		self.assertFalse(d.passthrough)
		self.assertEqual(d.present.output, OUTPUT_SPEECH_BRAILLE)
		self.assertEqual(d.text, "Hello")
		self.assertEqual(d.actionKey, "speechBraille+trim")
		self.assertEqual(r.presentedText, "Hello")

	def test_chosenOutputIsKept(self):
		rule = removing(MATCH_CONTAINS, "x", output=OUTPUT_BRAILLE)
		d = decide(record("a x"), rule, remainder="a")
		self.assertEqual(d.present.output, OUTPUT_BRAILLE)

	def test_nothingLeftIsSilentButSoundStillPlays(self):
		rule = removing(MATCH_EQUALS, "7 results")
		rule.action.sound = "builtin:blip"
		d = decide(record("7 results"), rule, remainder="")
		self.assertFalse(d.passthrough)
		self.assertIsNone(d.present)
		self.assertEqual(d.sound, "builtin:blip")
		self.assertEqual(d.actionKey, "none+trim+sound")

	def test_failedRemovalLeavesItToNvda(self):
		d = decide(record("x"), removing(MATCH_REGEX, "x"), remainder=None)
		self.assertTrue(d.passthrough)
		self.assertEqual(d.actionKey, ACTION_PASSTHROUGH)

	def test_doNotDisturbAndNvdaSettingStillWin(self):
		rule = removing(MATCH_CONTAINS, "x")
		self.assertIsNone(decide(record("a x"), rule, doNotDisturb=True, remainder="a").present)
		d = decide(record("a x"), rule, liveRegionsOn=False, remainder="a")
		self.assertIsNone(d.present)
		self.assertTrue(d.passthrough)

	def test_removeMatchIsNotPassthrough(self):
		self.assertFalse(Action(output=OUTPUT_DEFAULT, removeMatch=True).isPassthrough)
		self.assertEqual(Action(output=OUTPUT_NONE, removeMatch=True).describe(), "none+trim")

	def test_savedAndLoaded(self):
		rule = removing(MATCH_CONTAINS, "x")
		loaded = RuleSet.rulesFromJson(RuleSet.rulesToJson([rule]))[0]
		self.assertTrue(loaded.action.removeMatch)
		old = RuleSet.rulesFromJson([{"id": "o", "action": {"output": "speech"}}])[0]
		self.assertFalse(old.action.removeMatch)


if __name__ == "__main__":
	unittest.main()
