# Home Energy Observer

Home Assistant app for controlled, supervised deployment of a fixed
Home Energy custom integrations from a separate private Git repository.

Version 0.6.2 labels pending installations as awaiting manual verification and
shows a newer verified candidate alongside the pending version. A temporary
missing Ingress user ID is called out where the deployment actions are shown.

Version 0.6.1 presents the five managed parts in a compact release overview, with
the existing module actions available under expandable details. It keeps the
installation, restart, confirmation and recovery workflow explicit.

Version 0.6.0 manages Financial, Planner, Power Manager and Tesla independently,
with separate installation, confirmation and recovery for each fixed domain.
Schema 3 adds a fixed live.py allowlist entry for the Tesla adapter, requiring
Observer 0.6.0. Existing schema 1/2 releases and recovery history remain supported.
Installing integration files never activates their optional live trial.
It adds an independent initially-paused static dashboard updater with a stable
local loader URL, automatic file verification, idle-only installation and rollback.
No repeated manual upload or resource-version edit after one-time setup. Dashboard
YAML and manually uploaded files remain untouched. Refresh the browser to use an
updated dashboard; there is no automatic frontend health claim or hot swap.
An explicit one-time dashboard install can bypass only the charging gate for its
verified candidate, without restarting HA. Automatic installs still require idle.

The app source is public so Home Assistant Supervisor can install and update it
without granting the app write access to /addons. Runtime options, GitHub tokens,
private payloads and Home Assistant configuration are not part of this repository.

Security-sensitive deployments are disabled by default. Read DOCS before enabling.
