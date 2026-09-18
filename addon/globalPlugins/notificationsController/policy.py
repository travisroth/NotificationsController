# NotificationsController: policy.py
# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Deciding what happens to a notification once the rules have been checked. Nothing here imports NVDA."""

from __future__ import annotations

from dataclasses import dataclass

from .models import (
	ACTION_DISABLED_BY_NVDA,
	ACTION_DO_NOT_DISTURB,
	ACTION_PASSTHROUGH,
	CATEGORY_IMPORTANT,
	CATEGORY_UNCLASSIFIED,
	SOURCE_LIVE_REGION,
	Action,
	NotificationRecord,
	Rule,
)
from .rules import normalizeText


@dataclass
class Decision:
	passthrough: bool
	"""Let NVDA handle the notification as it normally would."""
	present: Action | None
	"""Present the notification with this action's speech and braille output. None for no output."""
	sound: str
	"""A sound to play, or empty."""
	log: bool
	actionKey: str
	"""What was done, as stored in history."""


def decide(
	record: NotificationRecord,
	rule: Rule | None,
	doNotDisturb: bool = False,
	liveRegionsOn: bool = True,
) -> Decision:
	"""Decide what to do with a notification, and fill in the record's rule and category.

	:param rule: The first matching rule, or None.
	:param doNotDisturb: Silence everything except important notifications.
	:param liveRegionsOn: NVDA's report dynamic content changes setting. When it is off, NVDA reports
		no live regions, and rules do not bring them back, so the quick toggle (NVDA+5) still silences them.
	"""
	if rule:
		record.matchedRuleId = rule.id
		record.matchedRuleName = rule.name
		record.category = rule.category
		action = rule.action
		log = rule.log
	else:
		record.category = CATEGORY_UNCLASSIFIED
		action = Action()
		log = True
	if doNotDisturb and record.category != CATEGORY_IMPORTANT:
		return Decision(False, None, "", log, ACTION_DO_NOT_DISTURB)
	if record.source == SOURCE_LIVE_REGION and not liveRegionsOn:
		# NVDA would not report it either; passing it through is harmless and keeps NVDA in charge.
		return Decision(True, None, "", log, ACTION_DISABLED_BY_NVDA)
	if action.isPassthrough:
		key = action.describe() if action.sound else ACTION_PASSTHROUGH
		return Decision(True, None, action.sound, log, key)
	return Decision(False, action, action.sound, log, action.describe())


class Deduper:
	"""Spots the same notification arriving again within a short time.

	Chromium and Teams sometimes raise the same notification twice, and a live region can be reported
	both through an event and through NVDA's in-process helper.
	"""

	def __init__(self, windowSeconds: float = 0.5):
		self.windowSeconds = windowSeconds
		self._lastKey: tuple[str, str] | None = None
		self._lastTime = 0.0

	def isDuplicate(self, record: NotificationRecord) -> bool:
		# Compare text as the rules do, so "Running" and "Running " count as the same.
		key = (record.appName, normalizeText(record.text))
		duplicate = (
			self.windowSeconds > 0
			and key == self._lastKey
			and 0 <= record.timestamp - self._lastTime < self.windowSeconds
		)
		self._lastKey = key
		self._lastTime = record.timestamp
		return duplicate
