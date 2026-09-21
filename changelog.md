# Changelog

## 0.1.0 (in development)

1. Logs UI Automation notifications, Windows toasts, web page live regions (including Chrome and Firefox) and pop-up alerts, such as apps' own notification pop-ups, to a history, without changing what NVDA reports.
2. Rules by app, kind, website (domain, subdomains, URL prefix), text (starts with, contains, is exactly, regular expression), activity ID and live region politeness.
3. Rule actions: NVDA default, speech and braille, speech only, braille only, or silent, with an optional built-in or custom sound, a category, and whether to log. A rule can also remove the text it matched and report the rest.
4. Notification history window with filters, details, pinning, reviewed state, and creating rules from a notification.
5. Rules window with ordering, enable and disable, import and export.
6. Do not disturb, reporting only important notifications.
7. Automatic deletion of old history, with a configurable retention period and entry limit.
8. Commands, with no keys assigned, to open the windows, review history without opening it, create a rule, and toggle Do not disturb.
