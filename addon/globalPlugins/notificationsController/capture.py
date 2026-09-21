# NotificationsController: capture.py
# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Turning what NVDA receives into NotificationRecords.

There are four ways a notification reaches NVDA:

1. A UIA notification event (event_UIA_notification), raised by apps such as Teams, Office, Settings,
	and by Chromium browsers for ariaNotify.
2. A Windows toast popup (event_UIA_window_windowOpen, or event_alert, on a Notification object).
3. A live region change reported as an NVDA event (event_liveRegionChange). This covers UIA live
	regions (Edge and Chrome in UIA mode, many Windows apps), MSHTML and Win32 live regions.
4. A live region change in Chrome or Firefox through IAccessible2. NVDA's in-process helper watches
	these inside the browser and calls back into NVDA directly, with only the text and politeness:
	no event and no object. LiveRegionHook intercepts that callback.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from ctypes import POINTER, WINFUNCTYPE, byref, c_long, c_void_p, c_wchar_p, cast

import api
import controlTypes
import eventHandler
import queueHandler
import treeInterceptorHandler
from logHandler import log
from NVDAObjects import NVDAObject

from .models import (
	SOURCE_ALERT,
	SOURCE_LIVE_REGION,
	SOURCE_TOAST,
	SOURCE_UIA,
	NotificationRecord,
	domainFromUrl,
	toastText,
)

BROWSER_APPS = frozenset(
	(
		"chrome",
		"msedge",
		"firefox",
		"brave",
		"opera",
		"vivaldi",
		"thorium",
		"chromium",
		"waterfox",
		"librewolf",
		"zen",
	),
)
"""App module names of web browsers, whose notifications can be tied to a web page."""

_MAX_PARENT_WALK = 15


def _safe(getter: Callable[[], object], default: object = "") -> object:
	"""turns fragile property access into “best effort” access, which is essential in an add-on dealing with constantly changing accessibility objects."""
	try:
		value = getter()
	except Exception:
		return default
	return default if value is None else value


def _isWebUrl(url: str) -> bool:
	return bool(url) and url.split(":", 1)[0].lower() in ("http", "https", "file")


def appInfo(obj: NVDAObject) -> tuple[str, str]:
	"""The app module name and, when it differs, the product name of the app an object belongs to."""
	app = obj.appModule
	if not app:
		return "", ""
	appName = str(_safe(lambda: app.appName))
	productName = str(_safe(lambda: app.productName))
	if productName.casefold() == appName.casefold():
		productName = ""
	return appName, productName


def isBackground(obj: NVDAObject) -> bool:
	focus = api.getFocusObject()
	return bool(focus and obj.appModule is not focus.appModule)


def foregroundTitle() -> str:
	fg = api.getForegroundObject()
	return str(_safe(lambda: fg.name)) if fg else ""


def urlForTreeInterceptor(ti: object) -> str:
	for attr in ("documentURL", "documentConstantIdentifier"):
		url = str(_safe(lambda attr=attr: getattr(ti, attr, None)))
		if _isWebUrl(url):
			return url
	return ""


def urlForObject(obj: NVDAObject, walkParents: bool = False) -> str:
	"""The address of the web page an object is in, or an empty string.

	Uses the page's browse mode document when there is one. Otherwise, when walkParents is set,
	looks up the ancestors for a document whose value is a URL, which is how browsers expose it.
	"""
	ti = _safe(lambda: obj.treeInterceptor, None)
	if ti:
		url = urlForTreeInterceptor(ti)
		if url:
			return url
	if not walkParents:
		return ""
	current: NVDAObject | None = obj
	for _i in range(_MAX_PARENT_WALK):
		if current is None:
			break
		node = current
		if _safe(lambda node=node: node.role, None) == controlTypes.Role.DOCUMENT:
			url = str(_safe(lambda node=node: node.value))
			if _isWebUrl(url):
				return url
		current = _safe(lambda node=node: node.parent, None)
	return ""


def urlForProcess(processId: int) -> str:
	"""The address of the page most likely to have raised a notification in a browser process.

	Prefers the document with focus. Otherwise uses the process's browse mode documents, but only
	when they are all on the same site, since guessing between sites would scope rules wrongly.
	"""
	focus = api.getFocusObject()
	if focus and focus.processID == processId:
		url = urlForObject(focus)
		if url:
			return url
	urls = []
	for ti in list(treeInterceptorHandler.runningTable):
		root = getattr(ti, "rootNVDAObject", None)
		if root is None or _safe(lambda root=root: root.processID, None) != processId:
			continue
		url = urlForTreeInterceptor(ti)
		if url:
			urls.append(url)
	if urls and len({domainFromUrl(u) for u in urls}) == 1:
		return urls[0]
	return ""


def appInfoForProcess(processId: int) -> tuple[str, str]:
	import appModuleHandler

	app = appModuleHandler.getAppModuleFromProcessID(processId)
	appName = str(_safe(lambda: app.appName))
	productName = str(_safe(lambda: app.productName))
	if productName.casefold() == appName.casefold():
		productName = ""
	return appName, productName


def _newRecord(source: str, text: str) -> NotificationRecord:
	return NotificationRecord(timestamp=time.time(), source=source, text=text or "")


def _setUrl(record: NotificationRecord, url: str) -> None:
	record.url = url
	record.domain = domainFromUrl(url)


def fromUIANotification(
	obj: NVDAObject,
	notificationKind: int | None,
	notificationProcessing: int | None,
	displayString: str | None,
	activityId: str | None,
) -> NotificationRecord:
	record = _newRecord(SOURCE_UIA, displayString or "")
	record.appName, record.appDisplayName = appInfo(obj)
	record.notificationKind = notificationKind
	record.notificationProcessing = notificationProcessing
	record.activityId = activityId or ""
	record.background = isBackground(obj)
	record.windowTitle = foregroundTitle()
	if record.appName in BROWSER_APPS:
		url = urlForObject(obj, walkParents=True) or urlForProcess(obj.processID)
		_setUrl(record, url)
	return record


_toastAppPattern = re.compile(r"^New notification from (.+?)(?:[.,:\n]|$)", re.IGNORECASE)
_toastSenderIds = ("sendername", "appname", "attribution")
"""Automation IDs of the part of a toast that names the sending app, lower case."""


def _describeTree(obj: NVDAObject, maxNodes: int = 40) -> tuple[list[str], str, list[str]]:
	"""Look through a toast's descendants.

	:return: Lines describing each element for the history details, the name of the element that names
		the sending app, and the text of the toast's other text elements (title, message) in order.
	"""
	lines: list[str] = []
	sender = ""
	parts: list[str] = []
	stack: list[tuple[NVDAObject, int]] = [(obj, 0)]
	while stack and len(lines) < maxNodes:
		node, depth = stack.pop()
		automationId = str(_safe(lambda node=node: getattr(node, "UIAAutomationId", "")))
		name = str(_safe(lambda node=node: node.name)).strip()
		role = _safe(lambda node=node: node.role, None)
		roleName = role.displayString if isinstance(role, controlTypes.Role) else ""
		lines.append(f"{'  ' * depth}{roleName} id={automationId!r} name={name!r}")
		if depth > 0 and name:
			if any(key in automationId.casefold() for key in _toastSenderIds):
				if not sender:
					sender = name
			elif role == controlTypes.Role.STATICTEXT:
				parts.append(name)
		children = _safe(lambda node=node: node.children, [])
		for child in reversed(list(children)):
			stack.append((child, depth + 1))
	return lines, sender, parts


def fromToast(obj: NVDAObject) -> NotificationRecord:
	name = str(_safe(lambda: obj.name))
	description = str(_safe(lambda: obj.description))
	fullName = name
	if description and description not in name:
		fullName = f"{name} {description}".strip()
	lines, sender, parts = _describeTree(obj)
	if not sender:
		match = _toastAppPattern.match(name)
		if match:
			sender = match.group(1).strip()
	record = _newRecord(SOURCE_TOAST, toastText(fullName, parts))
	record.appName, _product = appInfo(obj)
	record.appDisplayName = sender
	record.details = "\n".join([f"Spoken as: {fullName}", *lines])
	record.windowTitle = foregroundTitle()
	return record


def livePoliteness(obj: NVDAObject) -> str:
	politeness = _safe(lambda: obj.liveRegionPoliteness, None)
	value = getattr(politeness, "value", politeness)
	return value if value in ("polite", "assertive") else ""


_MAX_ALERT_NODES = 60


def isReportableAlert(obj: NVDAObject) -> bool:
	"""Whether NVDA would report an alert event on this object, as NVDA's IAccessible alert handler
	decides: the object has the alert role and the focus is not inside it.

	Like NVDA, a pending focus event is processed first, since an alert event can arrive just before the
	focus moves into the alert, and then NVDA does not report it.
	"""
	if _safe(lambda: obj.role, None) != controlTypes.Role.ALERT:
		return False
	try:
		if eventHandler.isPendingEvents("gainFocus"):
			api.processPendingEvents()
		return obj not in api.getFocusAncestors() and obj != api.getFocusObject()
	except Exception:
		return True


def alertText(obj: NVDAObject) -> str:
	"""The text of an alert: its name and description, or the text inside it when it has no name.

	Pop-ups built from web content, such as Teams' own notifications, often have no name of their own,
	so the text of their descendants is gathered in order, skipping repeats and buttons.
	"""
	name = str(_safe(lambda: obj.name)).strip()
	description = str(_safe(lambda: obj.description)).strip()
	parts = [p for p in (name, description) if p]
	if parts and not (len(parts) == 1 and description and not name):
		return ", ".join(dict.fromkeys(parts))
	gathered: list[str] = []
	stack: list[NVDAObject] = list(reversed(list(_safe(lambda: obj.children, []))))
	visited = 0
	while stack and visited < _MAX_ALERT_NODES:
		node = stack.pop()
		visited += 1
		role = _safe(lambda node=node: node.role, None)
		if role in (controlTypes.Role.BUTTON, controlTypes.Role.MENUBUTTON, controlTypes.Role.LINK):
			continue
		children = list(_safe(lambda node=node: node.children, []))
		if children:
			stack.extend(reversed(children))
			continue
		text = str(_safe(lambda node=node: node.name)).strip()
		if text and (not gathered or gathered[-1] != text):
			gathered.append(text)
	return ", ".join([*parts, *gathered]) if gathered else ", ".join(parts)


def fromAlert(obj: NVDAObject) -> NotificationRecord:
	record = _newRecord(SOURCE_ALERT, alertText(obj))
	record.appName, record.appDisplayName = appInfo(obj)
	record.background = isBackground(obj)
	record.windowTitle = foregroundTitle()
	_setUrl(record, urlForObject(obj, walkParents=True))
	return record


def fromLiveRegionEvent(obj: NVDAObject) -> NotificationRecord:
	record = _newRecord(SOURCE_LIVE_REGION, str(_safe(lambda: obj.name)))
	record.appName, record.appDisplayName = appInfo(obj)
	record.politeness = livePoliteness(obj)
	record.background = isBackground(obj)
	record.windowTitle = foregroundTitle()
	isBrowser = record.appName in BROWSER_APPS
	_setUrl(record, urlForObject(obj, walkParents=isBrowser))
	return record


def fromHelperLiveRegion(text: str, politeness: str, processId: int) -> NotificationRecord:
	record = _newRecord(SOURCE_LIVE_REGION, text)
	record.politeness = politeness.lower() if politeness.lower() in ("polite", "assertive") else ""
	if processId:
		record.appName, record.appDisplayName = appInfoForProcess(processId)
		focus = api.getFocusObject()
		record.background = not (focus and focus.processID == processId)
		_setUrl(record, urlForProcess(processId))
	record.windowTitle = foregroundTitle()
	return record


_ReportLiveRegionType = WINFUNCTYPE(c_long, c_wchar_p, c_wchar_p)
_DLL_SYMBOL = "_nvdaControllerInternal_reportLiveRegion"
_keepAlive: list[object] = []
"""Callbacks ever handed to the helper. Never released: another add-on that hooked the same pointer
after this one may still call it, and a report may be in flight on an RPC thread while the add-on
unloads. A released callback would be a call into freed memory. Each kept callback holds only its small
hook object, which drops the plugin's handler when uninstalled."""


class LiveRegionHook:
	"""Intercepts the live region reports NVDA's in-process helper sends from Chrome and Firefox.

	The helper calls through a function pointer exported by nvdaHelperLocal. The hook points it at
	its own callback, which runs on an RPC thread, notes the calling process and queues the report to
	NVDA's main thread. The handler there decides what to do and calls passthrough() to let NVDA
	present it as usual.

	Hooks chain: the pointer that was there before is kept and called for passthrough, so a hook another
	add-on installed earlier still works. Uninstalling puts the previous pointer back when this hook is
	still the one installed. When another hook was installed on top, this hook's callback stays in that
	hook's chain, so uninstalling also makes it inactive: an inactive callback forwards every report
	straight to the previous pointer and never reaches the add-on again, including reports that were
	already queued when it was uninstalled.
	"""

	def __init__(self, handler: Callable[[str, str, int], None]):
		self._handler: Callable[[str, str, int], None] | None = handler
		self._callback = _ReportLiveRegionType(self._rpcCallback)
		_keepAlive.append(self._callback)
		self._previousPointer: int | None = None
		self._previous: Callable[[str, str], int] | None = None
		self.active = False
		"""True while reports go to the handler. Once False, it stays False."""
		self.installed = False

	@staticmethod
	def _pointerSlot():
		import NVDAHelper

		return cast(getattr(NVDAHelper.localLib.dll, _DLL_SYMBOL), POINTER(c_void_p)).contents

	@property
	def callbackAddress(self) -> int:
		return cast(self._callback, c_void_p).value or 0

	def install(self) -> bool:
		if self.installed:
			return True
		if self._handler is None:
			# Uninstalled hooks are not reused; make a new one.
			return False
		try:
			slot = self._pointerSlot()
			self._previousPointer = slot.value
			if not self._previousPointer:
				log.warning("NotificationsController: live region callback not set, not hooking it")
				return False
			self._previous = _ReportLiveRegionType(self._previousPointer)
			self.active = True
			slot.value = self.callbackAddress
		except Exception:
			self.active = False
			log.error("NotificationsController: could not hook live region reports", exc_info=True)
			return False
		self.installed = True
		return True

	def uninstall(self) -> None:
		# Deactivate before touching the pointer, so from this moment no report reaches the handler.
		self.active = False
		self._handler = None
		if not self.installed:
			return
		self.installed = False
		try:
			slot = self._pointerSlot()
			if slot.value == self.callbackAddress:
				slot.value = self._previousPointer
			else:
				log.debug(
					"NotificationsController: another hook was installed after this one; "
					"leaving this hook inactive in its chain",
				)
		except Exception:
			log.error("NotificationsController: could not unhook live region reports", exc_info=True)

	def passthrough(self, text: str, politeness: str) -> None:
		"""Let NVDA handle a live region report as it would without the add-on."""
		if self._previous:
			self._previous(text, politeness)

	def _dispatch(self, text: str, politeness: str, processId: int) -> None:
		"""Runs on the main thread for each queued report."""
		handler = self._handler
		if not self.active or handler is None:
			# Uninstalled after this report was queued.
			self.passthrough(text, politeness)
			return
		handler(text, politeness, processId)

	def _rpcCallback(self, text: str | None, politeness: str | None) -> int:
		# Runs on an RPC thread. Must not raise, and must not touch NVDA objects.
		text = text or ""
		politeness = politeness or ""
		if not self.active:
			return self._previous(text, politeness) if self._previous else 0
		try:
			processId = _rpcClientProcessId()
			queueHandler.queueFunction(queueHandler.eventQueue, self._dispatch, text, politeness, processId)
		except Exception:
			log.error("NotificationsController: live region callback failed", exc_info=True)
			if self._previous:
				return self._previous(text, politeness)
		return 0


def _rpcClientProcessId() -> int:
	"""The process that made the current RPC call. Only valid on the RPC thread during the call."""
	from winBindings import rpcrt4

	pid = c_long()
	rpcrt4.I_RpcBindingInqLocalClientPID(None, byref(pid))
	return pid.value
