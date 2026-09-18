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
import queueHandler
import treeInterceptorHandler
from logHandler import log
from NVDAObjects import NVDAObject

from .models import (
	SOURCE_LIVE_REGION,
	SOURCE_TOAST,
	SOURCE_UIA,
	NotificationRecord,
	domainFromUrl,
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
_toastAppIds = ("appname", "attribution", "header")


def _describeTree(obj: NVDAObject, maxNodes: int = 40) -> tuple[list[str], str]:
	"""Lines describing a toast's descendants, and the name of one that looks like the app name."""
	lines: list[str] = []
	appName = ""
	stack: list[tuple[NVDAObject, int]] = [(obj, 0)]
	while stack and len(lines) < maxNodes:
		node, depth = stack.pop()
		automationId = str(_safe(lambda node=node: getattr(node, "UIAAutomationId", "")))
		name = str(_safe(lambda node=node: node.name))
		role = _safe(lambda node=node: node.role, None)
		roleName = role.displayString if isinstance(role, controlTypes.Role) else ""
		lines.append(f"{'  ' * depth}{roleName} id={automationId!r} name={name!r}")
		if not appName and name and any(key in automationId.casefold() for key in _toastAppIds):
			appName = name
		children = _safe(lambda node=node: node.children, [])
		for child in reversed(list(children)):
			stack.append((child, depth + 1))
	return lines, appName


def fromToast(obj: NVDAObject) -> NotificationRecord:
	name = str(_safe(lambda: obj.name))
	description = str(_safe(lambda: obj.description))
	text = name
	if description and description not in name:
		text = f"{name} {description}".strip()
	record = _newRecord(SOURCE_TOAST, text)
	record.appName, _product = appInfo(obj)
	lines, sender = _describeTree(obj)
	if not sender:
		match = _toastAppPattern.match(name)
		if match:
			sender = match.group(1).strip()
	record.appDisplayName = sender
	record.details = "\n".join(lines)
	record.windowTitle = foregroundTitle()
	return record


def livePoliteness(obj: NVDAObject) -> str:
	politeness = _safe(lambda: obj.liveRegionPoliteness, None)
	value = getattr(politeness, "value", politeness)
	return value if value in ("polite", "assertive") else ""


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
"""Callbacks ever handed to the helper. Never released, so a report arriving on an RPC thread while
the add-on is being unloaded cannot call into freed memory."""


class LiveRegionHook:
	"""Intercepts the live region reports NVDA's in-process helper sends from Chrome and Firefox.

	The helper calls through a function pointer exported by nvdaHelperLocal. The hook points it at
	its own callback, which runs on an RPC thread, notes the calling process and queues the report to
	NVDA's main thread. The handler there decides what to do and calls passthrough() to let NVDA
	present it as usual. The previous pointer is kept, so another add-on's hook still works, and is put
	back on uninstall.
	"""

	def __init__(self, handler: Callable[[str, str, int], None]):
		self._handler = handler
		self._callback = _ReportLiveRegionType(self._rpcCallback)
		_keepAlive.append(self._callback)
		self._previousPointer: int | None = None
		self._previous: Callable[[str, str], int] | None = None
		self.installed = False

	@staticmethod
	def _pointerSlot():
		import NVDAHelper

		return cast(getattr(NVDAHelper.localLib.dll, _DLL_SYMBOL), POINTER(c_void_p)).contents

	def install(self) -> bool:
		if self.installed:
			return True
		try:
			slot = self._pointerSlot()
			self._previousPointer = slot.value
			if not self._previousPointer:
				log.warning("NotificationsController: live region callback not set, not hooking it")
				return False
			self._previous = _ReportLiveRegionType(self._previousPointer)
			slot.value = cast(self._callback, c_void_p).value
		except Exception:
			log.error("NotificationsController: could not hook live region reports", exc_info=True)
			return False
		self.installed = True
		return True

	def uninstall(self) -> None:
		if not self.installed:
			return
		try:
			slot = self._pointerSlot()
			if slot.value == cast(self._callback, c_void_p).value:
				slot.value = self._previousPointer
			else:
				log.warning(
					"NotificationsController: live region callback was replaced by someone else; leaving it",
				)
		except Exception:
			log.error("NotificationsController: could not unhook live region reports", exc_info=True)
		self.installed = False

	def passthrough(self, text: str, politeness: str) -> None:
		"""Let NVDA handle a live region report as it would without the add-on."""
		if self._previous:
			self._previous(text, politeness)

	def _rpcCallback(self, text: str | None, politeness: str | None) -> int:
		# Runs on an RPC thread. Must not raise, and must not touch NVDA objects.
		text = text or ""
		politeness = politeness or ""
		try:
			processId = _rpcClientProcessId()
			queueHandler.queueFunction(queueHandler.eventQueue, self._handler, text, politeness, processId)
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
