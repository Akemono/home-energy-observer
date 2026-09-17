# Home Energy Observer App Repository

Public distribution repository for the generic Home Energy Observer app.

No Home Assistant configuration, credentials, private deployment payloads or
Tesla data are stored here. The app reads releases from a separately configured
private GitHub repository using a repository-scoped Contents: read-only token.

## Install

In Home Assistant open **Settings > Apps > App store > Repositories** and add:

`https://github.com/Akemono/home-energy-observer`

Install **Home Energy Observer**, keep deployment disabled initially, and follow
the app documentation. Home Assistant Supervisor manages future app updates.

The app deliberately has no write access to `/addons`. It requests write access
to the Home Assistant configuration directory only for its fixed managed
integration folder, plus the Home Assistant Core API for charging-state checks
and explicit restart requests.
