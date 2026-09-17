# Home Energy Observer 0.3.5

## Recommended installation from the app repository

Add https://github.com/Akemono/home-energy-observer under
Settings > Apps > App store > Repositories, then install Home Energy Observer.
Updates are subsequently offered by Home Assistant; this app does not need
write access to /addons and cannot replace its own container.

Configure repository as the owner/name of the private deployment repository,
branch as its release branch and github_token as a repository-scoped,
fine-grained Contents: read-only token. Also configure charging_contactor_entity
with the Wall Connector contactor and charging_power_entity with the Shelly
Total active power sensor that measures only the Wall Connector. The default
charging_idle_power_watts is 50 W and is constrained to 1–100 W. Never publish
or send this configuration or token.

## One-time upgrade and permissions

Make a Home Assistant backup first. Keep Studio Code Server available for recovery.
For migration from the older Local App only, extract the matching ZIP and replace the files INSIDE
/addons/home_energy_observer. Keep the existing directory/slug; do not nest it
and do not uninstall the app (uninstall can delete stored state and credentials).

Open Settings > Apps, the app store, Check for updates, then Home Energy Observer
> Update. If no Update is offered after refreshing local sources, use Rebuild.
A simple Restart does not rebuild the app image.

This update explicitly requests:
- homeassistant_config read/write, mounted as /homeassistant inside this app
  (the same configuration is /config inside HA/Studio Code Server).
- homeassistant_api: true, to read local charging state and request an explicit
  Home Assistant restart.

No hassio_api, host network, privileged, Docker access or full_access is requested.
Keep Protection mode enabled. Do not disable AppArmor to work around an error.

IMPORTANT: HA grants access to the ENTIRE configuration directory and a broad Core
API, not just one folder or two calls. Our software restricts its own operations;
this is NOT an OS-enforced single-folder sandbox. Downloaded Python runs inside
HA Core on the next restart. The trusted Git production branch is therefore an
execution trust boundary. A checksum detects corruption, not a malicious publisher.
Keep GitHub Contents: read-only credentials repository-scoped; never send tokens
in chat. The app does not send configuration files or HA tokens to GitHub.

## Start safely

1. Keep github_token, repository, branch=production and interval_seconds unchanged.
2. Leave enable_deployment OFF initially. Open Web UI and confirm App 0.3.5.
   Legacy sandbox state, if present, remains inert in /data/sandbox and is not displayed or polled.
3. While logged into HA as your administrator, copy your Ingress user ID displayed
   at the bottom of this app. In the app Configuration set deployment_admin_user_id
   to that 32-character ID. This is an identifier, not a password.
4. Set enable_deployment ON, save Configuration and restart the app if requested.
   This does not restart HA. Automatic integration deployments initially remain Paused.
   An empty administrator ID also disables deployment.
5. Check now retrieves the production deployment candidate. If production has not
   been published yet, Deployment unavailable (HTTP 404) is expected. Do not
   change the branch to main to work around it.

Only the configured user's trusted Ingress identity can use deployment controls;
all such POST actions also require CSRF protection. panel_admin keeps the sidebar
admin-only. The app does not re-query HA roles: configure only your administrator's
ID and clear it if that user's authority is revoked. No identity header means
deployment actions are denied; do not weaken the check.

## First managed integration installation

After a candidate has been published on production:
1. Make sure the car is not charging. Check now, then Install verified candidate.
2. Expect Pending verification, NOT Confirmed. This means files are on disk only.
3. Restart Home Assistant is an explicit button and rechecks the charging gate.
   Restart interrupts ALL HA automations, including software load protection.
   A timeout can mean the restart happened; check HA before trying again.
4. For the FIRST installation only, open Settings > Devices & services >
   Add Integration > Home Energy Tesla Shadow. Later file updates reuse that entry.
5. Verify Tesla shadow decision and Tesla automatic control appear and that the
   switch is OFF after restart. This switch controls only the shadow adviser:
   it does NOT disable the existing YAML automations and is NOT the emergency
   manual-charging override yet. No charging commands are added.
6. Return to the app and click I verified the integration works — confirm installation.
   This is your explicit acceptance, not an automatic health check.
7. Resume deployments is optional. Future candidates can install automatically
   when idle, but every installation pauses again for explicit restart/acceptance.
   This initial release is supervised deployment, not unattended activation.

Do not manually copy integration files alongside this installer. If an unmanaged
home_energy_tesla folder already exists, or managed files were edited, installation
is blocked rather than overwriting user changes. Ask for a migration/reconciliation.

## Charging gate and overrides

The gate uses the local Wall Connector contactor and dedicated Shelly total active
power, not cloud charging state, connected state or the Wall Connector's phantom
vehicle-current reading. Connected but idle is allowed. The dedicated Shelly power
reading must have a timezone-aware last_reported within 120 seconds. A readable
contactor ON always counts as charging, even when its unchanged report is old.
A readable OFF contactor is accepted when the Shelly reading is fresh and its
absolute power is at or below charging_idle_power_watts. The default 50 W limit is
well below a 1 A charge and may never be configured above 100 W. Unknown or invalid
contactor states, unavailable/invalid/stale Shelly power, missing token and API or
network failures block installation and restart.

Check now refreshes the read-only charging-gate diagnostic even while deployments
are paused or an installation is awaiting confirmation.

Last reported freshness is not proof of sensor correctness. There is no atomic
lock between reading charging status and changing files/restarting HA. The app
does NOT stop the charger or hold its state. Avoid starting charging during an
installation. Overrides cannot guarantee uninterrupted charging or protection.

Force install despite charging arms an override for one install attempt of
the exact commit, valid for ten minutes. Then click Install verified candidate.
Rollback and Force restart each have a SEPARATE one-time override: installation consent
must not silently authorize a later HA restart. Overrides live only in memory,
expire, are consumed by an attempt, and never bypass file validation/ownership.
Pause deployments clears an armed override. They do not disable load protection;
a deliberate HA restart itself interrupts it. Use only with independent safe
electrical limits; this prototype is not a safety device.

## Recovery

Three previously CONFIRMED integration versions are stored with full file contents
in /data/deployment/deployment-state.json. Pending/failed candidates do not occupy
a successful-version slot. This is separate from the original JSON sandbox history.

Restore previous integration files restores the last confirmed version when a
candidate is pending, or the preceding recovery version otherwise. It obeys the
same charging gate, pauses deployments and requires restart plus acceptance.
There is no managed previous version on the FIRST install: use your HA backup
if first-install removal/recovery is needed. Files cannot roll back the effects
that arbitrary Python already had on HA or external devices.

Updates stage only the fixed allowlisted files in
/homeassistant/custom_components/.home_energy_tesla.stage. Existing folders are
swapped using Linux renameat2(RENAME_EXCHANGE); no unsafe two-rename fallback.
First installation uses a single directory rename. A persistent journal tracks
the intended old/new snapshots. After interruption, automatic deployment stops.

Recover interrupted deployment reconciles a known target with the journal. It
never swaps the live target. It can discard a partial stage only when every
remaining file is a prefix of the journal's expected content; symlinks and unknown
files block cleanup. Do not delete the journal to bypass a problem. If recovery
reports unexpected content, preserve the app data and restore your HA backup.
Removing old staging copies does not remove the retained recovery snapshots.

Only the home_energy_tesla folder and its fixed staging sibling are managed.
configuration.yaml, automations.yaml, scripts.yaml, helpers, secrets and other
integrations are not modified by the installer. Path checks are not protection
against another privileged process racing to change those paths.

## Validation and remaining limitations

Validation checks commit pinning, whole-payload SHA256, fixed manifest/path/domain,
an exact filename allowlist, Python compilation without execution and JSON
metadata. It is NOT a Home Assistant runtime/import check, code-security audit,
electrical-safety proof or automatic activation/health verification.

Local tests run on Windows with a test double for the Linux directory exchange.
There is no local Docker/HA Core test environment. Native Linux filesystem
exchange, HA Ingress identity, API restart and first integration setup still need
acceptance testing on the VM. fsync/atomic rename do not prove every power-loss,
hardware/storage or filesystem failure recoverable.

## Maintainer publishing

The app code is updated manually/rebuilt separately; never loaded from a payload.
tools/build_deployment.py prints an apply_patch patch for deterministic
deployment.json and releases/tesla-shadow.json. Apply that patch, then run
python tools/build_deployment.py --check and python -m unittest discover -s tests -v.
Publish the tested commit to production only when the user is ready.
The existing release.json/probe.json dry-run path remains backward compatible.

References:
- https://developers.home-assistant.io/docs/apps/configuration/
- https://developers.home-assistant.io/docs/apps/communication/
- https://developers.home-assistant.io/docs/api/rest/
