# Home Energy Observer 0.6.0

Schema 3 Tesla payloads require Observer 0.6.0 and allow the additional fixed
live.py module. Upgrade this app before installing such a candidate. Older
schema 1/2 payloads and rollback history retain their original file allowlists.
Installation does not activate optional live control; integration acceptance
and any live trial are separate user actions. No automatic Home Assistant restart.

Four fixed domains are supported: home_energy_financial, home_energy_planner,
home_energy_power and home_energy_tesla. Each has its own installation panel,
allowlist, state, three-version recovery history and acceptance. See the private
project's ARCHITECTURE.md for the integration migration. No private inputs are
needed in this generic Observer source.

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
2. Leave enable_deployment OFF initially. Open Web UI and confirm App 0.4.0.
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

For the modular migration, install Financial, Planner, Power Manager, then Tesla
using their separate panels before the explicit HA restart. Add the three central
integrations in Devices & services; keep an existing Tesla entry. Verify and confirm
each module separately. These are independent transactions, not a suite-wide atomic
exchange. Recover every interrupted module before requesting a restart. Never force
through unexpected/mismatched files. Existing Tesla schema-1 deployments still work.

After a candidate has been published on production:
1. Make sure the car is not charging. Check now, then Install verified candidate.
2. Expect Pending verification, NOT Confirmed. This means files are on disk only.
3. Restart Home Assistant is an explicit button and rechecks the charging gate.
   Restart interrupts ALL HA automations, including software load protection.
   A timeout can mean the restart happened; check HA before trying again.
4. For the FIRST installation only, open Settings > Devices & services >
   Add Integration for the installed modules. Later file updates reuse those entries.
   Configure Financial, Planner and Power Manager before Tesla Shadow.
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
managed integration folder already exists, or managed files were edited, installation
is blocked rather than overwriting user changes. Ask for a migration/reconciliation.

## Charging gate and overrides

The gate uses the local Wall Connector contactor and dedicated Shelly total active
power, not cloud charging state, connected state or the Wall Connector's phantom
vehicle-current reading. Connected but idle is allowed. The Wall Connector and
Shelly integrations are push-based and may leave last_reported unchanged while a
healthy value remains constant, so HA timestamps are not treated as heartbeats.
A readable contactor ON always counts as charging. A readable OFF contactor is
accepted when the Shelly absolute power is at or below charging_idle_power_watts.
The default 50 W limit is well below a 1 A charge and may never be configured above
100 W. Unknown, unavailable, malformed, non-finite or restored states, missing token
and API/network failures block installation and restart. HA integration availability
is relied upon to mark a disconnected device unavailable; this is not a hardware
safety interlock.

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
in /data/deployment/deployment-state.json for Tesla, and
/data/deployment/<domain>/deployment-state.json for each central integration.
Pending/failed candidates do not occupy
a successful-version slot. This is separate from the original JSON sandbox history.

Restore previous integration files restores the last confirmed version when a
candidate is pending, or the preceding recovery version otherwise. It obeys the
same charging gate, pauses deployments and requires restart plus acceptance.
There is no managed previous version on the FIRST install: use your HA backup
if first-install removal/recovery is needed. Files cannot roll back the effects
that arbitrary Python already had on HA or external devices.

Updates stage only the fixed allowlisted files in
/homeassistant/custom_components/.<domain>.stage for the selected fixed domain. Existing folders are
swapped using Linux renameat2(RENAME_EXCHANGE); no unsafe two-rename fallback.
First installation uses a single directory rename. A persistent journal tracks
the intended old/new snapshots. After interruption, automatic deployment stops.

Recover interrupted deployment reconciles a known target with the journal. It
never swaps the live target. It can discard a partial stage only when every
remaining file is a prefix of the journal's expected content; symlinks and unknown
files block cleanup. Do not delete the journal to bypass a problem. If recovery
reports unexpected content, preserve the app data and restore your HA backup.
Removing old staging copies does not remove the retained recovery snapshots.

Only the four listed domain folders and their fixed staging siblings are managed.
configuration.yaml, automations.yaml, scripts.yaml, helpers, secrets and other
integrations are not modified by the installer. Path checks are not protection
against another privileged process racing to change those paths.

## Validation and remaining limitations

Validation checks commit pinning, whole-payload SHA256, fixed manifest/path/domain,
an exact filename allowlist, Python compilation without execution and JSON
metadata. It is NOT a Home Assistant runtime/import check, code-security audit,
electrical-safety proof or automatic activation/health verification.

Schema 2 requires this 0.4.0 installer and is rejected by older apps. Schema 1
remains accepted for historical Tesla recovery only. Do not downgrade the app to
0.3.x once schema-2 records exist: keep 0.4.0 to roll back individual integrations.
No request can supply an arbitrary domain, manifest path, payload path or filename.
One poll pins every module to the same commit. Overrides are bound to domain,
commit, operation and one use. A restart is blocked while ANY module has a journal.

Local tests run on Windows with a test double for the Linux directory exchange.
There is no local Docker/HA Core test environment. Native Linux filesystem
exchange, HA Ingress identity, API restart and first integration setup still need
acceptance testing on the VM. fsync/atomic rename do not prove every power-loss,
hardware/storage or filesystem failure recoverable.

## Maintainer publishing

The app code is updated manually/rebuilt separately; never loaded from a payload.
tools/build_deployment.py prints an apply_patch patch for deterministic
deployment.json, deployments/<domain>.json and the four fixed release bundles.
Apply that patch (or use --write for deterministic generated files), then run
python tools/build_deployment.py --check and python -m unittest discover -s tests -v.
Publish the tested commit to production only when the user is ready.
The existing release.json/probe.json dry-run path remains backward compatible.

References:
- https://developers.home-assistant.io/docs/apps/configuration/
- https://developers.home-assistant.io/docs/apps/communication/
- https://developers.home-assistant.io/docs/api/rest/
# Repairing an unverified installation (0.4.1)

If a broken installation is still Pending verification, do not confirm it.
Use Check now, then Replace unverified installation in the affected module.
The button appears only for a candidate with different verified files.
This explicit action requires idle charging status and cannot use a force override.
It checks both displayed commits, preserves confirmed recovery history, and uses
the normal durable deployment journal. Pending installations are never replaced
automatically. After all intended repairs are pending, restart HA once, verify
the integrations, and only then confirm them. Interrupted repairs use Recover
interrupted deployment; never delete deployment state or staging files manually.
# Dashboard updates (Observer 0.5.0)

Observer 0.5.1 additionally offers **Install dashboard while charging (once)**.
This explicitly installs the displayed verified candidate immediately, even when
charging is active or unknown. The commit-bound permission is cleared after the
attempt, including errors. It never disables automatic charging gates or integrity
checks, and never restarts HA or sends a charging command. Rollback still requires idle.

Update the Observer app once through Home Assistant. Existing integrations and
their confirmation/restart workflow are unchanged. There is a separate **Dashboard
updates** panel, initially paused, using the same configured release branch and
administrator/CSRF protection.

1. Choose **Check now**, then **Resume dashboard updates** in that panel. Installation
   waits for confirmed idle charging; unknown/charging never bypasses this gate.
2. Wait for **Installed files** to show a dashboard version.
3. In HA **Settings > Dashboards > Resources**, replace the old dashboard resource
   (do not add a second copy) with `/local/home-energy-managed/loader.js`, type
   **JavaScript module**. Keep the existing dashboard YAML and refresh the page.

After this one-time setup, published dashboard bundles are installed automatically
while updates remain enabled. A normal full browser-page refresh loads the new
content-addressed version. Merely changing HA tabs is not a full reload. Open
sessions are not forcibly reloaded and active controls are not hot-swapped.

No dashboard files or tokens are downloaded directly from GitHub by the browser.
Observer uses its existing repository credentials; the browser only loads local
static files. As with any HA www content, those code files are publicly readable;
never include secrets in release JavaScript. Repository code is trusted, not sandboxed.

The only managed static directory is `/homeassistant/www/home-energy-managed`.
Existing `/www/community/home-energy` uploads and `.storage` remain untouched.
Unexpected/modified files block updates, rather than being adopted or overwritten.
**Restore previous dashboard and pause updates** restores a verified prior bundle;
refresh the browser afterwards. Three prior versions are kept. No app-triggered HA
restart is provided for dashboard updates. The optional one-time install override
is available from 0.5.1. **Recover interrupted
dashboard update** preserves a provable previous/new transaction; ambiguous files
remain untouched. After interrupted recovery updates are paused until resumed.

"Installed" refers to verified files on disk, not tested UI health. Check the dashboard
after a release and use rollback if needed. There is no automatic JavaScript health
judgment or rollback based on browser errors. Never downgrade Observer below 0.5.0
while expecting dashboard updates to be managed.
