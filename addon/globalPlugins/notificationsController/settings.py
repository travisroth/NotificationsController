# NotificationsController: settings.py
# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Configuration: the confspec, where files live, and the translated labels shared by the GUI."""

from __future__ import annotations

import os

import addonHandler
import config
import globalVars

from .models import (
	ACTION_DISABLED_BY_NVDA,
	ACTION_DO_NOT_DISTURB,
	ACTION_PASSTHROUGH,
	BUILTIN_SOUNDS,
	CATEGORY_IMPORTANT,
	CATEGORY_INFORMATIONAL,
	CATEGORY_SPAM,
	CATEGORY_UNCLASSIFIED,
	MATCH_ANY,
	MATCH_CONTAINS,
	MATCH_EQUALS,
	MATCH_REGEX,
	MATCH_STARTS_WITH,
	OUTPUT_BRAILLE,
	OUTPUT_DEFAULT,
	OUTPUT_NONE,
	OUTPUT_SPEECH,
	OUTPUT_SPEECH_BRAILLE,
	POLITENESS_ANY,
	POLITENESS_ASSERTIVE,
	POLITENESS_POLITE,
	SOUND_BUILTIN_PREFIX,
	SOURCE_ALERT,
	SOURCE_ANY,
	SOURCE_LIVE_REGION,
	SOURCE_TOAST,
	SOURCE_UIA,
)

addonHandler.initTranslation()

SECTION = "notificationsController"

confspec = {
	"enabled": "boolean(default=True)",
	"logEnabled": "boolean(default=True)",
	"persistHistory": "boolean(default=True)",
	"retentionHours": "integer(default=24, min=0)",
	"maxEntries": "integer(default=5000, min=100, max=100000)",
	"keepImportant": "boolean(default=False)",
	"dedupeMs": "integer(default=500, min=0, max=10000)",
	"doNotDisturb": "boolean(default=False)",
	"captureUIA": "boolean(default=True)",
	"captureToasts": "boolean(default=True)",
	"captureLiveRegions": "boolean(default=True)",
	"captureAlerts": "boolean(default=True)",
}

RETENTION_CHOICES = (1, 12, 24, 72, 168, 720, 0)
"""Retention periods offered, in hours. 0 keeps entries until the entry cap is reached."""


def registerConfig() -> None:
	config.conf.spec[SECTION] = confspec


def conf() -> config.AggregatedSection:
	return config.conf[SECTION]


def dataDir() -> str:
	return os.path.join(globalVars.appArgs.configPath, "notificationsController")


def rulesPath() -> str:
	return os.path.join(dataDir(), "rules.json")


def historyPath() -> str:
	return os.path.join(dataDir(), "history.jsonl")


def addonDir() -> str:
	return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def builtinSoundPath(name: str) -> str:
	return os.path.join(addonDir(), "sounds", name + ".wav")


def soundPath(sound: str) -> str:
	"""The wav file for a rule's sound value, or an empty string for no sound."""
	if not sound:
		return ""
	if sound.startswith(SOUND_BUILTIN_PREFIX):
		return builtinSoundPath(sound[len(SOUND_BUILTIN_PREFIX) :])
	return sound


def retentionLabel(hours: int) -> str:
	if hours == 0:
		# Translators: A history retention choice: entries are only removed when there are too many.
		return _("Keep until the entry limit is reached")
	if hours < 24:
		# Translators: A history retention choice, in hours.
		return ngettext("{n} hour", "{n} hours", hours).format(n=hours)
	days = hours // 24
	# Translators: A history retention choice, in days.
	return ngettext("{n} day", "{n} days", days).format(n=days)


def sourceLabels() -> dict[str, str]:
	return {
		# Translators: Where a notification came from: any source.
		SOURCE_ANY: _("Any source"),
		# Translators: Where a notification came from: a UI Automation notification event raised by an app.
		SOURCE_UIA: _("App notification (UIA)"),
		# Translators: Where a notification came from: a Windows toast popup.
		SOURCE_TOAST: _("Windows toast"),
		# Translators: Where a notification came from: an ARIA live region on a web page.
		SOURCE_LIVE_REGION: _("Web live region"),
		# Translators: Where a notification came from: an object with the alert role, such as an app's own
		# pop-up or a web page alert.
		SOURCE_ALERT: _("Pop-up alert"),
	}


def matchTypeLabels() -> dict[str, str]:
	return {
		# Translators: How a rule matches notification text.
		MATCH_STARTS_WITH: _("Starts with"),
		# Translators: How a rule matches notification text.
		MATCH_CONTAINS: _("Contains"),
		# Translators: How a rule matches notification text.
		MATCH_EQUALS: _("Is exactly"),
		# Translators: How a rule matches notification text.
		MATCH_REGEX: _("Regular expression"),
		# Translators: How a rule matches notification text: the text is not checked.
		MATCH_ANY: _("Any text"),
	}


def outputLabels() -> dict[str, str]:
	return {
		# Translators: What a rule does with a notification: let NVDA handle it as usual.
		OUTPUT_DEFAULT: _("NVDA default"),
		# Translators: What a rule does with a notification.
		OUTPUT_SPEECH_BRAILLE: _("Speech and braille"),
		# Translators: What a rule does with a notification.
		OUTPUT_SPEECH: _("Speech only"),
		# Translators: What a rule does with a notification.
		OUTPUT_BRAILLE: _("Braille only"),
		# Translators: What a rule does with a notification: no speech and no braille.
		OUTPUT_NONE: _("No speech or braille"),
	}


def builtinSoundLabels() -> dict[str, str]:
	labels = {
		# Translators: The name of a built-in sound.
		"chime": _("Chime"),
		# Translators: The name of a built-in sound.
		"blip": _("Blip"),
		# Translators: The name of a built-in sound.
		"alert": _("Alert"),
	}
	return {SOUND_BUILTIN_PREFIX + name: labels[name] for name in BUILTIN_SOUNDS}


def politenessLabels() -> dict[str, str]:
	return {
		# Translators: Live region politeness filter: any.
		POLITENESS_ANY: _("Any"),
		# Translators: Live region politeness.
		POLITENESS_POLITE: _("Polite"),
		# Translators: Live region politeness.
		POLITENESS_ASSERTIVE: _("Assertive"),
	}


def categoryLabel(category: str) -> str:
	labels = {
		# Translators: A notification category.
		CATEGORY_IMPORTANT: _("Important"),
		# Translators: A notification category.
		CATEGORY_INFORMATIONAL: _("Informational"),
		# Translators: A notification category.
		CATEGORY_SPAM: _("Spam"),
		# Translators: The category of a notification no rule matched.
		CATEGORY_UNCLASSIFIED: _("Unclassified"),
	}
	return labels.get(category, category)


def actionLabel(action: str) -> str:
	"""Describe what was done with a notification, from the key stored in its history record."""
	special = {
		# Translators: What was done with a notification: NVDA handled it as usual.
		ACTION_PASSTHROUGH: _("NVDA default"),
		# Translators: What was done with a notification: silenced because Do not disturb was on.
		ACTION_DO_NOT_DISTURB: _("Silenced by Do not disturb"),
		# Translators: What was done with a notification: silenced because an NVDA setting turns it off,
		# such as report dynamic content changes.
		ACTION_DISABLED_BY_NVDA: _("Off in NVDA settings"),
	}
	if action in special:
		return special[action]
	output, _sep, sound = action.partition("+")
	label = outputLabels().get(output, output)
	if sound:
		# Translators: What was done with a notification: an output such as speech only, plus a sound.
		label = _("{output}, with sound").format(output=label)
	return label
