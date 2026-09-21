# NotificationsController: controller.py
# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""The running state of the add-on: rules, history, and the path every notification takes."""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from enum import Enum

import config
import wx
from logHandler import log

from . import output, settings
from .history import History
from .models import NotificationRecord, Rule
from .policy import Deduper, decide
from .rules import RuleSet
from .storage import StorageSettings

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


class StorageProblem(Enum):
	"""Something that went wrong storing history, which the settings panel explains."""

	SETTINGS_UNREADABLE = "settingsUnreadable"
	"""settings.json could not be read, so history is kept in memory only."""
	SETTINGS_NOT_SAVED = "settingsNotSaved"
	"""The storage settings apply now but could not be saved, so they may change back after a restart."""
	HISTORY_UNREADABLE = "historyUnreadable"
	"""Saving is on, but the history file could not be read, so history is kept in memory only."""
	HISTORY_NOT_DELETED = "historyNotDeleted"
	"""Saving was turned off, but the history file could not be deleted."""


class Controller:
	def __init__(self):
		self.rules = RuleSet()
		self.rulesLoadError: str | None = None
		self.storage = StorageSettings()
		self.storageProblems: set[StorageProblem] = set()
		"""What went wrong storing history, for the settings panel to explain."""
		self.history = History()
		self.deduper = Deduper()
		self._flushTimer: _Timer | None = None
		self._pruneTimer: _Timer | None = None
		self.onRulesChanged: list[Callable[[], None]] = []

	# Lifecycle

	def start(self) -> None:
		self.load()
		self._flushTimer = _Timer(self.flush)
		self._flushTimer.Start(FLUSH_INTERVAL_MS)
		self._pruneTimer = _Timer(self.history.prune)
		self._pruneTimer.Start(PRUNE_INTERVAL_MS)

	def load(self) -> None:
		"""Read rules, storage settings and history."""
		self.loadRules()
		self.storage = self._loadStorage()
		self.history.path = settings.historyPath() if self.storage.persistHistory else None
		self._applyStorageLimits()
		try:
			self.history.load()
			if self.history.loadErrors:
				log.warning(
					f"NotificationsController: skipped {self.history.loadErrors} damaged history lines"
				)
		except OSError:
			# Keep history in memory only: writing to a file that could not be read could append
			# clashing entries to it, or replace it with only this session's.
			log.error("NotificationsController: could not read history; not saving it", exc_info=True)
			self.history.path = None
			self.storageProblems.add(StorageProblem.HISTORY_UNREADABLE)

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

	# Storage settings

	def _loadStorage(self) -> StorageSettings:
		path = settings.storagePath()
		try:
			loaded = StorageSettings.load(path)
		except (OSError, ValueError) as e:
			# Fail closed: keep history in memory only until the user saves settings again.
			# The file is left in place, so this happens on every start until then; a copy is kept for
			# diagnosis if it is damaged.
			log.error(
				f"NotificationsController: could not read {path}; history is not saved to disk "
				"until the add-on's settings are saved again",
				exc_info=True,
			)
			self.storageProblems.add(StorageProblem.SETTINGS_UNREADABLE)
			if isinstance(e, ValueError):
				try:
					shutil.copyfile(path, path + ".damaged")
				except OSError:
					pass
			return StorageSettings.failClosed()
		if loaded is not None:
			return loaded
		# First run of this version: carry over anything an earlier version kept in NVDA's configuration.
		storage = StorageSettings.fromDict(settings.legacyStorageValues())
		try:
			storage.save(settings.storagePath())
		except OSError:
			log.error("NotificationsController: could not save storage settings", exc_info=True)
		return storage

	def _applyStorageLimits(self) -> None:
		self.history.retentionSeconds = self.storage.retentionSeconds
		self.history.maxEntries = self.storage.maxEntries
		self.history.keepImportant = self.storage.keepImportant
		self.deduper.windowSeconds = self.storage.dedupeMs / 1000
		self.history.prune()

	def setStorage(self, storage: StorageSettings) -> set[StorageProblem]:
		"""Apply storage settings the user changed, and save them.

		Turning off saving is the one place the history file is deleted, because that is an explicit
		request to stop keeping notifications on disk. Turning saving on merges any history already in
		the file with what is in memory.

		The settings apply to this session even when they cannot be saved, so turning off saving always
		stops writing straight away.
		:return: What went wrong; empty when everything was saved as asked. Also kept in storageProblems.
		"""
		old = self.storage
		self.storage = storage
		problems: set[StorageProblem] = set()
		try:
			storage.save(settings.storagePath())
		except OSError:
			log.error("NotificationsController: could not save storage settings", exc_info=True)
			problems.add(StorageProblem.SETTINGS_NOT_SAVED)
		if not storage.persistHistory:
			self.history.setPath(None)
			# Delete when saving is being turned off, and try again if an earlier delete failed.
			if old.persistHistory or StorageProblem.HISTORY_NOT_DELETED in self.storageProblems:
				try:
					History.deleteFile(settings.historyPath())
				except OSError:
					log.error("NotificationsController: could not delete history file", exc_info=True)
					problems.add(StorageProblem.HISTORY_NOT_DELETED)
		elif self.history.path is None:
			# Turning saving on, or trying again after the file could not be read.
			try:
				self.history.setPath(settings.historyPath())
			except OSError:
				# setPath changed nothing, so history is still memory only.
				log.error("NotificationsController: could not read history file", exc_info=True)
				problems.add(StorageProblem.HISTORY_UNREADABLE)
		self._applyStorageLimits()
		self.storageProblems = problems
		return problems

	# Rules

	def _useRules(self, rules: RuleSet) -> None:
		rules.onRuleError = self._onRuleError
		self.rules = rules

	def _onRuleError(self, rule: Rule, error: str) -> None:
		log.warning(
			f"NotificationsController: rule {rule.name!r} is turned off until the rules change: "
			f"its regular expression {error}",
		)
		# Let an open rules window show the rule's error. Matching runs inside event handling, so the
		# window is refreshed afterwards rather than from here.
		wx.CallAfter(self._notifyRulesChanged)

	def _notifyRulesChanged(self) -> None:
		for callback in list(self.onRulesChanged):
			callback()

	def loadRules(self) -> None:
		path = settings.rulesPath()
		try:
			self._useRules(RuleSet.load(path))
			self.rulesLoadError = None
		except (OSError, ValueError) as e:
			# Keep the damaged file for the user to look at, and start with no rules.
			log.error(f"NotificationsController: could not read {path}", exc_info=True)
			self.rulesLoadError = str(e)
			self._useRules(RuleSet())
			try:
				os.replace(path, path + ".damaged")
			except OSError:
				pass

	def commitRules(self, rules: list[Rule]) -> bool:
		"""Save a new list of rules, and only once it is saved, start using it.

		Callers build the new list from copies, so when saving fails, the rules in use, the file and the
		rules window all stay as they were.
		:return: Whether the rules were saved.
		"""
		candidate = RuleSet(rules)
		try:
			candidate.save(settings.rulesPath())
		except OSError:
			log.error("NotificationsController: could not save rules", exc_info=True)
			return False
		self._useRules(candidate)
		self._notifyRulesChanged()
		return True

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
			remainder = None
			if rule and rule.action.removeMatch:
				remainder = self.rules.removeMatched(rule, record.text)
			decision = decide(
				record,
				rule,
				doNotDisturb=conf["doNotDisturb"],
				liveRegionsOn=config.conf["presentation"]["reportDynamicContentChanges"],
				remainder=remainder,
			)
			record.action = decision.actionKey
			output.playSound(decision.sound)
			if decision.passthrough:
				passed = True
				passthrough()
			elif decision.present:
				output.present(
					decision.text or record.text,
					decision.present,
					record.politeness,
					record.notificationProcessing,
				)
			if decision.log and conf["logEnabled"] and not duplicate:
				self.history.add(record)
		except Exception:
			log.error("NotificationsController: error processing a notification", exc_info=True)
			if not passed:
				passthrough()
