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
from urllib.request import Request, build_opener, HTTPRedirectHandler

DOMAIN = "home_energy_tesla"
MAX_SIZE = 1024 * 1024
ALLOWED = frozenset(("__init__.py", "config_flow.py", "const.py", "engine.py",
                     "power.py", "sensor.py", "switch.py", "manifest.json",
                     "strings.json", "translations/en.json", "README.md"))
CORE_FILES = frozenset(("__init__.py", "config_flow.py", "coordinator.py", "engine.py",
                        "sensor.py", "manifest.json", "strings.json", "translations/en.json", "README.md"))
LIVE_ALLOWED = ALLOWED | {"live.py"}
MODULES = {"home_energy_financial": CORE_FILES, "home_energy_planner": CORE_FILES,
           "home_energy_power": CORE_FILES, DOMAIN: LIVE_ALLOWED}
DASHBOARD = "home_energy_dashboard"
MANAGED_MODULES = (*MODULES, DASHBOARD)
DEPLOY_ACTIONS = ("deploy-install", "deploy-override", "deploy-restart", "restart-override",
                  "deploy-confirm", "deploy-rollback", "rollback-override", "deploy-recover",
                  "deploy-pause", "deploy-resume", "deploy-replace")


def payload_path(domain):
    return "releases/tesla-shadow.json" if domain == DOMAIN else "releases/" + domain + ".json"


def manifest_path(domain):
    return "deployment.json" if domain == DOMAIN else "deployments/" + domain + ".json"


DEFAULT_CONTACTOR = "binary_sensor.evse_contactor_closed"
DEFAULT_POWER = "sensor.evse_total_active_power"
DEFAULT_IDLE_POWER_WATTS = 50.0


class Blocked(ValueError):
    """Safe user-facing reason, never raw network errors."""


def validate(record, expected_domain=None):
    if not isinstance(record, dict) or not re.fullmatch("[0-9a-f]{40}", str(record.get("commit", ""))):
        raise Blocked("Invalid deployment commit")
    manifest, payload = record["manifest"], record["payload"]
    domain = manifest.get("domain")
    if domain == DASHBOARD:
        from dashboard_deployment import validate_dashboard
        if expected_domain not in (None, DASHBOARD):
            raise Blocked("Deployment domain mismatch")
        return validate_dashboard(record)
    schema = manifest.get("schema_version")
    if (domain not in MODULES or (expected_domain is not None and domain != expected_domain)
            or type(schema) is not int or schema not in (1, 2, 3)
            or (schema == 1 and domain != DOMAIN)
            or (schema == 2 and manifest.get("min_observer_version") != "0.4.0")
            or (schema == 3 and (domain != DOMAIN or manifest.get("min_observer_version") != "0.6.0"))
            or manifest.get("kind") != "ha-integration"
            or manifest.get("path") != payload_path(domain)):
        raise Blocked("Deployment manifest rejected")
    version = manifest.get("version", "")
    if not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise Blocked("Invalid deployment version")
    data = payload.encode("utf-8")
    if len(data) > MAX_SIZE or hashlib.sha256(data).hexdigest() != manifest.get("sha256"):
        raise Blocked("Deployment checksum or size rejected")
    files = json.loads(payload)
    expected_files = (LIVE_ALLOWED if schema == 3 else ALLOWED) if domain == DOMAIN else MODULES[domain]
    if not isinstance(files, dict) or set(files) != expected_files:
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
    if (integration.get("domain") != domain or integration.get("version") != version
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
            states = [self._request("states/" + name) for name in (self.contactor, self.power)]
            for name, state in zip((self.contactor, self.power), states):
                if state.get("entity_id") != name:
                    return self._charging_result("unknown", "entity response mismatch")
            # These push integrations may not report unchanged healthy values,
            # so HA timestamps are not a heartbeat. Depend on entity availability
            # and reject restored startup snapshots instead.
            for state in states:
                attributes = state.get("attributes", {})
                if not isinstance(attributes, dict) or attributes.get("restored") is True:
                    return self._charging_result("unknown", "entity state is restored or malformed")
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
    def __init__(self, directory, config_root, ha, enabled=False, domain=DOMAIN):
        if domain not in MANAGED_MODULES:
            raise Blocked("Unsupported integration domain")
        self.domain = domain
        self.root = Path(config_root)
        self.parent = self.root / "custom_components"
        self.target = self.parent / domain
        self.stage = self.parent / ("." + domain + ".stage")
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
                validate(record, self.domain)
        journal = state.get("journal")
        if journal:
            validate(journal["new"], self.domain)
            if journal["old"] is not None:
                validate(journal["old"], self.domain)
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
        expected = validate(record, self.domain)
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
            if relative not in expected or item.stat().st_size > MAX_SIZE:
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
        validate(record, self.domain)
        self.grant = (commit, operation, time.monotonic() + 600)
        self.status = "One-time " + operation + " override armed for this commit (10 minutes)"

    def require_enabled(self):
        if not self.enabled:
            raise Blocked("Enable deployments in app Configuration first")

    def write(self, record, rollback=False):
        self.require_enabled()
        validate(record, self.domain)
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
            for name, content in validate(record, self.domain).items():
                path = self.stage / name
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("x", encoding="utf-8", newline="") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
            if not self.matches(self.stage, record):
                raise Blocked("Staged verification failed")
            if (self.stage / "translations").exists():
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
        manifest = json.loads(client.get("/contents/" + manifest_path(self.domain) + "?ref=" + commit, raw=True))
        # Never use a remote-controlled path, even before validation.
        payload = client.get("/contents/" + payload_path(self.domain) + "?ref=" + commit, raw=True).decode("utf-8")
        record = dict(commit=commit, manifest=manifest, payload=payload)
        validate(record, self.domain)
        if self.grant and self.grant[1] == "install" and self.grant[0] != commit:
            self.grant = None
        self.candidate = record
        # Refresh the diagnostic even while deployments are paused or pending.
        # This is read-only and lets Check now explain a fail-closed gate.
        charging = self.ha.charging()
        if self.state["pending"]:
            self.status = "Pending verification; restart HA if needed, verify the integration, then confirm manually"
            return
        if self.state["paused"]:
            self.status = "Deployment candidate verified; automatic deployments paused"
            return
        if self.state["active"] and self.state["active"]["manifest"]["sha256"] == manifest["sha256"]:
            self.status = "Confirmed deployment is current"
            return
        # A normal charging block must not create a journal/staging transaction.
        if charging != "idle" and not self.grant:
            self.status = "Waiting: charging or charging state unknown"
            return
        self.write(record)

    def action(self, name, commit, pending_commit=""):
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
        elif name == "deploy-replace":
            pending = self.state["pending"]
            if self.state["journal"] or not pending or pending["commit"] != pending_commit:
                raise Blocked("No matching pending installation; refresh")
            if not self.candidate or self.candidate["commit"] != commit:
                raise Blocked("Candidate changed; refresh")
            validate(self.candidate, self.domain)
            if pending["manifest"]["sha256"] == self.candidate["manifest"]["sha256"]:
                raise Blocked("Candidate has the same files as the pending installation")
            # Explicit repair only; never inherit an install/restart override.
            self.grant = None
            self.write(self.candidate)
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
        def button(action, text, record=None, pending_commit=""):
            commit = record["commit"] if record else ""
            return '<form method="post" action="' + action + '"><input type="hidden" name="csrf" value="' + csrf + '"><input type="hidden" name="commit" value="' + commit + '"><input type="hidden" name="pending_commit" value="' + esc(pending_commit) + '"><button>' + text + '</button></form>'
        def label(record):
            return "None" if record is None else esc(record["manifest"]["version"] + " / " + record["commit"][:12])
        active, pending = self.state["active"], self.state["pending"]
        buttons = button("deploy-pause", "Pause deployments") + button("deploy-resume", "Resume deployments")
        if self.candidate and not pending:
            buttons += button("deploy-install", "Install verified candidate", self.candidate)
            buttons += button("deploy-override", "Force install despite charging — arm one-time override", self.candidate)
        if pending:
            if (self.candidate and not self.state["journal"]
                    and self.candidate["manifest"]["sha256"] != pending["manifest"]["sha256"]):
                buttons += button("deploy-replace", "Replace unverified installation (requires idle)",
                                  self.candidate, pending["commit"])
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
                '</div><p>Managed integration: ' + esc(self.domain) + '. Existing YAML charging automations remain active. Confirmation is your manual acceptance, not an automatic health check.</p></section>')


class SuiteInstaller:
    """Independent install/rollback transactions; no cross-directory atomic claim.

    Keeps the historic Tesla state path. New domains each have separate journals,
    overrides, three-version history and explicit acceptance. All poll one commit.
    """
    def __init__(self, directory, config_root, ha, enabled=False):
        self.modules = {domain: Installer(directory if domain == DOMAIN else Path(directory) / domain,
                       config_root, ha, enabled, domain) for domain in MODULES}
        from dashboard_deployment import DashboardInstaller
        self.modules[DASHBOARD] = DashboardInstaller(Path(directory) / DASHBOARD, config_root, ha, enabled)
        self.status = "Each integration installs independently. Install central modules before Tesla."

    def poll(self, client, branch):
        if not any(module.enabled for module in self.modules.values()):
            return
        for module in self.modules.values():
            module.candidate = None
        try:
            commit = client.get("/commits/" + branch)
        except Exception:
            for module in self.modules.values():
                module.grant = None
            raise

        class PinnedClient:
            def get(self, path, **kwargs):
                return commit if path.startswith("/commits/") else client.get(path, **kwargs)

        for module in self.modules.values():
            try:
                module.poll(PinnedClient(), branch)
            except Exception as error:
                module.candidate = None
                module.grant = None
                module.status = (str(error) if isinstance(error, Blocked) else
                                 "Module unavailable or validation failed; existing installation preserved")

    def action(self, name, commit, pending_commit=""):
        action, separator, domain = name.partition("--")
        domain = domain if separator else DOMAIN
        if domain not in self.modules or action not in DEPLOY_ACTIONS:
            raise Blocked("Unknown module action")
        if action == "deploy-restart" and any(module.state["journal"] for module in self.modules.values()):
            raise Blocked("Recover all interrupted module installations before restarting HA")
        if action == "deploy-replace":
            self.modules[domain].action(action, commit, pending_commit)
        else:
            self.modules[domain].action(action, commit)

    def render(self, csrf):
        esc = html.escape
        names = {
            "home_energy_financial": "Financial",
            "home_energy_planner": "Planner",
            "home_energy_power": "Power Manager",
            DOMAIN: "Tesla",
            DASHBOARD: "Dashboard",
        }

        def is_new_candidate(module):
            candidate = module.candidate
            if not candidate:
                return False
            for record in (module.state["active"], module.state["pending"]):
                if record and record["manifest"].get("sha256") == candidate["manifest"].get("sha256"):
                    return False
            return True

        # The first unresolved release operation is shown as the page's primary
        # action. The detailed module forms below remain the source of truth for
        # every other operation and keep their commit/CSRF checks intact.
        selected = None
        selected_action = ""
        for domain, module in self.modules.items():
            if module.enabled and module.state["journal"]:
                selected, selected_action = (domain, module), "deploy-recover"
                break
        for domain, module in self.modules.items():
            if selected is None and module.enabled and module.state["pending"] and domain != DASHBOARD:
                # Pending integrations always need an explicit human health check.
                # HA exposes no dependable persisted boot marker here, so do not
                # keep telling the user to restart after a reboot they performed.
                selected, selected_action = (domain, module), "deploy-confirm"
                break
        if selected is None and not any(m.state["journal"] for m in self.modules.values()):
            for domain, module in self.modules.items():
                if (module.enabled and not module.state["paused"] and is_new_candidate(module)
                        and not module.state["pending"]):
                    selected, selected_action = (domain, module), "deploy-install"
                    break

        def action_form(domain, module, action, text):
            record = (module.state["pending"] if action in ("deploy-restart", "deploy-confirm") else
                      module.candidate if action in ("deploy-install", "deploy-replace") else None)
            commit = record["commit"] if record else ""
            pending_commit = module.state["pending"]["commit"] if action == "deploy-replace" and module.state["pending"] else ""
            return ('<form method="post" action="' + action + '--' + domain + '">'
                    '<input type="hidden" name="csrf" value="' + esc(csrf) + '">'
                    '<input type="hidden" name="commit" value="' + esc(commit) + '">'
                    '<input type="hidden" name="pending_commit" value="' + esc(pending_commit) + '">'
                    '<button class="primary-action">' + text + ' <span aria-hidden="true">→</span></button></form>')

        if selected:
            domain, module = selected
            record = module.state["pending"] if selected_action in ("deploy-restart", "deploy-confirm") else module.candidate
            version = record["manifest"]["version"] if record else ""
            if selected_action == "deploy-recover":
                headline = "Een onderbroken installatie vraagt om herstel"
                description = "Herstel de bestanden van " + names.get(domain, domain) + " voordat je verdergaat."
                button_text = "Herstel installatie"
                stage = "Herstel nodig"
            elif selected_action == "deploy-restart":
                headline = names.get(domain, domain) + " wacht op een Home Assistant-herstart"
                description = "Versie " + version + " is geïnstalleerd en wacht op controle. Herstart daarna Home Assistant en controleer de werking."
                button_text = "Herstart Home Assistant"
                stage = "Herstart nodig"
            elif selected_action == "deploy-confirm":
                headline = names.get(domain, domain) + " wacht op controle en bevestiging"
                description = "Herstart eerst Home Assistant als dat na installatie nog niet is gebeurd. Controleer daarna of Home Assistant en versie " + version + " van de integratie goed werken en bevestig de installatie handmatig."
                button_text = "Ik heb herstart en de integratie gecontroleerd — bevestig"
                stage = "Controle nodig"
            elif domain == DASHBOARD:
                headline = "Dashboardupdate beschikbaar"
                description = "Versie " + version + " is gecontroleerd. Na installatie vernieuw je het Home Assistant-dashboard in je browser."
                button_text = "Installeer dashboardbestanden"
                stage = "Installatie beschikbaar"
            else:
                headline = "Nieuwe versie beschikbaar voor " + names.get(domain, domain)
                description = "Versie " + version + " is gecontroleerd en klaar voor installatie."
                button_text = "Installeer gecontroleerde versie"
                stage = "Installatie beschikbaar"
            primary = action_form(domain, module, selected_action, button_text)
            pending = module.state["pending"]
            if (pending and module.candidate and
                    module.candidate["manifest"]["sha256"] != pending["manifest"]["sha256"]):
                primary += action_form(domain, module, "deploy-replace",
                    "Vervang door kandidaat " + module.candidate["manifest"]["version"] + " (vereist inactief laden)")
            alert_class = "observer-alert"
        else:
            blocked_candidate = next(((domain, module) for domain, module in self.modules.items()
                                      if is_new_candidate(module)), None)
            blocked_pending = next(((domain, module) for domain, module in self.modules.items()
                                    if module.state["pending"] and not module.enabled), None)
            blocked_recovery = next(((domain, module) for domain, module in self.modules.items()
                                     if module.state["journal"] and not module.enabled), None)
            if blocked_recovery:
                headline = "Herstel nodig voor " + names.get(blocked_recovery[0], blocked_recovery[0])
                description = "Deployments staan uit. Schakel ze in bij app Configuration om de onderbroken installatie te herstellen."
                stage = "Herstel geblokkeerd"
                alert_class = "observer-alert"
            elif blocked_pending:
                headline = "Een installatie wacht op controle en bevestiging"
                description = "Deployments staan uit. Schakel ze in bij app Configuration om verder te gaan met " + names.get(blocked_pending[0], blocked_pending[0]) + ". Herstart Home Assistant als dat na installatie nog niet is gebeurd."
                stage = "Deployment uitgeschakeld"
                alert_class = "observer-alert"
            elif blocked_candidate and not blocked_candidate[1].enabled:
                headline = "Er is een kandidaat, maar deployments staan uit"
                description = "Schakel deployments voor " + names.get(blocked_candidate[0], blocked_candidate[0]) + " in bij app Configuration."
                stage = "Deployment uitgeschakeld"
                alert_class = "observer-alert"
            elif blocked_candidate and blocked_candidate[1].state["paused"]:
                headline = "Nieuwe versie beschikbaar voor " + names.get(blocked_candidate[0], blocked_candidate[0])
                description = "Automatische deployments staan gepauzeerd. Hervat deployments bij de geavanceerde acties om verder te gaan."
                stage = "Deployments gepauzeerd"
                alert_class = "observer-alert"
            else:
                headline = "Geen openstaande releaseactie"
                description = "Er is geen nieuwe kandidaat beschikbaar. Gebruik Controleer nu om releases opnieuw op te halen."
                stage = "Geen openstaande actie"
                alert_class = "observer-alert observer-alert-clear"
            primary = ""

        confirmation_count = sum(bool(m.state["pending"]) for domain, m in self.modules.items() if domain != DASHBOARD)
        candidate_count = sum(is_new_candidate(m) for m in self.modules.values())
        normal_pending = any(m.state["pending"] for domain, m in self.modules.items() if domain != DASHBOARD)
        normal_candidate = any(is_new_candidate(m) for domain, m in self.modules.items() if domain != DASHBOARD)
        dashboard_module = self.modules[DASHBOARD]
        dashboard_release = bool(dashboard_module.state["pending"] or is_new_candidate(dashboard_module))
        if normal_pending:
            current_step = 3
        elif candidate_count or dashboard_module.state["pending"] or any(m.state["journal"] for m in self.modules.values()):
            current_step = 2
        else:
            current_step = 0
        step_three = "Herstart en controle" if normal_pending else ("HA herstarten" if normal_candidate else ("Dashboard verversen in browser" if dashboard_release else "HA herstarten"))
        flow_steps = ("Kandidaat gecontroleerd", "Bestanden geïnstalleerd", step_three, "Integraties handmatig bevestigen")
        flow = []
        for index, title in enumerate(flow_steps, 1):
            css = " neutral" if current_step == 0 else (" done" if index < current_step else (" current" if index == current_step else ""))
            marker = "–" if current_step == 0 else ("✓" if index < current_step else str(index))
            flow.append('<div class="observer-step' + css + '"><span>' + marker + '</span>' + title + '</div>')

        charging = getattr(self.modules[DOMAIN].ha, "last_charging_check", "Niet gecontroleerd")
        if not isinstance(charging, str):
            charging = "Niet beschikbaar"
        automatic_count = sum(m.enabled and not m.state["paused"] for m in self.modules.values())
        if automatic_count == len(self.modules):
            updates = "Automatische deployments actief (5/5)"
        elif automatic_count:
            updates = "Automatische deployments gemengd (" + str(automatic_count) + "/5 actief)"
        else:
            updates = "Automatische deployments gepauzeerd of uitgeschakeld (0/5)"
        rows = []
        for domain, module in self.modules.items():
            active = module.state["active"]
            pending = module.state["pending"]
            candidate = module.candidate if is_new_candidate(module) else None
            installed_version = active["manifest"]["version"] if active else "—"
            pending_version = pending["manifest"]["version"] if pending else "—"
            candidate_version = candidate["manifest"]["version"] if candidate else "—"
            if module.state["journal"]:
                status, status_class = "Herstel nodig", "warn"
            elif pending:
                if domain == DASHBOARD:
                    status, status_class = "Bestanden controleren", "blue"
                else:
                    status, status_class = "Controle nodig", "warn"
            elif candidate:
                status, status_class = "Nieuwe versie", "blue"
            elif domain == DASHBOARD and active:
                status, status_class = "Bestanden geplaatst", "blue"
            elif active:
                status, status_class = "Actueel", "ok"
            else:
                status, status_class = "Nog niet geïnstalleerd", "neutral"
            rows.append('<div class="observer-row"><div class="observer-module"><span class="observer-icon">'
                        + esc(names.get(domain, domain)[:1]) + '</span><strong>' + esc(names.get(domain, domain))
                        + '</strong></div><div data-label="Bevestigd">' + esc(installed_version)
                        + '</div><div data-label="Pending">' + esc(pending_version)
                        + '</div><div data-label="Kandidaat">' + esc(candidate_version)
                        + '</div><div data-label="Status"><span class="observer-status ' + status_class + '">'
                        + esc(status) + '</span></div></div>')

        parts = ['<section class="observer-overview">',
                 '<div class="observer-heading"><div><div class="observer-eyebrow">Home Energy Observer</div>'
                 '<h2>Releasebeheer</h2><p>Installaties, herstarts en herstelversies op één plek.</p></div>'
                 '<form method="post" action="check"><input type="hidden" name="csrf" value="' + esc(csrf)
                 + '"><button class="observer-check">↻ &nbsp; Controleer nu</button></form></div>',
                 '<div class="observer-hero"><section class="' + alert_class + '"><div class="observer-eyebrow">'
                 + esc(stage) + '</div><h3>' + esc(headline) + '</h3><p>' + esc(description)
                 + '</p>' + primary + '</section><section class="observer-safety"><div class="observer-eyebrow">'
                 + 'Status en veiligheid</div><h3>' + esc(updates) + '</h3><p><strong>Laadcontrole laatst:</strong> '
                 + esc(charging) + '</p><p class="observer-note">Een herstart onderbreekt tijdelijk alle Home Assistant-automatiseringen, ook laadbeveiliging.</p></section></div>',
                 '<section class="observer-flow"><div class="observer-section-head"><h3>Releasepad</h3><span>'
                 + str(confirmation_count) + ' openstaande bevestiging(en) · ' + str(candidate_count) + ' kandidaat(en)</span></div><div class="observer-steps">'
                 + ''.join(flow) + '</div></section>',
                 '<section class="observer-modules"><div class="observer-section-head"><h3>Onderdelen</h3><span>Vijf integraties</span></div>'
                 '<div class="observer-table-head"><div>Onderdeel</div><div>Bevestigd</div><div>Pending</div><div>Kandidaat</div><div>Status</div></div>'
                 + ''.join(rows) + '</section>',
                 '<details class="observer-advanced"><summary>Geavanceerde acties, details en herstelversies</summary>'
                 '<p class="observer-note">Alle installatie-, pauzeer-, hervat-, bevestig-, rollback- en herstelacties staan per onderdeel hieronder.</p>']
        for domain, module in self.modules.items():
            page = module.render(csrf)
            for action in DEPLOY_ACTIONS:
                page = page.replace('action="' + action + '"', 'action="' + action + '--' + domain + '"')
            # The outer wrapper provides a compact summary; preserve the full
            # trusted module renderer inside so no existing action is lost.
            content = page.removeprefix("<section>").removesuffix("</section>")
            content = content.replace("Integration deployment — experimental", esc(names.get(domain, domain)))
            content = content.replace("<h2>", '<h3 class="observer-detail-title">', 1).replace("</h2>", "</h3>", 1)
            parts.append('<details class="observer-detail"><summary>' + esc(names.get(domain, domain)) + '</summary><section>' + content + '</section></details>')
        parts.append('</details>')
        return ''.join(parts)
