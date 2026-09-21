# NotificationsController: output.py
# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Presenting a notification the way a rule asks: speech, braille, a sound, or any mix."""

from __future__ import annotations

import os

import braille
import nvwave
import speech
import ui
from logHandler import log
from speech.priorities import Spri

from .models import (
	OUTPUT_BRAILLE,
	OUTPUT_SPEECH,
	OUTPUT_SPEECH_BRAILLE,
	POLITENESS_ASSERTIVE,
	Action,
)
from .settings import soundPath


def playSound(sound: str) -> None:
	path = soundPath(sound)
	if not path:
		return
	if not os.path.isfile(path):
		log.debugWarning(f"NotificationsController: sound file not found: {path}")
		return
	try:
		nvwave.playWaveFile(path, asynchronous=True)
	except Exception:
		log.error(f"NotificationsController: could not play {path}", exc_info=True)


def _supersedesEarlier(notificationProcessing: int | None) -> bool:
	"""Whether a UIA notification replaces earlier ones, as progress and status notifications do."""
	import UIAHandler

	return notificationProcessing in (
		UIAHandler.NotificationProcessing_ImportantMostRecent,
		UIAHandler.NotificationProcessing_MostRecent,
	)


def present(
	text: str,
	action: Action,
	politeness: str = "",
	notificationProcessing: int | None = None,
) -> None:
	"""Speak and braille the text as the action asks. The action's sound is played separately.

	The default output (NVDA's own handling) is not presented here; the caller lets NVDA run.
	:param notificationProcessing: For UIA notifications, how the app asked for it to be processed.
		As NVDA does, a notification that supersedes earlier ones cancels speech that has not finished,
		or during say all is spoken straight away, so a stream of status updates does not pile up.
	"""
	if not text:
		return
	priority = Spri.NEXT if politeness == POLITENESS_ASSERTIVE else None
	if action.output in (OUTPUT_SPEECH, OUTPUT_SPEECH_BRAILLE) and _supersedesEarlier(notificationProcessing):
		if speech.sayAll.SayAllHandler.isRunning():
			priority = Spri.NOW
		else:
			speech.cancelSpeech()
	if action.output == OUTPUT_SPEECH_BRAILLE:
		ui.message(text, speechPriority=priority)
	elif action.output == OUTPUT_SPEECH:
		speech.speakMessage(text, priority=priority)
	elif action.output == OUTPUT_BRAILLE and braille.handler:
		braille.handler.message(text)


def reportRecordText(text: str) -> None:
	"""Report text from history on request, in speech and braille."""
	ui.message(text)
