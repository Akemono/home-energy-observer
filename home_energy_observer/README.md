# Home Energy Observer

Home Assistant app for controlled, supervised deployment of a fixed
Home Energy custom integrations from a separate private Git repository.

Version 0.5.0 manages Financial, Planner, Power Manager and Tesla independently,
with separate installation, confirmation and recovery for each fixed domain.
It adds an independent initially-paused static dashboard updater with a stable
local loader URL, automatic file verification, idle-only installation and rollback.
No repeated manual upload or resource-version edit after one-time setup. Dashboard
YAML and manually uploaded files remain untouched. Refresh the browser to use an
updated dashboard; there is no automatic frontend health claim or hot swap.

The app source is public so Home Assistant Supervisor can install and update it
without granting the app write access to /addons. Runtime options, GitHub tokens,
private payloads and Home Assistant configuration are not part of this repository.

Security-sensitive deployments are disabled by default. Read DOCS before enabling.
