# NotificationsController Implementation Plan

NotificationsController is a global NVDA add-on that watches the notifications Windows, applications, and web pages send to screen readers (UIA notifications, toasts, and ARIA live regions), logs every one of them, and lets the user decide per app and per text pattern whether each is spoken, brailled, played as a sound, or silenced. Out of the box it changes nothing: it only observes and logs, so the user can see what arrives and build rules from real examples.

## 1. Goals and non-goals

Goals:

1. Capture every UIA notification, toast, and ARIA live region announcement NVDA receives, with the text, source app, website (for web content), and metadata.
2. Default to pass-through: after install, NVDA behaves exactly as before, but everything is logged.
3. Let the user create rules (app plus text match) that pick an output action and a category.
4. Provide a history of notifications that can be reviewed, filtered, and turned into rules.
5. Automatically delete old history after a configurable retention period.
6. Stay compatible with NVDA core and other add-ons by always deferring to nextHandler unless a rule says otherwise.

Non-goals for version 1:

1. Syncing rules across machines or cloud features.
2. Changing how Windows itself shows or stores toasts (Action Center).
3. Backward compatibility with older NVDA releases. The minimum supported version is NVDA 2026.2.
4. Shipping default or starter rules. If good candidates turn up during use, a default rules file can be added later.

## 2. How notifications reach NVDA (capture points)

There are three separate paths, and the add-on must hook all of them.

1. UIA NotificationEvent. Apps call RaiseNotificationEvent (Teams, Office, Windows Settings, Calculator, File Explorer, Chromium browsers through ariaNotify, and many others). NVDA exposes this to global plugins as event_UIA_notification(self, obj, nextHandler, notificationKind=None, notificationProcessing=None, displayString=None, activityId=None). displayString is the text; activityId is a stable identifier that is often better than text for matching (for example a volume or brightness change).
2. Toast windows. Windows toast popups arrive as event_UIA_window_windowOpen on an object that is an instance of NVDAObjects.behaviors.Notification, which NVDA then reports through event_alert. This is the path shown in the project brief.
3. ARIA live regions. Web content marks regions with aria-live (or roles such as status, alert, log). NVDA reports changes through event_liveRegionChange on the changed object, which a global plugin can intercept. Live region rules must be scoped to a website, because some sites use aria-live well and others abuse it badly. The page address is read from the object's treeInterceptor (documentConstantIdentifier, which NVDA already uses to identify browse mode documents by URL), with a fallback of walking up to the document object and reading its value or URL attribute. Politeness (polite or assertive) is recorded when the browser exposes it.

For web content arriving through the UIA path (ariaNotify in Chromium browsers), the event carries no URL. When the source app is a browser, the add-on attaches the URL of the focused document's treeInterceptor so that those notifications can be scoped by website too.

For all paths the add-on builds a NotificationRecord and passes it through the rule engine before deciding whether to call nextHandler.

Research items to confirm early (spike in Phase 1):

1. Exactly which NVDA versions filter UIA notifications from background apps, and whether a global plugin sees them before that filter.
2. For toasts, the owning process is the shell (ShellExperienceHost or explorer), not the sending app. The real source app must be read from the toast content (the toast's app name text, or the AutomationId or name structure of the toast). Build a small resolver for this and record both values.
3. Whether event_alert also fires for the same toast (to avoid double logging), and how NVDA's own speech of the toast is triggered so it can be suppressed cleanly.
4. Duplicate events: Chromium and Teams sometimes raise the same text twice in quick succession. Decide on a dedupe window (proposed 500 ms, same app and same text).
5. Live region reliability: confirm that the URL can be obtained for live region events in Chrome, Edge, and Firefox, including inside iframes (where the frame's URL may differ from the top-level page; the rule should match on the top-level site). Confirm how NVDA coalesces partial text updates so the logged text matches what NVDA would have spoken.
6. Live region volume: abusive sites can fire many updates per second. Measure the cost of building a record per event and make sure the fast path (no matching rule, logging on) stays cheap.

Findings from reading NVDA's source (2026.2 and 2026.3):

1. Background UIA notifications: NVDA drops them in the object's own handler (NVDAObjects.UIA.UIA.event_UIA_notification checks the focus app module). Global plugins run earlier in the event chain, so the add-on sees them, and a rule with its own output can report them. The add-on records a background flag.
2. Chrome and Firefox live regions (IAccessible2) are not NVDA events at all. NVDA's in-process helper watches them inside the browser and calls nvdaControllerInternal_reportLiveRegion(text, politeness) over RPC, which speaks and brailles directly. The add-on repoints that callback's function pointer in nvdaHelperLocal (keeping the previous pointer, and restoring it on exit), gets the calling process from the RPC binding, and queues the report to the main thread. There is no object, so the page address comes from the focused document in that process, or from the process's open documents when they are all on one site.
3. UIA, MSHTML and Win32 live regions do arrive as event_liveRegionChange, with an object, so the page address comes from the object's browse mode document or its ancestors.
4. Toasts: NVDA's Toast_win10 ignores the same toast (same runtime ID) within 1 second; the add-on does the same so it does not log repeats NVDA would not report.
5. Sleep mode: NVDA does not run events for objects in sleep mode, and the helper callback is checked against sleep mode by the add-on.
6. Alerts are a fourth path. NVDA reports alert events on objects with the alert role, and UIA system alerts, and its event filter accepts events from topmost windows even when their app is in the background. Apps' own notification pop-ups can reach NVDA this way. Like NVDA, the add-on processes a pending focus event first and skips an alert the focus is inside.
7. The live region hook can be chained with another add-on's hook. Uninstalling makes the hook inactive before restoring the pointer; an inactive callback forwards straight to the previous pointer, including reports already queued, and drops its reference to the plugin.
8. Storage settings (saving history, retention, limits, duplicate window) are global, in settings.json, not in NVDA's configuration profiles, so a profile switch cannot start writing notifications to disk or delete history.

## 3. Data model

NotificationRecord (one per received notification):

1. id (incrementing integer or UUID)
2. timestamp (UTC, stored as ISO text)
3. source: "uia", "toast", or "liveRegion"
4. appName: NVDA appModule name, for example ms-teams, chrome, msedge, explorer
5. appDisplayName: friendly name when available (for toasts, the resolved sending app)
6. windowTitle: foreground window title at the time, optional
7. text: displayString or toast text
8. notificationKind, notificationProcessing, activityId (UIA path only)
9. url and domain (web content only; domain is the host name, for example teams.microsoft.com)
10. politeness: polite or assertive (live regions only, when available)
11. matchedRuleId (or none)
12. actionTaken: what the add-on did (passthrough, speech, braille, sound, silent, and so on)
13. category: important, informational, spam, or a user-defined category
14. reviewed flag, and pinned flag (pinned entries are exempt from auto-deletion)

Rule:

1. id, name, enabled
2. app: a specific appName, or "any app"
3. source: uia, toast, liveRegion, or any
4. site scope (for web content):
   1. domain, for example example.com
   2. includeSubdomains (default yes, so example.com also covers www.example.com and app.example.com)
   3. urlPrefix (optional, advanced), for example https://example.com/app/, for sites where only one section misbehaves
   4. A rule with a site scope only matches records that have a URL. Rules for live regions are expected to always have a site scope; the editor warns if a live region rule has none, since an unscoped rule would apply to every website.
5. matchType: any text, equals, starts with, contains, regular expression
6. pattern, caseSensitive
7. activityId (optional, exact match)
8. politeness (optional, live regions only): any, polite, or assertive
9. action: a set of flags rather than a single choice, so combinations work
   1. speak (yes or no)
   2. braille (yes or no)
   3. sound: none, a built-in sound, or a user-chosen wav file
   4. The resulting presets offered in the UI: default NVDA behavior, speak and braille, speak only, braille only, sound only, sound plus speech, sound plus braille, sound plus speech and braille, silent
10. category (important, informational, spam, custom)
11. log: yes or no (for truly noisy items the user may not want in history, such as a site that abuses live regions)
12. Order position. Rules are evaluated top to bottom; the first enabled match wins. The UI supports move up and move down.

"Starts with" is the default match type in the rule editor since it is expected to be the most common. Regular expressions are compiled once when rules load; patterns that fail to compile are rejected, with the error shown in the editor. Compiling only checks syntax, so it does not make a pattern safe: a valid pattern can still backtrack catastrophically. Matching therefore uses the regex package NVDA ships, with a 0.1 second timeout per search. A rule whose pattern times out is turned off until the rules change, and this is logged. Matching also looks at no more than the first 4000 characters, as a second safeguard.

## 4. Behavior of an incoming notification

1. Build the NotificationRecord from the event.
2. If the add-on is globally paused or disabled, log (if logging is on) and call nextHandler.
3. Dedupe check. If it is a duplicate within the window, call nextHandler or drop according to the matched action, and do not log it twice.
4. Find the first matching rule.
5. No match: action is passthrough. Log with category "unclassified" and call nextHandler. This is the install default.
6. Match with the "default NVDA behavior" action: log and call nextHandler.
7. Match with any other action: do not call nextHandler. Instead:
   1. speak only: speech.speakMessage(text)
   2. braille only: braille.handler.message(text)
   3. speak and braille: ui.message(text)
   4. sound: nvwave.playWaveFile(path, asynchronous=True), before any speech when combined
   5. silent: nothing
8. Append the record to history (unless the rule turns logging off).

Keep the event handler fast. All disk writes happen off the event path (see Storage).

A "Do not disturb" mode (toggle by gesture) silences everything that is not categorized important, while still logging. This covers meetings and presentations without editing rules.

## 5. Storage

Location: a folder named notificationsController inside the NVDA user configuration directory (globalVars.appArgs.configPath), so it follows portable copies and per-user config.

1. Settings: config.conf["notificationsController"] with a confspec. Holds enabled, loggingEnabled, retention period, maximum history entries, dedupe window, persistHistory (keep history across restarts or memory only), doNotDisturb, default sound.
2. Rules: rules.json, with a schemaVersion field for future migrations. Written atomically (write to a temp file, then replace).
3. History: JSON Lines file (history.jsonl), one record per line. Held in memory as a deque; new records are appended to disk by a background writer thread or a short wx timer that batches writes. Pruning rewrites the file.

Alternative considered: sqlite3. It would make filtering easier, but JSON Lines is simpler, has no dependency questions across NVDA builds, and history sizes are small. Revisit if history grows large.

Privacy: notification text can include message content from chats and email. The settings panel states that history is stored locally in plain text, offers "keep history in memory only", and offers a "clear all history" button.

## 6. Auto-deletion

1. Retention choices: 1 hour, 12 hours, 1 day (default), 3 days, 7 days, 30 days, never.
2. Also a maximum entry cap (default 5000) so a noisy app cannot grow the file without limit.
3. Pruning runs at startup, then hourly on a wx timer, and immediately when the retention setting changes.
4. Pinned entries are never pruned automatically. An option decides whether "important" entries get a longer retention.

## 7. User interface

All UI is standard wx controls tested with NVDA itself: plain list controls with accessible column labels, no custom-drawn controls, keyboard shortcuts for every button, and sensible focus on open.

1. NVDA menu entry: Tools, Notifications Controller, with submenu items Notification History, Rules, and Settings.
2. Settings panel (inside NVDA Settings, a SettingsPanel subclass):
   1. Enable the add-on
   2. Log notifications
   3. Keep history across restarts
   4. History retention period, maximum entries
   5. Duplicate suppression window
   6. Default sound for sound actions
   7. Clear all history button
3. Notification History dialog:
   1. Filter controls at the top: app (combo box populated from apps seen in history), website domain (populated from domains seen), category, source, text search, and "unreviewed only".
   2. A list of notifications, newest first, with time, app, website, category, action, and text.
   3. A read-only multi-line text field showing the full details of the selected item.
   4. Buttons: Create rule from this notification, Mark important or pin, Mark reviewed, Copy, Delete, Delete all shown, Close.
   5. Enter on an item speaks it again; the list is navigable with arrow keys like any list.
4. Rules dialog:
   1. List of rules in evaluation order, showing name, app, website, match, action, enabled.
   2. Buttons: Add, Edit, Duplicate, Delete, Move up, Move down, Enable or disable, Import, Export.
5. Rule editor dialog:
   1. Name, app (editable combo prefilled with apps seen in history, plus "any app"), source, website domain (editable combo prefilled with domains seen), include subdomains, URL prefix, match type, pattern, case sensitive, activityId, politeness, action preset, sound file browse button with a Play button to preview, category, log checkbox.
   2. A "Test" area showing how many history entries this rule would match, with a preview list, so the user can check a regex before saving.
   3. When opened from a history entry, fields are prefilled: app, website domain (for web content), match type starts with, and pattern set to the first words of the text.
   4. A quick option in the history dialog, "Silence all live regions from this site", creates a site-scoped live region rule with match type any text in one step, for sites that abuse aria-live.

The key workflow is: install, use the computer normally, open history, pick a noisy notification, press Create rule, choose Silent or Sound only, save.

## 8. Commands (scripts)

Scripts have no default gestures. They appear in NVDA's Input Gestures under a "Notifications Controller" category, so users assign their own keys (or bind them through another add-on's layered keyboard, such as BrlMultiline). Script names and descriptions should be clear and stable so they are easy to find and bind. Scripts:

1. Open notification history
2. Open rules
3. Report the most recent notification
4. Move to previous or next notification in history and report it (a quick review without opening a dialog)
5. Create a rule from the most recent notification
6. Toggle Do not disturb
7. Toggle the whole add-on on or off

## 9. Code layout

All code under addon/globalPlugins/notificationsController/. The rule engine and history store avoid NVDA imports so they can be unit tested with plain Python.

1. __init__.py: GlobalPlugin class, event handlers, scripts, menu registration, startup and shutdown (stop timers, flush writes).
2. capture.py: turns events and objects into NotificationRecord, including the toast source-app resolver.
3. models.py: NotificationRecord, Rule, Action dataclasses with to_dict and from_dict.
4. rules.py: rule loading, validation, regex compilation, first-match evaluation. Pure Python.
5. history.py: in-memory deque, JSON Lines persistence, batched writer, pruning, filtering. Pure Python apart from an injected clock and path.
6. output.py: speech, braille, and sound functions using NVDA APIs.
7. settings.py: confspec registration and helpers for paths.
8. gui/settingsPanel.py, gui/historyDialog.py, gui/rulesDialog.py, gui/ruleEditor.py.
9. addon/sounds/: one or two small default wav files, license compatible with GPL.
10. addon/doc/en/readme.md: user guide.
11. tests/: unit tests for rules.py and history.py, run with pytest outside NVDA.

Update buildVars.py: addon_name notificationsController, summary, description, author, version 0.1.0, pythonSources, minimum NVDA version 2026.2 and last tested version 2026.3 (the user runs alpha builds). No compatibility code for older NVDA releases; use current NVDA APIs directly.

capture.py also holds the URL and domain resolver for web content (live regions and browser UIA notifications). Domain extraction and site-scope matching go in rules.py so they can be unit tested.

## 10. Phases and milestones

Phase 1, capture and log only (the install-default behavior):

1. Fill in buildVars.py and the manifest.
2. Global plugin with event_UIA_notification, event_UIA_window_windowOpen, and event_liveRegionChange handlers that build records (including URL and domain for web content) and always call nextHandler.
3. In-memory history plus JSON Lines persistence, retention pruning.
4. Minimal Notification History dialog (list and details, no filters yet).
5. Complete the research items in section 2 with real apps.
6. Deliverable: a build that can be installed and left running to collect real notification samples.

Phase 2, rules and actions:

1. Rule model, rules.json, rule engine with all match types including regex, and site scope matching (domain, subdomains, URL prefix).
2. Output actions: speech only, braille only, both, sound, silent.
3. Rules dialog and rule editor, including Create rule from notification.
4. Unit tests for matching and ordering.

Phase 3, review and polish:

1. History filters, pin, reviewed state, categories, Do not disturb.
2. Review scripts (previous, next, most recent).
3. Settings panel complete, privacy options, clear history.
4. Import and export rules.

Phase 4, release:

1. User documentation and changelog.
2. Translation readiness: every user-facing string wrapped in _() with translator comments.
3. Test on NVDA 2026.2 and 2026.3 alpha, including portable and secure-screen cases (the plugin should do nothing on secure screens: check globalVars.appArgs.secure).
4. Submit to the NVDA add-on store.

## 11. Testing

1. Unit tests (pytest) for rules.py and history.py: match types, case sensitivity, invalid regex, ordering, domain and subdomain matching, URL prefix matching, records with no URL against site-scoped rules, pruning by age and by count, pinned exemption, file corruption recovery (a bad line is skipped, not fatal).
2. A small test generator: a PowerShell or C# helper that raises UIA notification events with chosen text and activityId, plus local HTML test pages that use ariaNotify and aria-live regions (polite, assertive, role status, role alert, a region inside an iframe, and a deliberately abusive page that updates many times per second). Serve them from two different local host names to test site scoping. This allows repeatable manual tests without waiting for real apps.
3. Manual test matrix: Microsoft Teams (new Teams), Chrome, Edge, Firefox (live regions), Outlook, File Explorer copy progress, Windows volume and brightness, Windows toast notifications from any app, Settings app.
4. Check each action with speech only, braille display only (or the braille viewer), and both.
5. Performance check: flood of notifications (for example 50 per second from the generator) must not lag NVDA; disk writes are batched.
6. Confirm that with no rules defined, NVDA output is identical to having the add-on disabled.

## 12. Risks

1. NVDA internal APIs for UIA notifications and toast handling may change between releases. Keep capture code isolated in capture.py and guard with try and except that falls back to calling nextHandler.
2. Toast source-app detection is heuristic. Show the raw data in history so users can still make rules when the resolver guesses wrong.
3. Some apps put changing content (names, counts) in notification text, so starts with or regex matching is needed; activityId matching helps where present.
4. Suppressing a notification means the user may miss something important. History and the review scripts are the safety net, and the default is always pass-through.
5. Getting the URL for a live region event may fail in some browsers or frames. When no URL is available, the record is logged with an empty domain and only unscoped rules can match it, so NVDA behaves normally rather than silencing the wrong site.
6. Logging every live region update from an abusive site can flood history. The per-rule "do not log" option and the entry cap handle this.

## 13. Decisions

1. Minimum NVDA version is 2026.2, last tested 2026.3, with no compatibility code for older releases.
2. ARIA live regions are in scope for version 1, and live region rules are scoped by website domain, with an optional URL prefix.
3. No default keyboard gestures. All commands are available in Input Gestures for users to assign.
4. No starter or default rules for now. A default rules file may be added later if good candidates turn up.
