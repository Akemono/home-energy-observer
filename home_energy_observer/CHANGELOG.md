# Changelog

## 0.6.3

- Make candidate versions in the module overview clickable for manual install or
  replacement of a different pending integration version, using the existing commit-bound
  actions and charging safety gate.

## 0.6.2

- Show pending integrations as needing manual verification after a possible HA
  restart, rather than repeatedly claiming that another restart is required.
- Offer the existing commit-bound replacement action in the overview when a newer
  verified candidate is available alongside pending files.
- Warn prominently when the ingress user identity is unavailable or does not
  authorize deployment actions; keep server-side authorization unchanged.

## 0.6.1

- Redesign the app page as a compact release overview with module status and a
  guided primary action. Keep all existing verified deployment and recovery
  controls in expandable per-module details.
- Show only changed payload digests as new candidates in the overview.

## 0.6.0

- Support schema 3 for the Tesla adapter's fixed live.py module, with minimum
  Observer version validation. No arbitrary additional module paths.
- Preserve schema 1/2 file allowlists and historical recovery compatibility.
- No automatic live activation, charging commands or Home Assistant restart.

## 0.5.1

- Add explicit "Install dashboard while charging (once)" for the verified candidate.
- Reuse the commit-bound install grant, execute immediately and always clear it,
  including failures. Only the charging/unknown gate is bypassed; validation,
  modified-file refusal, atomic installation and recovery remain unchanged.
- No HA restart, charging commands, automatic gate relaxation or integration changes.

## 0.5.0

- Independent dashboard updater in www/home-energy-managed; no changes to manual
  uploads, Lovelace resource storage or dashboard YAML.
- Fixed loader resource reads a fresh local version manifest on each full page load
  and imports a content-addressed card. No repeated uploads or resource URL changes.
- Dashboard updates default paused; Resume enables automatic installs while idle.
  Atomic directory exchange, checksums, fixed allowlist, modified-file refusal,
  journal recovery and three previous versions. Rollback pauses updates.
- File verification is not frontend health acceptance. No HA restart or manual
  integration-style confirmation for dashboard files; integrations keep all gates.

## 0.4.1

- Explicit replacement of an unverified installation by a verified candidate.
- Bind repair to module, pending commit and candidate commit; require idle charging
  state without override. Never automatically replace pending installations.
- Reuse journal recovery and file checks, preserving confirmed history and requiring
  restart plus human verification of the replacement.

## 0.4.0

- Independent deployment panels for Financial, Planner, Power Manager and Tesla.
- Fixed domain-specific allowlists and paths; separate journals, overrides and recovery.
- Keep historic Tesla deployment state and schema-1 rollback; schema-2 releases
  require 0.4.0 and are rejected by older installers.
- Pin every module in a poll to one Git commit; no suite-wide atomic upgrade claim.
- Block HA restart until all interrupted module transactions are recovered.
- Preserve charging gate, admin/CSRF checks, no self-updating app and explicit acceptance.

## 0.3.6

- Stop treating Home Assistant state timestamps as device heartbeats for push integrations.
- Fail closed on unavailable, unknown, malformed, non-finite or restored entity states.
- Continue to block on contactor ON or dedicated EVSE power above the configured idle limit.

## 0.3.5

- Require freshness from the dedicated Shelly EVSE power measurement rather than an unchanged contactor.
- Continue to block whenever the readable contactor is ON, even if its report timestamp is old.
- Allow a readable OFF contactor with fresh Shelly power at or below the configured idle limit.

## 0.3.4

- Remove the retired sandbox release, sandbox recovery and sandbox pause controls from the Web UI.
- Stop polling or updating the legacy sandbox at runtime; its stored data is left untouched.
- Remove the ten-second full-page auto refresh that caused visible flicker.
- Keep one deployment-focused Check now control and show the last charging-gate diagnostic.

## 0.3.3

- Remove the unused legacy charging current option from configuration, schema and translations.
- Keep the dedicated Shelly active-power gate introduced in 0.3.2 unchanged.

## 0.3.2

- Replace unreliable Wall Connector vehicle-current gating with dedicated EVSE total active power.
- Treat a closed contactor or absolute power above the configurable idle limit as charging.
- Stop using the legacy current option for deployment decisions.
- Clarify that one-time force install/restart controls bypass only the charging gate.

## 0.3.1

- Publishable Home Assistant App Repository package.
- Remove personal repository and Wall Connector entity defaults from public code.
- Make the local charging contactor and current entity IDs configurable.

## 0.3.0

- Opt-in, supervised deployment of the home_energy_tesla integration from Git.
- New explicitly approved HA configuration read/write mount and Core API access.
- Default disabled; per-user Ingress authorization and CSRF for deployment controls.
- Local charging/unknown-state gate, commit-bound ten-minute single-use overrides.
- Explicit HA restart and manual acceptance; three confirmed recovery snapshots.
- Allowlisted files, checksums, compile-only Python validation, ownership checks.
- Journalled atomic directory exchange with guarded interrupted-deployment recovery.
- Existing dry-run sandbox retained separately; no Tesla commands added.
- Native HA/Linux acceptance testing is still required.

## 0.2.0

- Install harmless JSON releases in app-owned storage only.
- Keep three prior successful releases and restore the previous release.
- Persist pause after rollback so polling does not undo recovery.
- Add Check now, Pause and Resume controls behind Ingress and CSRF protection.
- Show app version, active release, recovery history and status in a styled English UI.
- Validate saved snapshots on restart and stop installation if corrupted.
- No Home Assistant configuration access or charging commands.

## 0.1.0

- Read-only GitHub polling and dry-run checksum verification.
