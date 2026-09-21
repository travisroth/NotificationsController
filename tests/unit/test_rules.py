# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for the rule engine: text matching, app and site scoping, ordering and saving."""

import json
import os
import tempfile
import unittest

from notificationsController import rules as rulesModule
from notificationsController.models import (
	MATCH_ANY,
	MATCH_CONTAINS,
	MATCH_EQUALS,
	MATCH_REGEX,
	MATCH_STARTS_WITH,
	OUTPUT_NONE,
	POLITENESS_ASSERTIVE,
	SOURCE_LIVE_REGION,
	SOURCE_TOAST,
	SOURCE_UIA,
	Action,
	NotificationRecord,
	Rule,
	domainFromUrl,
	siteDomain,
	toastText,
)
from notificationsController.rules import (
	RuleSet,
	domainMatches,
	normalizeText,
	validateRegex,
)


def record(text="Hello world", **kwargs):
	kwargs.setdefault("source", SOURCE_UIA)
	kwargs.setdefault("appName", "ms-teams")
	return NotificationRecord(timestamp=0.0, text=text, **kwargs)


def rule(**kwargs):
	kwargs.setdefault("id", kwargs.get("name", "r"))
	return Rule(**kwargs)


class TestTextMatching(unittest.TestCase):
	def check(self, matchType, pattern, text, caseSensitive=False):
		rules = RuleSet([rule(matchType=matchType, pattern=pattern, caseSensitive=caseSensitive)])
		return rules.match(record(text)) is not None

	def test_any(self):
		self.assertTrue(self.check(MATCH_ANY, "", "anything"))

	def test_startsWith(self):
		self.assertTrue(self.check(MATCH_STARTS_WITH, "hello", "Hello world"))
		self.assertFalse(self.check(MATCH_STARTS_WITH, "world", "Hello world"))

	def test_startsWithCaseSensitive(self):
		self.assertFalse(self.check(MATCH_STARTS_WITH, "hello", "Hello world", caseSensitive=True))
		self.assertTrue(self.check(MATCH_STARTS_WITH, "Hello", "Hello world", caseSensitive=True))

	def test_equals(self):
		self.assertTrue(self.check(MATCH_EQUALS, "hello world", "Hello world"))
		self.assertFalse(self.check(MATCH_EQUALS, "hello", "Hello world"))

	def test_contains(self):
		self.assertTrue(self.check(MATCH_CONTAINS, "LO WO", "Hello world"))
		self.assertFalse(self.check(MATCH_CONTAINS, "goodbye", "Hello world"))

	def test_whitespaceIsNormalized(self):
		self.assertTrue(self.check(MATCH_STARTS_WITH, "New  message from", "New message\nfrom Sam"))
		self.assertTrue(self.check(MATCH_EQUALS, " a b ", "a\t\tb"))

	def test_regex(self):
		self.assertTrue(self.check(MATCH_REGEX, r"^\d+ new messages?$", "3 new messages"))
		self.assertTrue(self.check(MATCH_REGEX, r"NEW", "3 new messages"))
		self.assertFalse(self.check(MATCH_REGEX, r"NEW", "3 new messages", caseSensitive=True))

	def test_invalidRegexNeverMatchesAndIsReported(self):
		rules = RuleSet([rule(id="bad", matchType=MATCH_REGEX, pattern="(")])
		self.assertIsNone(rules.match(record("(")))
		self.assertIn("bad", rules.errors())
		self.assertIsNotNone(validateRegex("("))
		self.assertIsNone(validateRegex("a+"))

	def test_normalizeText(self):
		self.assertEqual(normalizeText("  a \r\n b  "), "a b")


class TestRegexTimeout(unittest.TestCase):
	def test_timeoutTurnsRuleOffAndLaterRulesStillWork(self):
		errors = []
		rules = RuleSet(
			[
				rule(id="slow", name="Slow", matchType=MATCH_REGEX, pattern="anything"),
				rule(id="next", matchType=MATCH_ANY),
			],
		)
		rules.onRuleError = lambda r, error: errors.append((r.id, error))

		def timeOut(compiled, text, timeout=rulesModule.REGEX_TIMEOUT):
			raise TimeoutError

		original = rulesModule.regexSearch
		rulesModule.regexSearch = timeOut
		try:
			# A timeout stops matching: NVDA handles this notification as usual.
			self.assertIsNone(rules.match(record("x")))
			self.assertEqual(errors, [("slow", rulesModule.REGEX_TIMEOUT_ERROR)])
			# The slow rule stays off, so the next notification costs no timeout and reaches later rules.
			self.assertEqual(rules.match(record("y")).id, "next")
			self.assertEqual(len(errors), 1)
			self.assertEqual(rules.errors(), {"slow": rulesModule.REGEX_TIMEOUT_ERROR})
		finally:
			rulesModule.regexSearch = original

	def test_budgetCutsShortWithoutBlamingTheRule(self):
		import time

		errors = []
		rules = RuleSet(
			[
				rule(id="fairlySlow", matchType=MATCH_REGEX, pattern="first"),
				rule(id="second", matchType=MATCH_REGEX, pattern="second"),
				rule(id="next", matchType=MATCH_ANY),
			],
		)
		rules.onRuleError = lambda r, error: errors.append(r.id)
		timeouts = []

		def search(compiled, text, timeout=rulesModule.REGEX_TIMEOUT):
			timeouts.append(timeout)
			if compiled.pattern == "first":
				# Just inside its own limit, but most of the notification's budget.
				time.sleep(rulesModule.REGEX_TIMEOUT * 0.9)
				return False
			raise TimeoutError

		original = rulesModule.regexSearch
		rulesModule.regexSearch = search
		try:
			self.assertIsNone(rules.match(record("x")))
		finally:
			rulesModule.regexSearch = original
		# The second rule only got what was left of the budget, so it is not turned off.
		self.assertLess(timeouts[1], rulesModule.REGEX_TIMEOUT)
		self.assertEqual(errors, [])
		self.assertEqual(rules.errors(), {})

	@unittest.skipUnless(rulesModule.HAS_TIMEOUT, "needs the regex package, which NVDA ships")
	def test_catastrophicBacktrackingIsStopped(self):
		import time

		# Exponential in the regex engine. (^(a+)+b is not: regex optimizes that one.)
		rules = RuleSet(
			[
				rule(id="evil", matchType=MATCH_REGEX, pattern=r"^(a|aa)+$"),
				rule(id="next", matchType=MATCH_ANY),
			],
		)
		start = time.monotonic()
		self.assertIsNone(rules.match(record("a" * 60 + "b")))
		self.assertLess(time.monotonic() - start, 5)
		self.assertIn("evil", rules.errors())
		self.assertEqual(rules.match(record("a" * 60 + "b")).id, "next")

	@unittest.skipUnless(rulesModule.HAS_TIMEOUT, "needs the regex package, which NVDA ships")
	def test_manySlowRulesShareOneBudget(self):
		import time

		rules = RuleSet(
			[rule(id=f"evil{i}", matchType=MATCH_REGEX, pattern=r"^(a|aa)+$") for i in range(20)]
			+ [rule(id="next", matchType=MATCH_ANY)],
		)
		text = "a" * 60 + "b"
		start = time.monotonic()
		self.assertIsNone(rules.match(record(text)))
		elapsed = time.monotonic() - start
		self.assertLess(elapsed, 0.2)
		# Only the rule that used its whole allowance is turned off; each notification costs one timeout.
		self.assertEqual(list(rules.errors()), ["evil0"])


class TestScoping(unittest.TestCase):
	def test_appMatchesModuleNameOrDisplayName(self):
		rules = RuleSet([rule(app="Microsoft Teams", matchType=MATCH_ANY)])
		self.assertIsNotNone(rules.match(record(appName="ms-teams", appDisplayName="Microsoft Teams")))
		self.assertIsNone(rules.match(record(appName="chrome")))
		rules = RuleSet([rule(app="MS-TEAMS", matchType=MATCH_ANY)])
		self.assertIsNotNone(rules.match(record(appName="ms-teams")))

	def test_emptyAppMatchesAnyApp(self):
		rules = RuleSet([rule(matchType=MATCH_ANY)])
		self.assertIsNotNone(rules.match(record(appName="whatever")))

	def test_source(self):
		rules = RuleSet([rule(source=SOURCE_TOAST, matchType=MATCH_ANY)])
		self.assertIsNone(rules.match(record(source=SOURCE_UIA)))
		self.assertIsNotNone(rules.match(record(source=SOURCE_TOAST)))

	def test_domainWithSubdomains(self):
		rules = RuleSet([rule(domain="example.com", matchType=MATCH_ANY)])
		self.assertIsNotNone(rules.match(record(domain="example.com")))
		self.assertIsNotNone(rules.match(record(domain="app.example.com")))
		self.assertIsNone(rules.match(record(domain="badexample.com")))
		self.assertIsNone(rules.match(record(domain="")))

	def test_domainWithoutSubdomains(self):
		rules = RuleSet([rule(domain="example.com", includeSubdomains=False, matchType=MATCH_ANY)])
		self.assertIsNotNone(rules.match(record(domain="example.com")))
		self.assertIsNone(rules.match(record(domain="www.example.com")))

	def test_domainTypedAsUrl(self):
		self.assertTrue(domainMatches("www.example.com", "https://Example.com/path", True))

	def test_urlPrefix(self):
		rules = RuleSet([rule(urlPrefix="https://example.com/app/", matchType=MATCH_ANY)])
		self.assertIsNotNone(rules.match(record(url="https://EXAMPLE.com/app/inbox")))
		self.assertIsNone(rules.match(record(url="https://example.com/other")))
		self.assertIsNone(rules.match(record(url="")))

	def test_activityIdAndPoliteness(self):
		rules = RuleSet([rule(activityId="Volume", matchType=MATCH_ANY)])
		self.assertIsNone(rules.match(record(activityId="Other")))
		self.assertIsNotNone(rules.match(record(activityId="Volume")))
		rules = RuleSet([rule(politeness=POLITENESS_ASSERTIVE, matchType=MATCH_ANY)])
		self.assertIsNone(rules.match(record(source=SOURCE_LIVE_REGION, politeness="polite")))
		self.assertIsNotNone(rules.match(record(source=SOURCE_LIVE_REGION, politeness="assertive")))


class TestOrdering(unittest.TestCase):
	def test_firstEnabledMatchWins(self):
		rules = RuleSet(
			[
				rule(id="disabled", enabled=False, matchType=MATCH_ANY),
				rule(id="first", matchType=MATCH_STARTS_WITH, pattern="hello"),
				rule(id="second", matchType=MATCH_ANY),
			],
		)
		self.assertEqual(rules.match(record("hello there")).id, "first")
		self.assertEqual(rules.match(record("bye")).id, "second")

	def test_recompileAfterEdit(self):
		r = rule(matchType=MATCH_STARTS_WITH, pattern="a")
		rules = RuleSet([r])
		r.pattern = "b"
		rules.recompile()
		self.assertIsNotNone(rules.match(record("b")))


class TestDomains(unittest.TestCase):
	def test_domainFromUrl(self):
		self.assertEqual(domainFromUrl("https://Mail.Example.com:443/x?y"), "mail.example.com")
		self.assertEqual(domainFromUrl("example.com"), "example.com")
		self.assertEqual(domainFromUrl(""), "")
		self.assertEqual(domainFromUrl("about:blank"), "")

	def test_siteDomain(self):
		self.assertEqual(siteDomain("www.example.com"), "example.com")
		self.assertEqual(siteDomain("app.example.com"), "app.example.com")


class TestToastText(unittest.TestCase):
	def test_usesParts(self):
		self.assertEqual(
			toastText("New notification from X, Title, Body.. 1 of 1", ["Title", "Body", " "]),
			"Title, Body",
		)

	def test_cleansSpokenNameWithoutParts(self):
		self.assertEqual(
			toastText("New notification from Windows PowerShell, Notification tester, Test 1.. 1 of 1", []),
			"Notification tester, Test 1",
		)
		self.assertEqual(toastText("Something else. 2 of 3", []), "Something else")
		self.assertEqual(toastText("Plain", []), "Plain")


class TestPersistence(unittest.TestCase):
	def test_roundTrip(self):
		original = RuleSet(
			[
				rule(
					id="x",
					name="Quiet site",
					domain="example.com",
					source=SOURCE_LIVE_REGION,
					action=Action(output=OUTPUT_NONE, sound="builtin:chime"),
					log=False,
				),
			],
		)
		with tempfile.TemporaryDirectory() as d:
			path = os.path.join(d, "sub", "rules.json")
			original.save(path)
			with open(path, encoding="utf-8") as f:
				self.assertEqual(json.load(f)["schemaVersion"], 1)
			loaded = RuleSet.load(path)
		self.assertEqual(loaded.rules, original.rules)
		self.assertIsInstance(loaded.rules[0].action, Action)

	def test_missingFileIsEmpty(self):
		self.assertEqual(RuleSet.load(os.path.join(tempfile.gettempdir(), "nope-nc.json")).rules, [])

	def test_importBareListAndUnknownKeys(self):
		rules = RuleSet.rulesFromJson([{"name": "a", "future": 1, "action": {"output": "none", "x": 2}}, 5])
		self.assertEqual(len(rules), 1)
		self.assertTrue(rules[0].id)
		self.assertEqual(rules[0].action.output, OUTPUT_NONE)

	def test_wrongTypesKeepDefaults(self):
		rules = RuleSet.rulesFromJson(
			[
				{
					"id": "x",
					"app": None,
					"pattern": 5,
					"enabled": "yes",
					"action": {"output": None, "sound": 3},
				}
			],
		)
		rule = rules[0]
		self.assertEqual((rule.app, rule.pattern, rule.enabled), ("", "", True))
		self.assertEqual((rule.action.output, rule.action.sound), ("default", ""))
		# The rule set compiles and matches without errors.
		self.assertIs(RuleSet(rules).match(record("x")), rule)

	def test_importRejectsNonList(self):
		with self.assertRaises(ValueError):
			RuleSet.rulesFromJson({"rules": "nope"})


if __name__ == "__main__":
	unittest.main()
