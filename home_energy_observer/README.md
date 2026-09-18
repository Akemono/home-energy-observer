# Home Energy Observer

Home Assistant app for controlled, supervised deployment of a fixed
Home Energy custom integrations from a separate private Git repository.

Version 0.4.1 manages Financial, Planner, Power Manager and Tesla independently,
with separate installation, confirmation and recovery for each fixed domain.
It adds explicit, idle-only replacement of an unverified installation by a
different verified candidate, without accepting the broken version.

The app source is public so Home Assistant Supervisor can install and update it
without granting the app write access to /addons. Runtime options, GitHub tokens,
private payloads and Home Assistant configuration are not part of this repository.

Security-sensitive deployments are disabled by default. Read DOCS before enabling.
