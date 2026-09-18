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


def present(text: str, action: Action, politeness: str = "") -> None:
	"""Speak and braille the text as the action asks. The action's sound is played separately.

	The default output (NVDA's own handling) is not presented here; the caller lets NVDA run.
	"""
	if not text:
		return
	priority = Spri.NEXT if politeness == POLITENESS_ASSERTIVE else None
	if action.output == OUTPUT_SPEECH_BRAILLE:
		ui.message(text, speechPriority=priority)
	elif action.output == OUTPUT_SPEECH:
		speech.speakMessage(text, priority=priority)
	elif action.output == OUTPUT_BRAILLE and braille.handler:
		braille.handler.message(text)


def reportRecordText(text: str) -> None:
	"""Report text from history on request, in speech and braille."""
	ui.message(text)
