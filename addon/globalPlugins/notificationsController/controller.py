# NotificationsController: controller.py
# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""The running state of the add-on: rules, history, and the path every notification takes."""

from __future__ import annotations

import os
from collections.abc import Callable

import config
import wx
from logHandler import log

from . import output, settings
from .history import History
from .models import NotificationRecord
from .policy import Deduper, decide
from .rules import RuleSet

FLUSH_INTERVAL_MS = 3000
PRUNE_INTERVAL_MS = 60 * 60 * 1000


class _Timer(wx.Timer):
	def __init__(self, callback: Callable[[], None]):
		super().__init__()
		self._callback = callback

	def Notify(self) -> None:
		try:
			self._callback()
		except Exception:
			log.error("NotificationsController: timer callback failed", exc_info=True)


class Controller:
	def __init__(self):
		self.rules = RuleSet()
		self.rulesLoadError: str | None = None
		self.history = History()
		self.deduper = Deduper()
		self._flushTimer: _Timer | None = None
		self._pruneTimer: _Timer | None = None
		self.onRulesChanged: list[Callable[[], None]] = []

	# Lifecycle

	def start(self) -> None:
		self.loadRules()
		self.applySettings()
		try:
			self.history.load()
			if self.history.loadErrors:
				log.warning(
					f"NotificationsController: skipped {self.history.loadErrors} damaged history lines"
				)
		except OSError:
			log.error("NotificationsController: could not read history", exc_info=True)
		self._flushTimer = _Timer(self.flush)
		self._flushTimer.Start(FLUSH_INTERVAL_MS)
		self._pruneTimer = _Timer(self.history.prune)
		self._pruneTimer.Start(PRUNE_INTERVAL_MS)

	def stop(self) -> None:
		for timer in (self._flushTimer, self._pruneTimer):
			if timer:
				timer.Stop()
		self._flushTimer = self._pruneTimer = None
		self.flush()

	def flush(self) -> None:
		if not self.history.needsFlush:
			return
		try:
			self.history.flush()
		except OSError:
			log.error("NotificationsController: could not save history", exc_info=True)

	def applySettings(self) -> None:
		conf = settings.conf()
		self.history.retentionSeconds = conf["retentionHours"] * 3600
		self.history.maxEntries = conf["maxEntries"]
		self.history.keepImportant = conf["keepImportant"]
		if conf["persistHistory"]:
			self.history.setPath(settings.historyPath())
		else:
			if self.history.path:
				try:
					self.history.deleteFile()
				except OSError:
					log.error("NotificationsController: could not delete history file", exc_info=True)
			self.history.setPath(None)
		self.deduper.windowSeconds = conf["dedupeMs"] / 1000
		self.history.prune()

	# Rules

	def loadRules(self) -> None:
		path = settings.rulesPath()
		try:
			self.rules = RuleSet.load(path)
			self.rulesLoadError = None
		except (OSError, ValueError) as e:
			# Keep the damaged file for the user to look at, and start with no rules.
			log.error(f"NotificationsController: could not read {path}", exc_info=True)
			self.rulesLoadError = str(e)
			self.rules = RuleSet()
			try:
				os.replace(path, path + ".damaged")
			except OSError:
				pass

	def saveRules(self) -> None:
		self.rules.recompile()
		try:
			self.rules.save(settings.rulesPath())
		except OSError:
			log.error("NotificationsController: could not save rules", exc_info=True)
			raise
		for callback in list(self.onRulesChanged):
			callback()

	# Notifications

	def process(self, record: NotificationRecord, passthrough: Callable[[], None]) -> None:
		"""Apply the rules to a notification, present it, and log it.

		:param passthrough: Lets NVDA handle the notification as usual. Called at most once.
		"""
		conf = settings.conf()
		if not conf["enabled"] or not record.text.strip():
			passthrough()
			return
		passed = False
		try:
			duplicate = self.deduper.isDuplicate(record)
			rule = self.rules.match(record)
			decision = decide(
				record,
				rule,
				doNotDisturb=conf["doNotDisturb"],
				liveRegionsOn=config.conf["presentation"]["reportDynamicContentChanges"],
			)
			record.action = decision.actionKey
			output.playSound(decision.sound)
			if decision.passthrough:
				passed = True
				passthrough()
			elif decision.present:
				output.present(record.text, decision.present, record.politeness)
			if decision.log and conf["logEnabled"] and not duplicate:
				self.history.add(record)
		except Exception:
			log.error("NotificationsController: error processing a notification", exc_info=True)
			if not passed:
				passthrough()
