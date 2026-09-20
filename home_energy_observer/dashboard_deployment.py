"""Independent, content-addressed local dashboard deployment; no HA storage edits."""
import hashlib
import html
import json
from pathlib import Path
import re

from deployment import Blocked, Installer, DASHBOARD, MAX_SIZE, validate


def loader_source():
    return Path(__file__).with_name("dashboard-loader.js").read_text(encoding="utf-8")


def validate_dashboard(record):
    manifest, payload = record["manifest"], record["payload"]
    if (manifest.get("schema_version") != 1 or type(manifest.get("schema_version")) is not int
            or manifest.get("kind") != "ha-dashboard" or manifest.get("domain") != DASHBOARD
            or manifest.get("path") != "releases/home_energy_dashboard.json"
            or manifest.get("min_observer_version") != "0.5.0"
            or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", str(manifest.get("version", "")))):
        raise Blocked("Dashboard manifest rejected")
    if not isinstance(payload, str) or len(payload.encode()) > MAX_SIZE:
        raise Blocked("Dashboard size rejected")
    if hashlib.sha256(payload.encode()).hexdigest() != manifest.get("sha256"):
        raise Blocked("Dashboard checksum rejected")
    files = json.loads(payload)
    if not isinstance(files, dict) or any(not isinstance(v, str) or "\x00" in v for v in files.values()):
        raise Blocked("Dashboard content rejected")
    info = json.loads(files.get("version.json", "{}"))
    digest = info.get("sha256", "") if isinstance(info, dict) else ""
    if not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest):
        raise Blocked("Dashboard file hash rejected")
    entry = "card-" + digest + ".js"
    if (type(info.get("schema")) is not int or set(files) != {"loader.js", "version.json", entry}
            or info != {"schema": 1, "version": manifest["version"], "sha256": digest, "entry": entry}
            or files["loader.js"] != loader_source()
            or hashlib.sha256(files[entry].encode()).hexdigest() != digest):
        raise Blocked("Dashboard file allowlist or hash rejected")
    # JavaScript is trusted repository code, never executed by the installer.
    return files


class DashboardInstaller(Installer):
    def __init__(self, directory, config_root, ha, enabled=False):
        super().__init__(directory, config_root, ha, enabled, DASHBOARD)
        # New owned directory; never overwrite the user's manually uploaded resource.
        self.parent = self.root / "www"
        self.target = self.parent / "home-energy-managed"
        self.stage = self.parent / ".home-energy-managed.stage"

    def matches(self, path, record, partial=False):
        self.paths()
        if path.is_dir() and any(item.is_dir() for item in path.iterdir()):
            raise Blocked("Unexpected subdirectory in managed dashboard; preserved")
        return super().matches(path, record, partial)

    def _validate_state(self, state):
        super()._validate_state(state)
        if "dashboard_resume" in state and type(state["dashboard_resume"]) is not bool:
            raise Blocked("Invalid dashboard resume state")

    def _finish(self):
        pending = self.state["pending"]
        if not pending or self.state["journal"]:
            return
        resume = self.state.get("dashboard_resume", False) and not self.state["rollback"]
        # Confirm bytes on disk, NOT frontend runtime health or user acceptance.
        super().action("deploy-confirm", pending["commit"])
        self.save(paused=not resume, dashboard_resume=False)
        self.status = "Dashboard files verified and installed; refresh the browser page to load them. Runtime health not checked."

    def write(self, record, rollback=False):
        self.require_enabled()
        validate(record, DASHBOARD)
        if self.state["journal"]:
            raise Blocked("Recover interrupted deployment first")
        self.save(dashboard_resume=not self.state["paused"] and not rollback)
        super().write(record, rollback)
        self._finish()

    def recover(self):
        super().recover()
        self.save(dashboard_resume=False)
        self._finish()

    def poll(self, client, branch):
        if self.enabled and not self.state["journal"]:
            self._finish()  # Complete durable verified install after a process interruption.
        super().poll(client, branch)

    def action(self, name, commit, pending_commit=""):
        if name == "deploy-override":
            self.require_enabled()
            if self.state["pending"] or self.state["journal"]:
                raise Blocked("Resolve pending or interrupted dashboard installation first")
            # One explicit, commit-bound install. Never leave a grant for background polling.
            try:
                self.arm(commit, "install")
                super().action("deploy-install", commit)
            finally:
                self.grant = None
            return
        if name not in {"deploy-pause", "deploy-resume", "deploy-install", "deploy-rollback", "deploy-recover"}:
            raise Blocked("Dashboard has no HA restart, rollback override or manual health-confirm action")
        super().action(name, commit, pending_commit)

    def render(self, csrf):
        def button(action, label, record=None):
            commit = record["commit"] if record else ""
            return ('<form method="post" action="' + action + '"><input type="hidden" name="csrf" value="'
                    + html.escape(csrf) + '"><input type="hidden" name="commit" value="' + commit
                    + '"><button>' + label + '</button></form>')
        active = self.state["active"]
        label = lambda record: html.escape(record["manifest"]["version"]) if record else "None"
        controls = button("deploy-pause", "Pause dashboard updates") + button("deploy-resume", "Resume dashboard updates")
        if self.candidate and not self.state["pending"]:
            controls += button("deploy-install", "Install dashboard now (requires idle)", self.candidate)
            if not self.state["journal"]:
                controls += button("deploy-override", "Install dashboard while charging (once)", self.candidate)
        if active and self.state["history"]:
            controls += button("deploy-rollback", "Restore previous dashboard and pause updates", active)
        if self.state["journal"]:
            controls += button("deploy-recover", "Recover interrupted dashboard update")
        return ('<section><h2>Dashboard updates</h2><p>' + html.escape(self.status)
                + '</p><p>Installed files: ' + label(active) + '<br>Candidate: ' + label(self.candidate)
                + '<br>Automatic updates: ' + ("Paused" if self.state["paused"] else "Enabled")
                + '<br>Recovery versions: ' + str(len(self.state["history"]))
                + '/3</p><p>Stable resource: <code>/local/home-energy-managed/loader.js</code> (JavaScript module). '
                'Replace the old resource once; do not add both. Refresh the browser page after an update. '
                'No HA restart, resource URL edits or uploads for later releases. Dashboard YAML is untouched. '
                'Automatic updates wait until charging is confirmed idle. The explicit one-time install bypasses only '
                'the charging/unknown-state gate for this candidate, never file validation. No HA restart or charging command. '
                'Installed means verified files, not tested UI health.</p>'
                '<div class="actions">' + (controls if self.enabled else "Enable deployments in app Configuration first.")
                + '</div></section>')
