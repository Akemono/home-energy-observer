# Changelog

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
