# NotificationsController: models.py
# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Data types shared by the capture code, the rule engine, the history store and the GUI.

Nothing here imports NVDA, so the rule engine and history store can be tested with plain Python.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

# Where a notification came from.
SOURCE_UIA = "uia"
SOURCE_TOAST = "toast"
SOURCE_LIVE_REGION = "liveRegion"
SOURCES = (SOURCE_UIA, SOURCE_TOAST, SOURCE_LIVE_REGION)
SOURCE_ANY = "any"

# Categories. Rules may also use any other text as a custom category.
CATEGORY_UNCLASSIFIED = "unclassified"
CATEGORY_IMPORTANT = "important"
CATEGORY_INFORMATIONAL = "informational"
CATEGORY_SPAM = "spam"
BUILTIN_CATEGORIES = (CATEGORY_IMPORTANT, CATEGORY_INFORMATIONAL, CATEGORY_SPAM)

# How rule text is matched.
MATCH_ANY = "any"
MATCH_EQUALS = "equals"
MATCH_STARTS_WITH = "startsWith"
MATCH_CONTAINS = "contains"
MATCH_REGEX = "regex"
MATCH_TYPES = (MATCH_ANY, MATCH_EQUALS, MATCH_STARTS_WITH, MATCH_CONTAINS, MATCH_REGEX)

# Live region politeness.
POLITENESS_ANY = "any"
POLITENESS_POLITE = "polite"
POLITENESS_ASSERTIVE = "assertive"

# Speech and braille output chosen by a rule.
OUTPUT_DEFAULT = "default"
"""Let NVDA present the notification as it normally would."""
OUTPUT_SPEECH_BRAILLE = "speechBraille"
OUTPUT_SPEECH = "speech"
OUTPUT_BRAILLE = "braille"
OUTPUT_NONE = "none"
OUTPUT_MODES = (OUTPUT_DEFAULT, OUTPUT_SPEECH_BRAILLE, OUTPUT_SPEECH, OUTPUT_BRAILLE, OUTPUT_NONE)

# Sounds. A sound value is empty for no sound, SOUND_BUILTIN_PREFIX plus a name, or a wav file path.
SOUND_NONE = ""
SOUND_BUILTIN_PREFIX = "builtin:"
BUILTIN_SOUNDS = ("chime", "blip", "alert")

# What the add-on did with a notification, recorded in history.
ACTION_PASSTHROUGH = "passthrough"
ACTION_DO_NOT_DISTURB = "doNotDisturb"
ACTION_DISABLED_BY_NVDA = "nvdaSettingOff"


def domainFromUrl(url: str) -> str:
	"""Return the lower case host name of a URL, or an empty string when there is none.

	Also accepts a bare host name such as example.com, so it can clean up what a user types into a rule.
	"""
	url = (url or "").strip()
	if not url:
		return ""
	if "://" not in url:
		_scheme, sep, rest = url.partition(":")
		if sep and not rest[:1].isdigit():
			# A URL without a host, such as about:blank or data:, rather than host:port.
			return ""
		url = "http://" + url
	try:
		host = urlsplit(url).hostname or ""
	except ValueError:
		return ""
	return host.lower().rstrip(".")


def siteDomain(domain: str) -> str:
	"""The domain a new rule should use for a notification's domain: the host without a leading www."""
	return domain.lower().removeprefix("www.")


@dataclass
class NotificationRecord:
	"""One notification received by NVDA."""

	timestamp: float
	"""Seconds since the epoch."""
	source: str
	text: str
	appName: str = ""
	"""The NVDA app module name of the process that raised it, for example ms-teams or chrome."""
	appDisplayName: str = ""
	"""A friendlier app name when one is known. For toasts, the app that sent the toast."""
	windowTitle: str = ""
	url: str = ""
	domain: str = ""
	notificationKind: int | None = None
	notificationProcessing: int | None = None
	activityId: str = ""
	politeness: str = ""
	background: bool = False
	"""True when the sending app was not the foreground app. NVDA drops background UIA notifications."""
	details: str = ""
	"""Extra technical information, such as the structure of a toast, to help when writing rules."""
	matchedRuleId: str = ""
	matchedRuleName: str = ""
	action: str = ACTION_PASSTHROUGH
	category: str = CATEGORY_UNCLASSIFIED
	reviewed: bool = False
	pinned: bool = False
	id: int = 0

	def toDict(self) -> dict[str, Any]:
		return dataclasses.asdict(self)

	@classmethod
	def fromDict(cls, data: dict[str, Any]) -> NotificationRecord:
		names = {f.name for f in dataclasses.fields(cls)}
		return cls(**{k: v for k, v in data.items() if k in names})

	@property
	def appLabel(self) -> str:
		"""The name to show for the app."""
		return self.appDisplayName or self.appName


@dataclass
class Action:
	"""What to do with a notification a rule matches."""

	output: str = OUTPUT_DEFAULT
	sound: str = SOUND_NONE

	@property
	def isPassthrough(self) -> bool:
		"""True when NVDA's own handling runs, possibly with a sound added."""
		return self.output == OUTPUT_DEFAULT

	def describe(self) -> str:
		"""A stable, untranslated key for the history log, such as speech+sound."""
		parts = [self.output]
		if self.sound:
			parts.append("sound")
		return "+".join(parts)


@dataclass
class Rule:
	"""A user rule. The first enabled rule that matches a notification decides what happens to it."""

	id: str = ""
	name: str = ""
	enabled: bool = True
	app: str = ""
	"""App module name or display name to match, case insensitive. Empty matches any app."""
	source: str = SOURCE_ANY
	domain: str = ""
	"""Website host name. Empty means the rule is not limited to a website."""
	includeSubdomains: bool = True
	urlPrefix: str = ""
	matchType: str = MATCH_STARTS_WITH
	pattern: str = ""
	caseSensitive: bool = False
	activityId: str = ""
	politeness: str = POLITENESS_ANY
	action: Action = field(default_factory=Action)
	category: str = CATEGORY_INFORMATIONAL
	log: bool = True

	def toDict(self) -> dict[str, Any]:
		return dataclasses.asdict(self)

	@classmethod
	def fromDict(cls, data: dict[str, Any]) -> Rule:
		names = {f.name for f in dataclasses.fields(cls)}
		values = {k: v for k, v in data.items() if k in names}
		action = values.get("action")
		if isinstance(action, dict):
			actionNames = {f.name for f in dataclasses.fields(Action)}
			values["action"] = Action(**{k: v for k, v in action.items() if k in actionNames})
		elif not isinstance(action, Action):
			values["action"] = Action()
		return cls(**values)

	@property
	def isSiteScoped(self) -> bool:
		return bool(self.domain or self.urlPrefix)
