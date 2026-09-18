# NotificationsController: __init__.py
# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""NotificationsController: control and review the notifications apps and web pages send to NVDA.

Out of the box the add-on changes nothing. It logs every notification and lets NVDA handle it as
usual, so the user can see what arrives and then write rules for it.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import addonHandler
import globalPluginHandler
import globalVars
import gui
import ui
import wx
from logHandler import log
from NVDAObjects import NVDAObject
from NVDAObjects.behaviors import Notification
from scriptHandler import script

from . import capture, settings
from .controller import Controller
from .models import NotificationRecord

addonHandler.initTranslation()

# Translators: The name of the add-on, used as the input gestures category and in the Tools menu.
ADDON_NAME = _("Notifications Controller")

_TOAST_REPEAT_SECONDS = 1.0
"""NVDA ignores a toast that opens again within this time (NVDA issue 7128); so does the add-on."""


def describeRecord(record: NotificationRecord) -> str:
	when = time.strftime("%X", time.localtime(record.timestamp))
	app = record.appLabel or record.domain
	if app:
		# Translators: A notification read from history: its text, the app it came from, and the time.
		return _("{text}; {app}, {time}").format(text=record.text, app=app, time=when)
	# Translators: A notification read from history: its text and the time.
	return _("{text}; {time}").format(text=record.text, time=when)


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
	scriptCategory = ADDON_NAME

	def __init__(self):
		super().__init__()
		settings.registerConfig()
		self.controller = Controller()
		self.controller.start()
		self._liveRegionHook = capture.LiveRegionHook(self._onHelperLiveRegion)
		self._liveRegionHook.install()
		self._lastToast: tuple[object, float] = (None, 0.0)
		self._reviewId: int | None = None
		self._historyDialog = None
		self._rulesDialog = None
		self._menuItem = None
		self._createMenu()
		from .settingsPanel import NotificationsControllerPanel

		self._settingsPanel = NotificationsControllerPanel
		NotificationsControllerPanel.controller = self.controller
		gui.settingsDialogs.NVDASettingsDialog.categoryClasses.append(NotificationsControllerPanel)
		if self.controller.rulesLoadError:
			log.warning(f"NotificationsController: rules were not loaded: {self.controller.rulesLoadError}")

	def terminate(self):
		self._liveRegionHook.uninstall()
		self.controller.stop()
		try:
			gui.settingsDialogs.NVDASettingsDialog.categoryClasses.remove(self._settingsPanel)
		except ValueError:
			pass
		for dialog in (self._historyDialog, self._rulesDialog):
			if dialog:
				try:
					dialog.Destroy()
				except RuntimeError:
					pass
		if self._menuItem:
			try:
				gui.mainFrame.sysTrayIcon.toolsMenu.Remove(self._menuItem)
			except (RuntimeError, AttributeError):
				pass
		super().terminate()

	# Menu

	def _createMenu(self) -> None:
		tray = gui.mainFrame.sysTrayIcon
		menu = wx.Menu()
		items: list[tuple[str, Callable[[], None]]] = [
			# Translators: A Tools menu item that opens the notification history.
			(_("Notification &history..."), self.showHistory),
			# Translators: A Tools menu item that opens the notification rules.
			(_("&Rules..."), self.showRules),
			# Translators: A Tools menu item that opens the add-on's settings.
			(_("&Settings..."), self.showSettings),
		]
		for label, handler in items:
			item = menu.Append(wx.ID_ANY, label)
			tray.Bind(wx.EVT_MENU, lambda evt, handler=handler: handler(), item)
		self._menuItem = tray.toolsMenu.AppendSubMenu(menu, ADDON_NAME)

	# Windows

	def showHistory(self, record: NotificationRecord | None = None) -> None:
		from .historyDialog import HistoryDialog

		dialog = HistoryDialog.showInstance(self.controller, self)
		self._historyDialog = dialog
		if record:
			dialog.selectRecord(record.id)

	def showRules(self) -> None:
		from .rulesDialog import RulesDialog

		self._rulesDialog = RulesDialog.showInstance(self.controller)

	def showSettings(self) -> None:
		gui.mainFrame.popupSettingsDialog(gui.settingsDialogs.NVDASettingsDialog, self._settingsPanel)

	def createRuleFrom(self, record: NotificationRecord | None, parent: wx.Window | None = None) -> None:
		from .ruleEditor import editNewRuleFromRecord

		editNewRuleFromRecord(self.controller, record, parent)

	# Events

	def event_UIA_notification(
		self,
		obj: NVDAObject,
		nextHandler: Callable[[], None],
		notificationKind: int | None = None,
		notificationProcessing: int | None = None,
		displayString: str | None = None,
		activityId: str | None = None,
		**kwargs,
	):
		if not settings.conf()["captureUIA"] or not displayString:
			nextHandler()
			return
		try:
			record = capture.fromUIANotification(
				obj,
				notificationKind,
				notificationProcessing,
				displayString,
				activityId,
			)
		except Exception:
			log.error("NotificationsController: could not read a UIA notification", exc_info=True)
			nextHandler()
			return
		self.controller.process(record, nextHandler)

	def _handleToast(self, obj: NVDAObject, nextHandler: Callable[[], None]) -> None:
		if not settings.conf()["captureToasts"]:
			nextHandler()
			return
		runtimeId = None
		try:
			runtimeId = tuple(obj.UIAElement.getRuntimeID())
		except Exception:
			pass
		now = time.time()
		lastId, lastTime = self._lastToast
		if runtimeId is not None:
			self._lastToast = (runtimeId, now)
			if runtimeId == lastId and now - lastTime < _TOAST_REPEAT_SECONDS:
				# NVDA drops this repeat itself.
				nextHandler()
				return
		try:
			record = capture.fromToast(obj)
		except Exception:
			log.error("NotificationsController: could not read a toast", exc_info=True)
			nextHandler()
			return
		self.controller.process(record, nextHandler)

	def event_UIA_window_windowOpen(self, obj: NVDAObject, nextHandler: Callable[[], None]):
		if isinstance(obj, Notification):
			self._handleToast(obj, nextHandler)
		else:
			nextHandler()

	def event_alert(self, obj: NVDAObject, nextHandler: Callable[[], None]):
		if isinstance(obj, Notification):
			self._handleToast(obj, nextHandler)
		elif capture.isReportableAlert(obj):
			self._handleAlert(obj, nextHandler)
		else:
			nextHandler()

	def event_UIA_systemAlert(self, obj: NVDAObject, nextHandler: Callable[[], None]):
		self._handleAlert(obj, nextHandler)

	def _handleAlert(self, obj: NVDAObject, nextHandler: Callable[[], None]) -> None:
		"""An object with the alert role, such as an app's own notification pop-up or a web page alert.

		NVDA accepts events from topmost windows even when their app is in the background, so an app's own
		notification pop-up can be reported this way while another app has focus.
		"""
		if not settings.conf()["captureAlerts"]:
			nextHandler()
			return
		try:
			record = capture.fromAlert(obj)
		except Exception:
			log.error("NotificationsController: could not read an alert", exc_info=True)
			nextHandler()
			return
		self.controller.process(record, nextHandler)

	def event_liveRegionChange(self, obj: NVDAObject, nextHandler: Callable[[], None]):
		if not settings.conf()["captureLiveRegions"]:
			nextHandler()
			return
		try:
			record = capture.fromLiveRegionEvent(obj)
		except Exception:
			log.error("NotificationsController: could not read a live region", exc_info=True)
			nextHandler()
			return
		self.controller.process(record, nextHandler)

	def _onHelperLiveRegion(self, text: str, politeness: str, processId: int) -> None:
		"""A live region report from NVDA's in-process helper (Chrome, Firefox), on the main thread."""

		def passthrough():
			self._liveRegionHook.passthrough(text, politeness)

		if not settings.conf()["captureLiveRegions"]:
			passthrough()
			return
		import api

		focus = api.getFocusObject()
		if focus and focus.sleepMode == focus.SLEEP_FULL:
			passthrough()
			return
		try:
			record = capture.fromHelperLiveRegion(text, politeness, processId)
		except Exception:
			log.error("NotificationsController: could not read a live region report", exc_info=True)
			passthrough()
			return
		self.controller.process(record, passthrough)

	# Scripts

	@script(
		# Translators: Describes a command in input gestures.
		description=_("Opens the notification history"),
	)
	def script_showHistory(self, gesture):
		wx.CallAfter(self.showHistory)

	@script(
		# Translators: Describes a command in input gestures.
		description=_("Opens the notification rules"),
	)
	def script_showRules(self, gesture):
		wx.CallAfter(self.showRules)

	@script(
		# Translators: Describes a command in input gestures.
		description=_("Reports the most recent notification"),
	)
	def script_reportLatest(self, gesture):
		record = self.controller.history.latest()
		if not record:
			# Translators: Reported when there are no notifications in history.
			ui.message(_("No notifications"))
			return
		self._reviewId = record.id
		self._reportForReview(record)

	def _reportForReview(self, record: NotificationRecord) -> None:
		ui.message(describeRecord(record))
		if not record.reviewed:
			self.controller.history.setReviewed([record])

	def _moveReview(self, step: int) -> None:
		records = self.controller.history.records
		if not records:
			# Translators: Reported when there are no notifications in history.
			ui.message(_("No notifications"))
			return
		index = next((i for i, r in enumerate(records) if r.id == self._reviewId), None)
		if index is None:
			# Nothing reviewed yet, or that entry is gone: start just past the newest.
			index = len(records)
		newIndex = index + step
		if newIndex < 0:
			# Translators: Reported when moving back past the oldest notification in history.
			ui.message(_("Oldest notification"))
			newIndex = 0
		elif newIndex >= len(records):
			# Translators: Reported when moving forward past the newest notification in history.
			ui.message(_("Newest notification"))
			newIndex = len(records) - 1
		record = records[newIndex]
		self._reviewId = record.id
		self._reportForReview(record)

	@script(
		# Translators: Describes a command in input gestures.
		description=_("Reports the previous (older) notification in history"),
	)
	def script_previousNotification(self, gesture):
		self._moveReview(-1)

	@script(
		# Translators: Describes a command in input gestures.
		description=_("Reports the next (newer) notification in history"),
	)
	def script_nextNotification(self, gesture):
		self._moveReview(1)

	@script(
		# Translators: Describes a command in input gestures.
		description=_(
			"Creates a rule from the notification last reported by the review commands, "
			"or from the most recent notification",
		),
	)
	def script_createRule(self, gesture):
		history = self.controller.history
		record = (history.get(self._reviewId) if self._reviewId is not None else None) or history.latest()
		if not record:
			# Translators: Reported when there are no notifications in history.
			ui.message(_("No notifications"))
			return
		wx.CallAfter(self.createRuleFrom, record)

	@script(
		# Translators: Describes a command in input gestures.
		description=_("Turns Do not disturb on or off. When on, only important notifications are reported"),
	)
	def script_toggleDoNotDisturb(self, gesture):
		conf = settings.conf()
		conf["doNotDisturb"] = not conf["doNotDisturb"]
		if conf["doNotDisturb"]:
			# Translators: Reported when Do not disturb is turned on.
			ui.message(_("Do not disturb on"))
		else:
			# Translators: Reported when Do not disturb is turned off.
			ui.message(_("Do not disturb off"))

	@script(
		# Translators: Describes a command in input gestures.
		description=_(
			"Turns Notifications Controller on or off. When off, NVDA handles notifications as usual"
		),
	)
	def script_toggleEnabled(self, gesture):
		conf = settings.conf()
		conf["enabled"] = not conf["enabled"]
		if conf["enabled"]:
			# Translators: Reported when the add-on is turned on.
			ui.message(_("Notifications Controller on"))
		else:
			# Translators: Reported when the add-on is turned off.
			ui.message(_("Notifications Controller off"))


if globalVars.appArgs.secure:
	# Do nothing on secure screens: no logging of what appears there, and no settings to change.
	GlobalPlugin = globalPluginHandler.GlobalPlugin
