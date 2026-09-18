# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for the history store: adding, filtering, pruning and the JSON Lines file."""

import os
import tempfile
import unittest

from notificationsController.history import History
from notificationsController.models import (
	CATEGORY_IMPORTANT,
	CATEGORY_SPAM,
	SOURCE_LIVE_REGION,
	SOURCE_UIA,
	NotificationRecord,
)

HOUR = 3600.0


class Clock:
	def __init__(self, now=1_000_000.0):
		self.now = now

	def __call__(self):
		return self.now


def rec(text="hi", timestamp=1_000_000.0, **kwargs):
	kwargs.setdefault("source", SOURCE_UIA)
	return NotificationRecord(timestamp=timestamp, text=text, **kwargs)


class TestHistory(unittest.TestCase):
	def setUp(self):
		self.clock = Clock()
		self.history = History(clock=self.clock)

	def test_addAssignsIncreasingIds(self):
		a = self.history.add(rec("a"))
		b = self.history.add(rec("b"))
		self.assertEqual((a.id, b.id), (1, 2))
		self.assertIs(self.history.latest(), b)
		self.assertIs(self.history.get(1), a)

	def test_onChangeCalled(self):
		calls = []
		self.history.onChange.append(lambda: calls.append(1))
		self.history.add(rec())
		self.history.clear()
		self.assertEqual(len(calls), 2)

	def test_filterNewestFirst(self):
		self.history.add(rec("one", appName="chrome", domain="a.com"))
		self.history.add(rec("two", appName="ms-teams", appDisplayName="Microsoft Teams"))
		self.history.add(rec("three", appName="chrome", category=CATEGORY_SPAM, source=SOURCE_LIVE_REGION))
		self.assertEqual([r.text for r in self.history.filter()], ["three", "two", "one"])
		self.assertEqual([r.text for r in self.history.filter(app="chrome")], ["three", "one"])
		self.assertEqual([r.text for r in self.history.filter(app="microsoft teams")], ["two"])
		self.assertEqual([r.text for r in self.history.filter(domain="a.com")], ["one"])
		self.assertEqual([r.text for r in self.history.filter(category=CATEGORY_SPAM)], ["three"])
		self.assertEqual([r.text for r in self.history.filter(source=SOURCE_LIVE_REGION)], ["three"])
		self.assertEqual([r.text for r in self.history.filter(text="TW")], ["two"])
		self.history.setReviewed([self.history.get(1)])
		self.assertEqual([r.text for r in self.history.filter(unreviewedOnly=True)], ["three", "two"])

	def test_distinctValues(self):
		self.history.add(rec(appName="chrome", domain="b.com"))
		self.history.add(rec(appName="ms-teams", appDisplayName="Microsoft Teams", domain="a.com"))
		self.assertEqual(self.history.apps(), ["chrome", "Microsoft Teams"])
		self.assertEqual(self.history.appNames(), ["chrome", "Microsoft Teams", "ms-teams"])
		self.assertEqual(self.history.domains(), ["a.com", "b.com"])

	def test_delete(self):
		self.history.add(rec("a"))
		self.history.add(rec("b"))
		self.assertEqual(self.history.delete([1, 99]), 1)
		self.assertEqual([r.text for r in self.history.records], ["b"])


class TestPruning(unittest.TestCase):
	def setUp(self):
		self.clock = Clock()
		self.history = History(retentionSeconds=24 * HOUR, maxEntries=100, clock=self.clock)
		now = self.clock.now
		self.old = self.history.add(rec("old", timestamp=now - 25 * HOUR))
		self.oldPinned = self.history.add(rec("old pinned", timestamp=now - 48 * HOUR, pinned=True))
		self.oldImportant = self.history.add(
			rec("old important", timestamp=now - 30 * HOUR, category=CATEGORY_IMPORTANT),
		)
		self.new = self.history.add(rec("new", timestamp=now - HOUR))

	def texts(self):
		return [r.text for r in self.history.records]

	def test_retention(self):
		self.assertEqual(self.history.prune(), 2)
		self.assertEqual(self.texts(), ["old pinned", "new"])

	def test_keepImportant(self):
		self.history.keepImportant = True
		self.assertEqual(self.history.prune(), 1)
		self.assertEqual(self.texts(), ["old pinned", "old important", "new"])

	def test_retentionZeroKeepsForever(self):
		self.history.retentionSeconds = 0
		self.assertEqual(self.history.prune(), 0)

	def test_capRemovesOldestUnpinned(self):
		self.history.retentionSeconds = 0
		self.history.maxEntries = 2
		self.assertEqual(self.history.prune(), 2)
		self.assertEqual(self.texts(), ["old pinned", "new"])

	def test_addOverCapPrunesInBatches(self):
		self.history.retentionSeconds = 0
		self.history.maxEntries = 4
		slack = self.history.pruneSlack
		for i in range(slack):
			self.history.add(rec(f"extra {i}", timestamp=self.clock.now))
		self.assertEqual(len(self.history), 4 + slack)
		self.history.add(rec("newest", timestamp=self.clock.now))
		self.assertEqual(len(self.history), 4)
		self.assertNotIn("old", self.texts())
		self.assertIn("old pinned", self.texts())


class TestPersistence(unittest.TestCase):
	def setUp(self):
		self._dir = tempfile.TemporaryDirectory()
		self.path = os.path.join(self._dir.name, "nc", "history.jsonl")
		self.clock = Clock()

	def tearDown(self):
		self._dir.cleanup()

	def make(self, **kwargs):
		return History(self.path, clock=self.clock, **kwargs)

	def lines(self):
		with open(self.path, encoding="utf-8") as f:
			return f.read().splitlines()

	def test_appendThenReload(self):
		h = self.make()
		h.add(rec("café ✓", appName="chrome", url="https://x.com/", domain="x.com"))
		self.assertTrue(h.needsFlush)
		h.flush()
		self.assertFalse(h.needsFlush)
		h.add(rec("second"))
		h.flush()
		self.assertEqual(len(self.lines()), 2)
		loaded = self.make()
		loaded.load()
		self.assertEqual([r.text for r in loaded.records], ["café ✓", "second"])
		self.assertEqual(loaded.records[0].domain, "x.com")
		# New ids continue after the loaded ones.
		self.assertEqual(loaded.add(rec("third")).id, 3)

	def test_changesRewriteFile(self):
		h = self.make()
		h.add(rec("a"))
		h.add(rec("b"))
		h.flush()
		h.delete([1])
		h.setPinned([h.get(2)], True)
		h.flush()
		self.assertEqual(len(self.lines()), 1)
		loaded = self.make()
		loaded.load()
		self.assertTrue(loaded.records[0].pinned)

	def test_damagedLinesSkipped(self):
		h = self.make()
		h.add(rec("good"))
		h.flush()
		with open(self.path, "a", encoding="utf-8") as f:
			f.write("{not json\n\n[1,2]\nnull\n")
			f.write('{"timestamp": "yesterday", "source": "uia", "text": "bad time"}\n')
			f.write('{"timestamp": 1000000.0, "source": "uia", "text": null}\n')
			f.write('{"timestamp": 1000001, "source": "uia", "text": "odd", "id": "7", "pinned": "no"}\n')
			f.write('{"timestamp": 1000002, "source": "uia", "text": "same id", "id": 1}\n')
		loaded = self.make()
		loaded.load()
		self.assertEqual([r.text for r in loaded.records], ["good", "odd", "same id"])
		self.assertEqual(loaded.loadErrors, 5)
		odd = loaded.records[1]
		self.assertFalse(odd.pinned)
		self.assertIsInstance(odd.timestamp, float)
		ids = {r.id for r in loaded.records}
		self.assertEqual(len(ids), 3)
		self.assertNotIn(0, ids)
		loaded.flush()
		self.assertEqual(len(self.lines()), 3)

	def test_loadPrunesOld(self):
		h = self.make()
		h.add(rec("old", timestamp=self.clock.now - 48 * HOUR))
		h.add(rec("new"))
		h.flush()
		loaded = self.make()
		loaded.load()
		self.assertEqual([r.text for r in loaded.records], ["new"])

	def test_setPathWritesEverything(self):
		h = History(None, clock=self.clock)
		h.add(rec("a"))
		h.flush()
		h.setPath(self.path)
		h.add(rec("b"))
		h.flush()
		self.assertEqual(len(self.lines()), 2)

	def test_memoryOnly(self):
		h = History(None, clock=self.clock)
		h.add(rec())
		self.assertFalse(h.needsFlush)
		h.flush()
		self.assertFalse(os.path.exists(self.path))


if __name__ == "__main__":
	unittest.main()
