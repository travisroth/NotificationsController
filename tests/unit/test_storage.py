# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for the global storage settings file."""

import os
import tempfile
import unittest

from notificationsController.storage import MAX_ENTRIES, MIN_ENTRIES, StorageSettings


class TestStorageSettings(unittest.TestCase):
	def test_roundTrip(self):
		with tempfile.TemporaryDirectory() as d:
			path = os.path.join(d, "nc", "settings.json")
			self.assertIsNone(StorageSettings.load(path))
			original = StorageSettings(
				persistHistory=False, retentionHours=72, maxEntries=200, keepImportant=True
			)
			original.save(path)
			self.assertEqual(StorageSettings.load(path), original)

	def test_damagedFileGivesDefaults(self):
		with tempfile.TemporaryDirectory() as d:
			path = os.path.join(d, "settings.json")
			with open(path, "w", encoding="utf-8") as f:
				f.write("{nope")
			self.assertEqual(StorageSettings.load(path), StorageSettings())

	def test_migratesNvdaConfigText(self):
		settings = StorageSettings.fromDict(
			{"persistHistory": "False", "retentionHours": "168", "dedupeMs": " 0 "}
		)
		self.assertFalse(settings.persistHistory)
		self.assertEqual(settings.retentionHours, 168)
		self.assertEqual(settings.dedupeMs, 0)

	def test_badValuesKeepDefaultsAndLimitsApply(self):
		settings = StorageSettings.fromDict(
			{
				"persistHistory": 1,
				"retentionHours": -5,
				"maxEntries": 10**9,
				"keepImportant": None,
				"dedupeMs": "x",
			},
		)
		self.assertTrue(settings.persistHistory)
		self.assertEqual(settings.retentionHours, 0)
		self.assertEqual(settings.maxEntries, MAX_ENTRIES)
		self.assertFalse(settings.keepImportant)
		self.assertEqual(settings.dedupeMs, 500)
		self.assertEqual(StorageSettings.fromDict({"maxEntries": 1}).maxEntries, MIN_ENTRIES)
		self.assertEqual(StorageSettings.fromDict(None), StorageSettings())


if __name__ == "__main__":
	unittest.main()
