# NotificationsController: storage.py
# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Settings about how history is stored. These are global, not part of NVDA's configuration profiles.

Whether history is written to disk, how long it is kept, and similar settings describe one shared
history file. If they changed with the active profile, switching profiles could write notification text
to disk while a memory only profile was active, or delete history on a profile switch. So they live in
their own file, settings.json in the add-on's data folder. Nothing here imports NVDA.
"""

from __future__ import annotations

import dataclasses
import json
import os
from dataclasses import dataclass
from typing import Any

from .rules import writeJsonAtomic

MIN_ENTRIES = 100
MAX_ENTRIES = 100000
MAX_DEDUPE_MS = 10000


@dataclass
class StorageSettings:
	persistHistory: bool = True
	retentionHours: int = 24
	"""0 keeps entries until the entry limit is reached."""
	maxEntries: int = 5000
	keepImportant: bool = False
	dedupeMs: int = 500

	@property
	def retentionSeconds(self) -> int:
		return self.retentionHours * 3600

	def toDict(self) -> dict[str, Any]:
		return dataclasses.asdict(self)

	@classmethod
	def fromDict(cls, data: Any) -> StorageSettings:
		"""Build settings from saved or migrated data, keeping defaults for anything missing or unusable.

		Accepts the text forms NVDA's configuration uses ("True", "24"), for migrating old settings.
		"""
		settings = cls()
		if not isinstance(data, dict):
			return settings
		for f in dataclasses.fields(cls):
			if f.name not in data:
				continue
			value = _coerce(data[f.name], type(f.default))
			if value is not None:
				setattr(settings, f.name, value)
		settings.retentionHours = max(0, settings.retentionHours)
		settings.maxEntries = min(MAX_ENTRIES, max(MIN_ENTRIES, settings.maxEntries))
		settings.dedupeMs = min(MAX_DEDUPE_MS, max(0, settings.dedupeMs))
		return settings

	@classmethod
	def failClosed(cls) -> StorageSettings:
		"""Settings to use when the saved ones cannot be read: nothing is written to disk.

		The saved file might have said not to keep notifications on disk, so the defaults, which do,
		would be the wrong guess.
		"""
		return cls(persistHistory=False)

	@classmethod
	def load(cls, path: str) -> StorageSettings | None:
		"""Read settings, or None when there is no file yet.

		:raises ValueError: The file exists but is damaged.
		:raises OSError: The file exists but cannot be read.
		"""
		if not os.path.exists(path):
			return None
		with open(path, encoding="utf-8") as f:
			data = json.load(f)
		if not isinstance(data, dict):
			raise ValueError("Storage settings are not a JSON object")
		settings = cls.fromDict(data)
		if _coerce(data.get("persistHistory"), bool) is None:
			# Missing or unreadable: do not guess that notifications may be kept on disk.
			settings.persistHistory = False
		return settings

	def save(self, path: str) -> None:
		writeJsonAtomic(path, self.toDict())


def _coerce(value: Any, expected: type) -> Any:
	"""Value as the expected type, or None when it cannot be one."""
	if expected is bool:
		if isinstance(value, bool):
			return value
		if isinstance(value, str) and value.strip().lower() in ("true", "false"):
			return value.strip().lower() == "true"
		return None
	if expected is int:
		if isinstance(value, bool):
			return None
		if isinstance(value, int):
			return value
		if isinstance(value, str):
			try:
				return int(value.strip())
			except ValueError:
				return None
		return None
	return value if isinstance(value, expected) else None
