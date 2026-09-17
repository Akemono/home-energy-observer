"""Restricted deployment workflow. Repository code is trusted, not sandboxed."""
import copy
import ctypes
import hashlib
import html
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
from datetime import datetime, timezone
from urllib.request import Request, build_opener, HTTPRedirectHandler

DOMAIN = "home_energy_tesla"
MAX_SIZE = 1024 * 1024
ALLOWED = frozenset(("__init__.py", "config_flow.py", "const.py", "engine.py",
                     "power.py", "sensor.py", "switch.py", "manifest.json",
                     "strings.json", "translations/en.json", "README.md"))
DEFAULT_CONTACTOR = "binary_sensor.evse_contactor_closed"
DEFAULT_POWER = "sensor.evse_total_active_power"
DEFAULT_IDLE_POWER_WATTS = 50.0


class Blocked(ValueError):
    """Safe user-facing reason, never raw network errors."""


def validate(record):
    if not isinstance(record, dict) or not re.fullmatch("[0-9a-f]{40}", str(record.get("commit", ""))):
        raise Blocked("Invalid deployment commit")
    manifest, payload = record["manifest"], record["payload"]
    if (type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1
            or manifest.get("kind") != "ha-integration"
            or manifest.get("path") != "releases/tesla-shadow.json"
            or manifest.get("domain") != DOMAIN):
        raise Blocked("Deployment manifest rejected")
    version = manifest.get("version", "")
    if not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise Blocked("Invalid deployment version")
    data = payload.encode("utf-8")
    if len(data) > MAX_SIZE or hashlib.sha256(data).hexdigest() != manifest.get("sha256"):
        raise Blocked("Deployment checksum or size rejected")
    files = json.loads(payload)
    if not isinstance(files, dict) or set(files) != ALLOWED:
        raise Blocked("Deployment file allowlist rejected")
    for name, content in files.items():
        if not isinstance(content, str) or "\x00" in content:
            raise Blocked("Invalid file content")
        if name.endswith(".py"):
            # Compile only: no execution/import of downloaded Python.
            compile(content, name, "exec", dont_inherit=True)
        elif name.endswith(".json"):
            json.loads(content)
    integration = json.loads(files["manifest.json"])
    if (integration.get("domain") != DOMAIN or integration.get("version") != version
            or integration.get("requirements") != [] or integration.get("config_flow") is not True):
        raise Blocked("Integration metadata rejected")
    return files


def atomic_json(path, value):
    encoded = json.dumps(value, ensure_ascii=True).encode()
    fd, name = tempfile.mkstemp(prefix=".state-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        sync_directory(path.parent)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def sync_directory(path):
    if os.name == "posix":
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def exchange(left, right):
    """Linux atomic directory exchange; no unsafe multi-rename fallback."""
    library = ctypes.CDLL(None, use_errno=True)
    rename = getattr(library, "renameat2", None)
    if rename is None:
        raise Blocked("Filesystem atomic exchange unavailable; no update performed")
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(left), -100, os.fsencode(right), 2) != 0:
        raise OSError(ctypes.get_errno(), "Atomic directory exchange failed")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class HomeAssistant:
    def __init__(self, token, contactor=DEFAULT_CONTACTOR, power=DEFAULT_POWER,
                 idle_power_watts=DEFAULT_IDLE_POWER_WATTS):
        if not re.fullmatch(r"binary_sensor\.[a-z0-9_]+", contactor):
            raise Blocked("Invalid charging contactor entity ID")
        if not re.fullmatch(r"sensor\.[a-z0-9_]+", power):
            raise Blocked("Invalid charging power entity ID")
        if (isinstance(idle_power_watts, bool) or not isinstance(idle_power_watts, (int, float))
                or not math.isfinite(float(idle_power_watts))
                or not 1 <= float(idle_power_watts) <= 100):
            raise Blocked("Idle power limit must be between 1 and 100 W")
        self.token = token
        self.contactor = contactor
        self.power = power
        self.idle_power_watts = float(idle_power_watts)
        self.last_charging_check = "Not checked since app start"
        self.opener = build_opener(NoRedirect())

    def _charging_result(self, status, detail):
        self.last_charging_check = status + ": " + detail
        return status

    def _request(self, suffix, post=False):
        if not self.token:
            raise Blocked("HA API token unavailable")
        request = Request("http://supervisor/core/api/" + suffix,
                          data=b"{}" if post else None,
                          headers={"Authorization": "Bearer " + self.token,
                                   "Content-Type": "application/json"})
        with self.opener.open(request, timeout=10) as response:
            raw = response.read(MAX_SIZE + 1)
        if len(raw) > MAX_SIZE:
            raise Blocked("HA response too large")
        return json.loads(raw)

    def charging(self):
        try:
            # A state that is unchanged may have an old last_updated.
            # Use the integration's periodic last_reported time, fail closed if absent.
            states = [self._request("states/" + name) for name in (self.contactor, self.power)]
            now = datetime.now(timezone.utc)
            for name, state in zip((self.contactor, self.power), states):
                if state.get("entity_id") != name:
                    return self._charging_result("unknown", "entity response mismatch")
                stamp = datetime.fromisoformat(state["last_reported"].replace("Z", "+00:00"))
                age = (now - stamp).total_seconds()
                if not 0 <= age <= 120:
                    return self._charging_result("unknown", name + " is stale or future-dated")
            watts = float(states[1]["state"])
            if not math.isfinite(watts):
                return self._charging_result("unknown", "dedicated EVSE power is not finite")
            if states[0]["state"] not in ("on", "off"):
                return self._charging_result("unknown", "contactor state is not on/off")
            if states[0]["state"] == "on":
                return self._charging_result("charging", "contactor is on")
            if abs(watts) > self.idle_power_watts:
                return self._charging_result("charging", "dedicated EVSE power exceeds %.1f W" % self.idle_power_watts)
            return self._charging_result("idle", "contactor off and dedicated EVSE power within %.1f W" % self.idle_power_watts)
        except Exception:
            return self._charging_result("unknown", "sensor/API read or validation failed")

    def restart(self):
        return self._request("services/homeassistant/restart", post=True)


class Installer:
    def __init__(self, directory, config_root, ha, enabled=False):
        self.root = Path(config_root)
        self.parent = self.root / "custom_components"
        self.target = self.parent / DOMAIN
        self.stage = self.parent / ("." + DOMAIN + ".stage")
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "deployment-state.json"
        self.ha, self.enabled = ha, enabled
        self.grant = None
        self.candidate = None
        self.status = "Deployments disabled in Configuration"
        self.state = {"schema": 1, "active": None, "history": [], "pending": None,
                      "paused": True, "journal": None, "rollback": False}
        if self.path.exists():
            if self.path.stat().st_size > MAX_SIZE * 12:
                raise Blocked("Deployment state too large")
            self.state = json.loads(self.path.read_text())
            self._validate_state(self.state)

    def _validate_state(self, state):
        if (not isinstance(state, dict) or type(state.get("schema")) is not int
                or state["schema"] != 1 or type(state.get("paused")) is not bool
                or type(state.get("rollback")) is not bool
                or not isinstance(state.get("history"), list) or len(state["history"]) > 3):
            raise Blocked("Invalid deployment state")
        for record in state["history"] + [state.get("active"), state.get("pending")]:
            if record is not None:
                validate(record)
        journal = state.get("journal")
        if journal:
            validate(journal["new"])
            if journal["old"] is not None:
                validate(journal["old"])
        if len(json.dumps(state, ensure_ascii=True).encode()) > MAX_SIZE * 12:
            raise Blocked("Deployment state exceeds storage limit")

    def save(self, **changes):
        state = dict(copy.deepcopy(self.state), **changes)
        self._validate_state(state)
        atomic_json(self.path, state)
        self.state = state

    def paths(self):
        if not self.root.is_dir() or self.root.is_symlink():
            raise Blocked("HA config mount missing or symlinked")
        for path in (self.parent, self.target, self.stage):
            if path.is_symlink():
                raise Blocked("Symlink in managed path; deployment blocked")
            if path.exists() and not path.is_dir():
                raise Blocked("Managed directory path is not a directory")
        if self.parent.resolve().parent != self.root.resolve():
            raise Blocked("Managed path escapes HA config")

    def matches(self, path, record, partial=False):
        self.paths()
        if record is None:
            return not path.exists()
        if not path.is_dir():
            return False
        expected = validate(record)
        found = {}
        for item in path.rglob("*"):
            if item.is_symlink():
                raise Blocked("Symlink inside managed directory")
            relative = item.relative_to(path).as_posix()
            if item.is_dir():
                if relative not in ("translations", "__pycache__"):
                    raise Blocked("Unmanaged directory inside integration")
                continue
            if not item.is_file():
                raise Blocked("Non-regular file inside integration")
            if relative.startswith("__pycache__/") and item.suffix == ".pyc":
                continue
            if relative not in ALLOWED or item.stat().st_size > MAX_SIZE:
                return False
            content = item.read_bytes()
            if partial:
                if not expected[relative].encode("utf-8").startswith(content):
                    return False
            else:
                found[relative] = content
        return partial or found == {name: content.encode("utf-8") for name, content in expected.items()}

    def cleanup_stage(self, record, partial=False):
        if not self.stage.exists():
            return
        if record is None or not self.matches(self.stage, record, partial=partial):
            raise Blocked("Unrecognised staging directory; preserved for inspection")
        # Exact fixed sibling, checked for containment, symlinks and owned content.
        shutil.rmtree(self.stage)
        sync_directory(self.parent)

    def safety(self, record, operation, consume=True):
        commit = record["commit"]
        grant = self.grant
        # Consume even when charging check fails or operation later fails.
        if consume:
            self.grant = None
        charging = self.ha.charging()
        override = bool(grant and grant[0] == commit and grant[1] == operation and time.monotonic() < grant[2])
        if charging != "idle" and not override:
            raise Blocked("Waiting: charging or charging state unknown")
        return charging

    def arm(self, commit, operation):
        previous = self.state["active"] if self.state["pending"] else (self.state["history"][0] if self.state["history"] else None)
        record = self.candidate if operation == "install" else self.state["pending"] if operation == "restart" else previous
        if record is None or record["commit"] != commit:
            raise Blocked("Candidate changed; refresh the page")
        if operation not in ("install", "restart", "rollback"):
            raise Blocked("Invalid override operation")
        validate(record)
        self.grant = (commit, operation, time.monotonic() + 600)
        self.status = "One-time " + operation + " override armed for this commit (10 minutes)"

    def require_enabled(self):
        if not self.enabled:
            raise Blocked("Enable deployments in app Configuration first")

    def write(self, record, rollback=False):
        self.require_enabled()
        validate(record)
        if self.state["journal"]:
            raise Blocked("Interrupted deployment; use Recover interrupted deployment")
        self.paths()
        old = self.state["pending"] or self.state["active"]
        if not self.matches(self.target, old):
            raise Blocked("Existing integration is unmanaged or modified; refusing overwrite")
        if self.stage.exists():
            raise Blocked("Staging directory already exists; preserved")
        if old and old["manifest"]["sha256"] == record["manifest"]["sha256"]:
            self.grant = None
            self.status = "These integration files are already installed"
            return
        self.safety(record, "rollback" if rollback else "install", consume=False)
        self.parent.mkdir(exist_ok=True)
        # Durable intent before any staged filesystem changes.
        self.save(journal={"old": old, "new": record, "rollback": rollback})
        try:
            self.stage.mkdir()
            for name, content in validate(record).items():
                path = self.stage / name
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("x", encoding="utf-8", newline="") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
            if not self.matches(self.stage, record):
                raise Blocked("Staged verification failed")
            sync_directory(self.stage / "translations")
            sync_directory(self.stage)
            # Recheck immediately before the directory exchange, not at download time.
            self.safety(record, "rollback" if rollback else "install")
            if not self.matches(self.target, old) or not self.matches(self.stage, record):
                raise Blocked("Files changed during deployment; swap refused")
            if self.target.exists():
                exchange(self.target, self.stage)
            else:
                os.rename(self.stage, self.target)
            sync_directory(self.parent)
            self.save(pending=record, rollback=rollback, paused=True)
            self.cleanup_stage(old)
            self.save(journal=None)
            self.status = "Files installed; restart HA, then confirm the integration works"
        except Exception:
            # Journal and both recoverable snapshots remain. Never silently report success.
            self.status = "Deployment interrupted or blocked; recover before continuing"
            raise

    def recover(self):
        self.require_enabled()
        journal = self.state["journal"]
        if not journal:
            raise Blocked("No interrupted deployment")
        old, new = journal["old"], journal["new"]
        # Recovery never replaces/removes the live target, hence needs no charging override.
        if self.matches(self.target, new):
            self.save(pending=new, paused=True, rollback=journal["rollback"])
            self.cleanup_stage(old)
            self.save(journal=None)
            self.status = "Installed files recovered; restart HA and confirm"
        elif self.matches(self.target, old):
            # Only exact prefixes of journal-owned files may be discarded.
            self.cleanup_stage(new, partial=True)
            self.save(journal=None, paused=True)
            self.status = "Previous installation preserved; deployments paused"
        else:
            raise Blocked("Unexpected files; automatic recovery refused. Restore HA backup")

    def poll(self, client, branch):
        if not self.enabled:
            self.status = "Deployments disabled in Configuration"
            return
        if self.state["journal"]:
            self.status = "Interrupted deployment; use Recover interrupted deployment"
            return
        self.candidate = None
        commit = client.get("/commits/" + branch)["sha"]
        if not isinstance(commit, str) or not re.fullmatch("[0-9a-f]{40}", commit):
            raise Blocked("Invalid Git commit")
        manifest = json.loads(client.get("/contents/deployment.json?ref=" + commit, raw=True))
        # Never use a remote-controlled path, even before validation.
        payload = client.get("/contents/releases/tesla-shadow.json?ref=" + commit, raw=True).decode("utf-8")
        record = dict(commit=commit, manifest=manifest, payload=payload)
        validate(record)
        if self.grant and self.grant[1] == "install" and self.grant[0] != commit:
            self.grant = None
        self.candidate = record
        if self.state["pending"]:
            self.status = "Awaiting restart and confirmation of installed files"
            return
        if self.state["paused"]:
            self.status = "Deployment candidate verified; automatic deployments paused"
            return
        if self.state["active"] and self.state["active"]["manifest"]["sha256"] == manifest["sha256"]:
            self.status = "Confirmed deployment is current"
            return
        # A normal charging block must not create a journal/staging transaction.
        if self.ha.charging() != "idle" and not self.grant:
            self.status = "Waiting: charging or charging state unknown"
            return
        self.write(record)

    def action(self, name, commit):
        self.require_enabled()
        if name == "deploy-recover":
            self.recover()
        elif name == "deploy-pause":
            self.save(paused=True)
            self.grant = None
            self.status = "Automatic deployments paused"
        elif name == "deploy-resume":
            if self.state["pending"] or self.state["journal"]:
                raise Blocked("Resolve pending deployment first")
            self.save(paused=False)
            self.status = "Automatic deployments enabled"
        elif name in ("deploy-override", "restart-override", "rollback-override"):
            self.arm(commit, {"deploy-override": "install", "restart-override": "restart",
                              "rollback-override": "rollback"}[name])
        elif name == "deploy-install":
            if self.state["pending"]:
                raise Blocked("Resolve pending deployment first")
            if not self.candidate or self.candidate["commit"] != commit:
                raise Blocked("Candidate changed; refresh")
            self.write(self.candidate)
        elif name == "deploy-restart":
            pending = self.state["pending"]
            if self.state["journal"] or not pending or pending["commit"] != commit:
                raise Blocked("No matching pending installation")
            if not self.matches(self.target, pending):
                raise Blocked("Installed files changed")
            self.safety(pending, "restart")
            self.status = "HA restart requested; verify the integration afterwards"
            self.ha.restart()
        elif name == "deploy-confirm":
            pending = self.state["pending"]
            if self.state["journal"] or not pending or pending["commit"] != commit:
                raise Blocked("No matching pending installation")
            if not self.matches(self.target, pending):
                raise Blocked("Installed files changed; cannot confirm")
            # Explicit human acceptance, NOT an automatic HA health claim.
            history = [r for r in self.state["history"] if r["manifest"]["sha256"] != pending["manifest"]["sha256"]]
            if self.state["active"] and not self.state["rollback"]:
                history.insert(0, self.state["active"])
            self.save(active=pending, pending=None, history=history[:3], paused=True, rollback=False)
            self.grant = None
            self.status = "Installation confirmed by user; deployments paused"
        elif name == "deploy-rollback":
            current = self.state["pending"] or self.state["active"]
            if not current or current["commit"] != commit:
                raise Blocked("Installed version changed; refresh")
            previous = self.state["active"] if self.state["pending"] else (self.state["history"][0] if self.state["history"] else None)
            if previous is None:
                raise Blocked("No previous managed version. Use your HA backup for first-install recovery")
            self.write(previous, rollback=True)
        else:
            raise Blocked("Unknown deployment action")

    def render(self, csrf):
        esc = html.escape
        def button(action, text, record=None):
            commit = record["commit"] if record else ""
            return '<form method="post" action="' + action + '"><input type="hidden" name="csrf" value="' + csrf + '"><input type="hidden" name="commit" value="' + commit + '"><button>' + text + '</button></form>'
        def label(record):
            return "None" if record is None else esc(record["manifest"]["version"] + " / " + record["commit"][:12])
        active, pending = self.state["active"], self.state["pending"]
        buttons = button("deploy-pause", "Pause deployments") + button("deploy-resume", "Resume deployments")
        if self.candidate and not pending:
            buttons += button("deploy-install", "Install verified candidate", self.candidate)
            buttons += button("deploy-override", "Force install despite charging — arm one-time override", self.candidate)
        if pending:
            buttons += button("deploy-restart", "Restart Home Assistant", pending)
            buttons += button("restart-override", "Force restart despite charging — arm one-time override", pending)
            buttons += button("deploy-confirm", "I verified the integration works — confirm installation", pending)
        if active:
            buttons += button("deploy-rollback", "Restore previous integration files", pending or active)
            previous = active if pending else (self.state["history"][0] if self.state["history"] else None)
            if previous:
                buttons += button("rollback-override", "Restore while charging — arm one-time override", previous)
        if self.state["journal"]:
            buttons += button("deploy-recover", "Recover interrupted deployment")
        grant = "None"
        if self.grant and time.monotonic() < self.grant[2]:
            grant = esc(self.grant[1] + " / " + self.grant[0][:12]) + " (one use, expires within 10 minutes)"
        gate_detail = getattr(self.ha, "last_charging_check", "Not available")
        if not isinstance(gate_detail, str):
            gate_detail = "Not available"
        return ('<section><h2>Integration deployment — experimental</h2><p>' + esc(self.status) +
                '</p><p>Confirmed: ' + label(active) + '<br>Pending verification: ' + label(pending) +
                '<br>Candidate: ' + label(self.candidate) + '<br>Recovery versions: ' + str(len(self.state["history"])) +
                '/3<br>Automatic deployments: ' + ("Paused" if self.state["paused"] else "Enabled") +
                '<br>Override: ' + grant + '<br>Charging gate last check: ' + esc(gate_detail) +
                '</p><p>Overrides bypass only the charging gate, never validation. Restarting HA interrupts ALL automations, including load protection. Do not rely on HA protection during restart.</p><div class="actions">' +
                (buttons if self.enabled else "Enable deployments in app Configuration to use these controls.") +
                '</div><p>Only home_energy_tesla is managed. Existing YAML charging automations remain active. Confirmation is your manual acceptance, not an automatic health check.</p></section>')
