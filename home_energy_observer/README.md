# Home Energy Observer

Home Assistant app for controlled, supervised deployment of a fixed
Home Energy custom-integration bundle from a separate private Git repository.

The app source is public so Home Assistant Supervisor can install and update it
without granting the app write access to /addons. Runtime options, GitHub tokens,
private payloads and Home Assistant configuration are not part of this repository.

Security-sensitive deployments are disabled by default. Read DOCS before enabling.
