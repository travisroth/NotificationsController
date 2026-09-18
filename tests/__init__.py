# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Unit tests for the parts of NotificationsController that do not need NVDA.

The add-on's package __init__ imports NVDA, so the package is registered here as a bare namespace
pointing at its folder. Its NVDA free modules (models, rules, history) then import normally.
"""

import os
import sys
import types

_PACKAGE_DIR = os.path.join(
	os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
	"addon",
	"globalPlugins",
	"notificationsController",
)

if "notificationsController" not in sys.modules:
	_package = types.ModuleType("notificationsController")
	_package.__path__ = [_PACKAGE_DIR]
	sys.modules["notificationsController"] = _package
