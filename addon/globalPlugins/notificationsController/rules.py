# NotificationsController: rules.py
# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""The rule engine: matching notifications against the user's ordered rule list, and saving the rules.

Rules are checked from the top of the list down and the first enabled rule that matches wins.
Nothing here imports NVDA.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from collections.abc import Callable, Iterable
from typing import Any

try:
	# NVDA ships the regex package. Unlike re, it can stop a search that runs too long, so a badly written
	# pattern (catastrophic backtracking, such as ^(a+)+b) cannot freeze NVDA's main thread.
	import regex as _engine

	HAS_TIMEOUT = True
except ImportError:
	# Only when running outside NVDA, such as in plain unit tests.
	_engine = re
	HAS_TIMEOUT = False

from .models import (
	MATCH_ANY,
	MATCH_CONTAINS,
	MATCH_EQUALS,
	MATCH_REGEX,
	MATCH_STARTS_WITH,
	POLITENESS_ANY,
	SOURCE_ANY,
	NotificationRecord,
	Rule,
	domainFromUrl,
)

SCHEMA_VERSION = 1

MAX_MATCH_TEXT = 4000
"""Only this much of a notification's text is matched. A second safeguard behind REGEX_TIMEOUT."""

REGEX_TIMEOUT = 0.1
"""Seconds one regular expression may spend on one notification before its rule is turned off."""

REGEX_BUDGET = 0.15
"""Seconds all regular expressions together may spend on one notification.

Without a total, many slow rules would each use their own REGEX_TIMEOUT, one after another. When the
budget runs out, or any search times out, matching stops and the notification is left to NVDA, which is
the safest outcome."""

REGEX_TIMEOUT_ERROR = "timed out"

_whitespace = re.compile(r"\s+")


def normalizeText(text: str) -> str:
	"""Collapse runs of whitespace, including line breaks, to single spaces and trim the ends.

	Both the notification text and a rule's pattern (other than a regular expression) are normalized,
	so a rule written from one line of a multi line toast still matches it.
	"""
	return _whitespace.sub(" ", text or "").strip()


def newRuleId() -> str:
	return uuid.uuid4().hex


def compileRegex(pattern: str, caseSensitive: bool = False):
	"""Compile a rule's regular expression with the engine used for matching. Raises ValueError."""
	try:
		return _engine.compile(pattern, 0 if caseSensitive else _engine.IGNORECASE)
	except _engine.error as e:
		raise ValueError(str(e)) from e


def regexSearch(compiled, text: str, timeout: float = REGEX_TIMEOUT) -> bool:
	"""Search with a time limit. Raises TimeoutError when the search takes too long."""
	if HAS_TIMEOUT:
		return bool(compiled.search(text, timeout=timeout))
	return bool(compiled.search(text))


def validateRegex(pattern: str, caseSensitive: bool = False) -> str | None:
	"""Return an error message if the pattern does not compile, otherwise None.

	This only checks the syntax. A pattern can compile and still be too slow; that is caught while
	matching, by REGEX_TIMEOUT.
	"""
	try:
		compileRegex(pattern, caseSensitive)
	except ValueError as e:
		return str(e)
	return None


def domainMatches(recordDomain: str, ruleDomain: str, includeSubdomains: bool) -> bool:
	recordDomain = recordDomain.lower()
	ruleDomain = domainFromUrl(ruleDomain)
	if not recordDomain or not ruleDomain:
		return False
	if recordDomain == ruleDomain:
		return True
	return includeSubdomains and recordDomain.endswith("." + ruleDomain)


class CompiledRule:
	"""A rule with its pattern prepared for fast matching."""

	def __init__(self, rule: Rule):
		self.rule = rule
		self.regex: Any = None
		self.error: str | None = None
		self.pattern = rule.pattern if rule.matchType == MATCH_REGEX else normalizeText(rule.pattern)
		if not rule.caseSensitive:
			self.pattern = self.pattern.casefold()
		if rule.matchType == MATCH_REGEX:
			try:
				self.regex = compileRegex(rule.pattern, rule.caseSensitive)
			except ValueError as e:
				self.error = str(e)
		self.app = rule.app.strip().casefold()
		self.urlPrefix = rule.urlPrefix.strip().casefold()

	def matches(self, record: NotificationRecord, timeout: float = REGEX_TIMEOUT) -> bool:
		"""Whether the rule matches.

		:param timeout: The most time its regular expression may take.
		:raises TimeoutError: When the regular expression takes longer.
		"""
		rule = self.rule
		if not rule.enabled or self.error:
			return False
		if rule.source != SOURCE_ANY and rule.source != record.source:
			return False
		if self.app and self.app not in (record.appName.casefold(), record.appDisplayName.casefold()):
			return False
		if rule.domain and not domainMatches(record.domain, rule.domain, rule.includeSubdomains):
			return False
		if self.urlPrefix and not record.url.casefold().startswith(self.urlPrefix):
			return False
		if rule.activityId and rule.activityId != record.activityId:
			return False
		if rule.politeness != POLITENESS_ANY and rule.politeness != record.politeness:
			return False
		return self.matchesText(record.text, timeout)

	def matchesText(self, text: str, timeout: float = REGEX_TIMEOUT) -> bool:
		matchType = self.rule.matchType
		if matchType == MATCH_ANY:
			return True
		text = text[:MAX_MATCH_TEXT]
		if matchType == MATCH_REGEX:
			return bool(self.regex) and regexSearch(self.regex, normalizeText(text), timeout)
		text = normalizeText(text)
		if not self.rule.caseSensitive:
			text = text.casefold()
		if matchType == MATCH_EQUALS:
			return text == self.pattern
		if matchType == MATCH_STARTS_WITH:
			return text.startswith(self.pattern)
		if matchType == MATCH_CONTAINS:
			return self.pattern in text
		return False


class RuleSet:
	"""The ordered list of rules, with loading and saving."""

	def __init__(self, rules: Iterable[Rule] = ()):
		self._rules: list[Rule] = list(rules)
		self._compiled: list[CompiledRule] = []
		self.onRuleError: Callable[[Rule, str], None] | None = None
		"""Called when a rule is turned off while matching, such as a regular expression timing out."""
		self.recompile()

	@property
	def rules(self) -> list[Rule]:
		"""The rules in evaluation order. Call recompile after changing a rule in place."""
		return self._rules

	def recompile(self) -> None:
		self._compiled = [CompiledRule(r) for r in self._rules]

	def setRules(self, rules: Iterable[Rule]) -> None:
		self._rules = list(rules)
		self.recompile()

	def match(self, record: NotificationRecord) -> Rule | None:
		"""The first enabled rule that matches, or None.

		Regular expressions share REGEX_BUDGET per notification. When a search times out, or the budget
		is used up, matching stops and None is returned, so NVDA handles the notification as usual. A rule
		whose search used its whole REGEX_TIMEOUT is turned off until the rules change, so it costs one
		timeout, not one per notification. A search cut short only by the budget turns nothing off.
		"""
		deadline = time.monotonic() + REGEX_BUDGET
		for compiled in self._compiled:
			timeout = REGEX_TIMEOUT
			if compiled.regex is not None:
				remaining = deadline - time.monotonic()
				if remaining <= 0:
					return None
				timeout = min(REGEX_TIMEOUT, remaining)
			try:
				if compiled.matches(record, timeout):
					return compiled.rule
			except TimeoutError:
				if timeout >= REGEX_TIMEOUT:
					compiled.error = REGEX_TIMEOUT_ERROR
					if self.onRuleError:
						self.onRuleError(compiled.rule, REGEX_TIMEOUT_ERROR)
				return None
		return None

	def get(self, ruleId: str) -> Rule | None:
		for rule in self._rules:
			if rule.id == ruleId:
				return rule
		return None

	def errors(self) -> dict[str, str]:
		"""Rules whose regular expression does not compile or timed out, by rule id."""
		return {c.rule.id: c.error for c in self._compiled if c.error}

	# Persistence

	@staticmethod
	def rulesFromJson(data: Any) -> list[Rule]:
		"""Read rules from parsed JSON: either the saved file format or a bare list of rules."""
		items = data.get("rules", []) if isinstance(data, dict) else data
		if not isinstance(items, list):
			raise ValueError("No list of rules found")
		rules: list[Rule] = []
		for item in items:
			if not isinstance(item, dict):
				continue
			rule = Rule.fromDict(item)
			if not rule.id:
				rule.id = newRuleId()
			rules.append(rule)
		return rules

	@staticmethod
	def rulesToJson(rules: Iterable[Rule]) -> dict[str, Any]:
		return {"schemaVersion": SCHEMA_VERSION, "rules": [r.toDict() for r in rules]}

	@classmethod
	def load(cls, path: str) -> RuleSet:
		"""Load rules from a file. A missing file gives an empty rule set; a damaged one raises."""
		if not os.path.isfile(path):
			return cls()
		with open(path, encoding="utf-8") as f:
			data = json.load(f)
		return cls(cls.rulesFromJson(data))

	def save(self, path: str) -> None:
		writeJsonAtomic(path, self.rulesToJson(self._rules))


def writeJsonAtomic(path: str, data: Any) -> None:
	"""Write JSON to a temporary file and then replace the target, so a crash cannot leave half a file."""
	os.makedirs(os.path.dirname(path), exist_ok=True)
	tmp = path + ".tmp"
	with open(tmp, "w", encoding="utf-8") as f:
		json.dump(data, f, ensure_ascii=False, indent="\t")
	os.replace(tmp, path)
