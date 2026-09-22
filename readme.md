# Notifications Controller

Notifications Controller is an NVDA add-on that lets you control and review the notifications that Windows, apps and web pages send to NVDA.

Apps and web pages send NVDA a lot of announcements. Some are useful, some are noise, and some are useful but easy to miss. With this add-on you can:

1. Keep a history of every notification, to review later.
2. Write rules, by app, website and text, that decide whether a notification is spoken, brailled, played as a sound, or silenced.
3. Put notifications into categories, such as important, informational and spam.
4. Turn on Do not disturb, so only important notifications are reported.

Out of the box the add-on changes nothing about what NVDA says. It only logs notifications, so you can see what arrives before you write any rules.

Requires NVDA 2026.2 or later.

## What counts as a notification

The add-on handles four kinds:

1. App notifications (UI Automation notifications). Many Windows apps send these: Microsoft Teams, Office, Settings, Calculator, File Explorer, and web browsers for some web page announcements.
2. Windows toasts: the popups in the corner of the screen. A toast's text is its title and message, such as "Lunch, Anyone free at noon?", without the "New notification from" and "1 of 1" that Windows adds, so rules can match the start of the message. The app that sent it is shown as its app, and the full text as spoken is in the details.
3. Web page live regions: parts of a web page marked to announce changes, such as chat messages, status messages and alerts. Some sites use these well and some overuse them badly, so rules for live regions are usually limited to one website.
4. Pop-up alerts: objects marked as alerts, such as an app's own notification pop-up, a web page alert, or a UI Automation system alert. NVDA reports these even from apps in the background when the pop-up is a topmost window. Apps such as Microsoft Teams can show their own pop-ups instead of Windows toasts. When an alert has no name of its own, its text is gathered from the text inside it.

You can turn handling of each kind on or off in the add-on's settings.

## Getting started

1. Install the add-on and restart NVDA.
2. Use your computer as usual for a while. The add-on quietly logs notifications.
3. Open the history: NVDA menu, Tools, Notifications Controller, Notification history.
4. Find a notification you want to change, and press the Create rule button.
5. In the rule editor, choose what should happen, such as "No speech or braille" with a sound, and press OK.

## Notification history

The history window lists notifications in time order, with the newest at the bottom. When it opens, the newest is selected. Notifications that arrive while it is open are added at the bottom without moving you.

For each notification the list shows its text, app, time, website, category and what was done with it. The Details box below the list shows everything known about the selected notification, including the page address for web content, the rule that matched, and technical details that help when writing rules.

Filters at the top narrow the list by app, website, category and kind, by text search, and to unreviewed notifications only. Selecting a notification marks it as reviewed, except when the unreviewed filter is on.

Keys in the list:

1. Enter reads the selected notification.
2. Delete deletes it.
3. Control+C copies its details.
4. F5 refreshes the list.

Buttons:

1. Create rule: opens the rule editor, filled in to match notifications like the selected one.
2. Silence this site's live regions: adds a rule that silences every live region on the selected notification's website. It asks in the same step whether they should still be logged to history: "Silence and keep logging" lets you review them later, "Silence and don't log" keeps them out of history too, and Cancel adds no rule. Available for live region notifications from a website.
3. Pin or Unpin: pinned notifications are never deleted automatically.
4. Copy: copies the details.
5. Delete, and Delete all shown.
6. Mark all shown as reviewed.
7. Rules: opens the rules window.

## Rules

A rule says which notifications it matches and what to do with them. Rules are checked from the top of the list down, and the first enabled rule that matches is used. A notification no rule matches is left to NVDA as usual.

Open the rules window from the NVDA menu, Tools, Notifications Controller, Rules. In the list, Enter edits a rule, Space turns it on or off, Delete deletes it, and Alt+Up and Alt+Down move it. New rules are added above the selected rule, or at the top when created from the history. You can import and export rules as JSON files to share them or move them to another computer. Changes are saved as soon as you make them.

### What a rule matches

Every field you fill in must match. Empty fields match anything.

1. App: the app that sent the notification. You can use NVDA's name for the app, such as ms-teams, chrome or msedge, or the product name, such as Microsoft Teams. The list offers names seen in the history. For toasts, use the name of the app that sent the toast.
2. Kind of notification: app notification, Windows toast, web live region, pop-up alert, or any.
3. Website: for web content, the site's domain, such as example.com. With Include subdomains checked, it also matches www.example.com, app.example.com and so on. You can type a full address and the domain is taken from it.
4. URL starts with: for sites where only one part misbehaves, the start of the page address, such as https://example.com/app/.
5. Text: how the notification's text is matched, and the Pattern to match.
	1. Starts with, the default, and the most common choice.
	2. Contains.
	3. Is exactly.
	4. Regular expression: a Python regular expression, searched anywhere in the text. A pattern that takes too long on a notification (more than a tenth of a second) turns its rule off until the rules change, and that notification is left to NVDA as usual, so a slow pattern cannot freeze NVDA. All regular expressions together get at most 0.15 seconds per notification, however many rules there are. The rule is marked with an error in the rules list, and the NVDA log says which rule it was. The Test against history button warns about slow patterns before you save.
	5. Any text.

	Matching ignores differences in spaces and line breaks, and ignores case unless Case sensitive is checked.
6. Activity ID: some apps label each kind of notification with a fixed identifier, shown in the history details. Matching on it is more reliable than matching the text, which can change.
7. Live region politeness: polite or assertive, for live regions only.

### What a rule does

1. Speech and braille:
	1. NVDA default: NVDA reports it as usual. Use this with a sound to add a sound, or just to give the notification a category.
	2. Speech and braille.
	3. Speech only.
	4. Braille only.
	5. No speech or braille.
2. Remove the matched text and report the rest: instead of the whole notification, report what is left once the text the rule matched is removed. Use it for notifications that carry useful text plus something you do not want to hear, such as instructions a chat app adds to every message. Starts with removes the start of the text; contains and regular expressions remove every match; is exactly leaves nothing. Leftover commas and spaces are tidied, and if nothing meaningful is left, nothing is reported (a sound still plays, if the rule has one). The rest is reported with the speech and braille choice above; with NVDA default it is spoken and brailled, since NVDA cannot report changed text itself. The history keeps the original text and shows what was reported, and the Test against history button shows what each matching notification would become.
3. Sound: no sound, one of the built-in sounds (chime, blip, alert), or your own wave file. The sound plays before any speech. With "No speech or braille", the sound plays instead of the notification.
4. Category: important, informational, spam, or type your own.
5. Log to history: uncheck it for notifications you never want to see, such as a very noisy site.

The Test against history button shows which notifications in the history the rule would match, and says how many.

A rule can bring back notifications NVDA would not normally report. NVDA ignores app notifications from apps in the background; a rule with speech or braille for them reports them anyway. The history details say when a notification came from a background app.

A few things still win over rules:

1. When NVDA's "Report dynamic content changes" is off (NVDA+5), live regions stay silent whatever the rules say, so that quick toggle keeps working.
2. Do not disturb.
3. Sleep mode for an app.

## Do not disturb

When Do not disturb is on, only notifications in the important category are reported. Everything else is still logged, so you can catch up afterwards in the history. Turn it on or off in the settings or with a command you assign.

## Commands

No keys are assigned by default. To assign them, open the NVDA menu, Preferences, Input gestures, and look in the Notifications Controller category. The commands are:

1. Open the notification history.
2. Open the notification rules.
3. Report the most recent notification.
4. Report the previous (older) notification in history.
5. Report the next (newer) notification in history.
6. Create a rule from the notification last reported by the review commands, or from the most recent one.
7. Turn Do not disturb on or off.
8. Turn Notifications Controller on or off.

The previous and next commands let you step through the history without opening the window. They start from the newest notification, and mark each one they read as reviewed.

## Settings

Open the NVDA menu, Preferences, Settings, Notifications Controller, or use Tools, Notifications Controller, Settings.

The first settings, and the choice of what to handle, follow NVDA's configuration profiles, so a profile can, for example, turn on Do not disturb. The history storage settings (items 4 to 8) are shared by all profiles, because they describe the one history file: a profile switch never starts or stops saving notifications to disk, and never deletes history.

1. Use rules for notifications: when off, NVDA handles every notification as usual and nothing is logged.
2. Do not disturb.
3. Log notifications to history.
4. Keep history after NVDA restarts. When on, history is saved as plain text in your NVDA settings folder. Notifications can contain private messages, so turn this off if you do not want them saved to disk. Turning it off deletes the saved history file; the history then lasts until NVDA exits. Turning it back on keeps what is in memory and adds it to any history already in the file.
5. Delete notifications older than: from 1 hour to 30 days, or keep them until the entry limit is reached. The default is 1 day.
6. Maximum notifications to keep: the oldest are deleted beyond this. The default is 5000.
7. Never automatically delete important notifications.
8. Log a repeated notification only once within a number of milliseconds. Some apps and browsers send the same notification twice in a row. The default is 500.
9. Clear history.
10. Handle app notifications, Windows toasts, web page live regions, and pop-up alerts.

## Where files are kept

In your NVDA settings folder, in a folder named notificationsController:

1. rules.json: your rules. If it cannot be read, it is renamed rules.json.damaged and the add-on starts with no rules.
2. history.jsonl: the history, one notification per line, when history is kept after restarts.
3. settings.json: the history storage settings, shared by all configuration profiles. If it cannot be read, the add-on does not save history to disk until you save its settings again: the file might have said not to. A damaged file is copied to settings.json.damaged. The settings show a warning for anything that went wrong, saying what is happening now and what may change: settings that could not be read or saved, a history file that could not be read (history is then kept in memory only and the file is left untouched), or a history file that could not be deleted when you turned saving off (its location is given, so you can delete it yourself). Saving the settings again tries once more.

## Known limits

1. For toasts, the sending app is worked out from the toast's contents, which is not always possible. The history details show the toast's structure to help.
2. For live regions in Chrome and Firefox, NVDA does not say which tab a report came from. The add-on uses the page with focus, or the browser's open page when all its pages are on one site. When it cannot tell, the website is left empty, and only rules without a website can match.
3. Duplicate detection compares each notification only with the one just before it, ignoring differences in spaces and line breaks.

## Development

The add-on code is in addon/globalPlugins/notificationsController. The rule engine (rules.py, policy.py), the history store (history.py) and the data types (models.py) do not use NVDA, and have unit tests:

	python -m unittest discover -s . -t .

Smoke tests load the whole add-on inside NVDA's own unit test environment, including its dialogs and the live region hook. They need an NVDA source checkout with its helper DLLs built (C:\code\nvda by default, or set NVDA_SOURCE_DIR):

	C:\code\nvda\.venv\Scripts\python.exe tests\nvda\smokeNvda.py

To build the add-on:

	uvx --from scons --with markdown scons

Test tools:

1. tools\notificationTester.ps1 sends UI Automation notifications and toasts on demand, with an optional delay to test background apps. Run it with Windows PowerShell: powershell -ExecutionPolicy Bypass -File tools\notificationTester.ps1
2. tools\testPages\liveRegions.html has buttons for polite, assertive, status and alert live regions, ariaNotify, a region in a frame, and a noisy region. Serve it from two host names, such as localhost and 127.0.0.1, to test website rules.
3. tools\makeSounds.py regenerates the built-in sounds.

See PLAN.md for the design.

## License

Copyright (C) 2026 Travis Roth. Distributed under the GNU General Public License, version 2. See COPYING.txt.
