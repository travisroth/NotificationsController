# NotificationsController: history.py
# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""The notification history: an in memory list backed by a JSON Lines file.

New records are appended to the file in batches by flush(). Any other change (deleting, pinning,
pruning) marks the history dirty, and the next flush() rewrites the whole file.
All methods are meant to be called from one thread, NVDA's main thread. Nothing here imports NVDA.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Iterable

from .models import CATEGORY_IMPORTANT, NotificationRecord


class History:
	def __init__(
		self,
		path: str | None = None,
		retentionSeconds: float = 24 * 3600,
		maxEntries: int = 5000,
		keepImportant: bool = False,
		clock: Callable[[], float] = time.time,
	):
		"""
		:param path: The JSON Lines file, or None to keep history in memory only.
		:param retentionSeconds: How long entries are kept. 0 keeps them until the entry cap is reached.
		:param maxEntries: The most entries to keep. Pinned entries count toward it but are never removed.
		:param keepImportant: When True, entries in the important category are never pruned.
		"""
		self.path = path
		self.retentionSeconds = retentionSeconds
		self.maxEntries = maxEntries
		self.keepImportant = keepImportant
		self._clock = clock
		self._records: list[NotificationRecord] = []
		self._pending: list[NotificationRecord] = []
		self._dirty = False
		self._nextId = 1
		self.loadErrors = 0
		"""How many lines of the file could not be read on the last load."""
		self.onChange: list[Callable[[], None]] = []
		"""Called after records are added, changed or removed."""

	# Reading

	@property
	def records(self) -> list[NotificationRecord]:
		"""All records, oldest first. Do not modify the list."""
		return self._records

	def __len__(self) -> int:
		return len(self._records)

	def get(self, recordId: int) -> NotificationRecord | None:
		for record in reversed(self._records):
			if record.id == recordId:
				return record
		return None

	def latest(self) -> NotificationRecord | None:
		return self._records[-1] if self._records else None

	def filter(
		self,
		app: str = "",
		domain: str = "",
		category: str = "",
		source: str = "",
		text: str = "",
		unreviewedOnly: bool = False,
	) -> list[NotificationRecord]:
		"""Records matching every given filter, newest first. Empty filters match everything."""
		app = app.casefold()
		text = text.casefold()
		result: list[NotificationRecord] = []
		for record in reversed(self._records):
			if app and app not in (record.appName.casefold(), record.appLabel.casefold()):
				continue
			if domain and record.domain != domain:
				continue
			if category and record.category != category:
				continue
			if source and record.source != source:
				continue
			if unreviewedOnly and record.reviewed:
				continue
			if text and text not in record.text.casefold():
				continue
			result.append(record)
		return result

	def apps(self) -> list[str]:
		"""Distinct app labels seen, sorted."""
		return sorted({r.appLabel for r in self._records if r.appLabel}, key=str.casefold)

	def appNames(self) -> list[str]:
		"""Distinct app module names and display names seen, sorted, for filling in rules."""
		names = {r.appName for r in self._records if r.appName}
		names.update(r.appDisplayName for r in self._records if r.appDisplayName)
		return sorted(names, key=str.casefold)

	def domains(self) -> list[str]:
		return sorted({r.domain for r in self._records if r.domain})

	def categories(self) -> list[str]:
		return sorted({r.category for r in self._records if r.category})

	@property
	def pruneSlack(self) -> int:
		"""How far past the entry cap history may grow before add() prunes.

		Pruning rewrites the history file, so it is done in batches rather than for every new entry.
		"""
		return max(10, self.maxEntries // 20)

	# Changing

	def add(self, record: NotificationRecord) -> NotificationRecord:
		record.id = self._nextId
		self._nextId += 1
		self._records.append(record)
		self._pending.append(record)
		if len(self._records) > self.maxEntries + self.pruneSlack:
			self.prune()
		self._changed()
		return record

	def delete(self, recordIds: Iterable[int]) -> int:
		ids = set(recordIds)
		before = len(self._records)
		self._records = [r for r in self._records if r.id not in ids]
		removed = before - len(self._records)
		if removed:
			self._pending = [r for r in self._pending if r.id not in ids]
			self._dirty = True
			self._changed()
		return removed

	def clear(self) -> None:
		self._records = []
		self._pending = []
		self._dirty = True
		self._changed()

	def update(self, records: Iterable[NotificationRecord]) -> None:
		"""Note that records were changed in place, for example pinned or marked reviewed."""
		if any(True for _ in records):
			self._dirty = True
			self._changed()

	def setPinned(self, records: Iterable[NotificationRecord], pinned: bool) -> None:
		records = list(records)
		for record in records:
			record.pinned = pinned
		self.update(records)

	def setReviewed(self, records: Iterable[NotificationRecord], reviewed: bool = True) -> None:
		records = list(records)
		for record in records:
			record.reviewed = reviewed
		self.update(records)

	def _isProtected(self, record: NotificationRecord) -> bool:
		return record.pinned or (self.keepImportant and record.category == CATEGORY_IMPORTANT)

	def prune(self, now: float | None = None) -> int:
		"""Remove entries older than the retention period, then the oldest entries over the cap.

		Pinned entries, and important ones when keepImportant is set, are never removed.
		:return: The number of entries removed.
		"""
		if now is None:
			now = self._clock()
		records = self._records
		if self.retentionSeconds > 0:
			cutoff = now - self.retentionSeconds
			records = [r for r in records if r.timestamp >= cutoff or self._isProtected(r)]
		excess = len(records) - self.maxEntries
		if excess > 0:
			kept: list[NotificationRecord] = []
			for record in records:
				if excess > 0 and not self._isProtected(record):
					excess -= 1
					continue
				kept.append(record)
			records = kept
		removed = len(self._records) - len(records)
		if removed:
			keptIds = {r.id for r in records}
			self._records = records
			self._pending = [r for r in self._pending if r.id in keptIds]
			self._dirty = True
			self._changed()
		return removed

	def _changed(self) -> None:
		for callback in list(self.onChange):
			callback()

	# Persistence

	def setPath(self, path: str | None) -> None:
		"""Change where history is saved. The next flush writes every record to the new file."""
		if path != self.path:
			self.path = path
			self._dirty = bool(path)

	@property
	def needsFlush(self) -> bool:
		return bool(self.path) and (self._dirty or bool(self._pending))

	def load(self) -> None:
		"""Read the history file, skipping any damaged lines. A missing file gives an empty history."""
		self._records = []
		self._pending = []
		self._dirty = False
		self.loadErrors = 0
		if self.path and os.path.isfile(self.path):
			with open(self.path, encoding="utf-8", errors="replace") as f:
				for line in f:
					line = line.strip()
					if not line:
						continue
					try:
						data = json.loads(line)
						self._records.append(NotificationRecord.fromDict(data))
					except ValueError:
						self.loadErrors += 1
		self._records.sort(key=lambda r: (r.timestamp, r.id))
		self._nextId = max((r.id for r in self._records), default=0) + 1
		seen: set[int] = set()
		for record in self._records:
			if record.id <= 0 or record.id in seen:
				# A missing or repeated id, from a damaged or hand edited file.
				record.id = self._nextId
				self._nextId += 1
				self._dirty = True
			seen.add(record.id)
		if self.loadErrors:
			# Rewrite the file without the damaged lines.
			self._dirty = True
		self.prune()

	def flush(self) -> None:
		"""Write pending changes to disk. Does nothing when history is memory only."""
		if not self.path:
			self._pending = []
			self._dirty = False
			return
		os.makedirs(os.path.dirname(self.path), exist_ok=True)
		if self._dirty:
			tmp = self.path + ".tmp"
			with open(tmp, "w", encoding="utf-8") as f:
				f.writelines(_toLine(record) for record in self._records)
			os.replace(tmp, self.path)
		elif self._pending:
			with open(self.path, "a", encoding="utf-8") as f:
				f.writelines(_toLine(record) for record in self._pending)
		self._pending = []
		self._dirty = False

	def deleteFile(self) -> None:
		"""Remove the history file, for when the user switches to memory only history."""
		if self.path and os.path.isfile(self.path):
			os.remove(self.path)


def _toLine(record: NotificationRecord) -> str:
	return json.dumps(record.toDict(), ensure_ascii=False) + "\n"
