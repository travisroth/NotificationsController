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
import uuid
from collections.abc import Iterable
from typing import Any

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
"""Only this much of a notification's text is matched, so a huge live region cannot stall a regex."""

_whitespace = re.compile(r"\s+")


def normalizeText(text: str) -> str:
	"""Collapse runs of whitespace, including line breaks, to single spaces and trim the ends.

	Both the notification text and a rule's pattern (other than a regular expression) are normalized,
	so a rule written from one line of a multi line toast still matches it.
	"""
	return _whitespace.sub(" ", text or "").strip()


def newRuleId() -> str:
	return uuid.uuid4().hex


def validateRegex(pattern: str, caseSensitive: bool = False) -> str | None:
	"""Return an error message if the pattern does not compile, otherwise None."""
	try:
		re.compile(pattern, 0 if caseSensitive else re.IGNORECASE)
	except re.error as e:
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
		self.regex: re.Pattern[str] | None = None
		self.error: str | None = None
		self.pattern = rule.pattern if rule.matchType == MATCH_REGEX else normalizeText(rule.pattern)
		if not rule.caseSensitive:
			self.pattern = self.pattern.casefold()
		if rule.matchType == MATCH_REGEX:
			try:
				self.regex = re.compile(rule.pattern, 0 if rule.caseSensitive else re.IGNORECASE)
			except re.error as e:
				self.error = str(e)
		self.app = rule.app.strip().casefold()
		self.urlPrefix = rule.urlPrefix.strip().casefold()

	def matches(self, record: NotificationRecord) -> bool:
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
		return self.matchesText(record.text)

	def matchesText(self, text: str) -> bool:
		matchType = self.rule.matchType
		if matchType == MATCH_ANY:
			return True
		text = text[:MAX_MATCH_TEXT]
		if matchType == MATCH_REGEX:
			return bool(self.regex and self.regex.search(normalizeText(text)))
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
		for compiled in self._compiled:
			if compiled.matches(record):
				return compiled.rule
		return None

	def get(self, ruleId: str) -> Rule | None:
		for rule in self._rules:
			if rule.id == ruleId:
				return rule
		return None

	def errors(self) -> dict[str, str]:
		"""Rules whose regular expression does not compile, by rule id."""
		return {c.rule.id: c.error for c in self._compiled if c.error}

	# Persistence

	@staticmethod
	def rulesFromJson(data: Any) -> list[Rule]:
		"""Read rules from parsed JSON: either the saved file format or a bare list of rules."""
		items = data.get("rules", []) if isinstance(data, dict) else data
		if not isinstance(items, list):
			raise ValueError("No list of rules found")  # noqa: TRY004 - callers handle bad files as ValueError
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
