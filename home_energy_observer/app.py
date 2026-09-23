"""Git observer with opt-in, explicitly authorised integration deployment."""
import copy
import hashlib
import html
import json
import os
import re
import secrets
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, quote
from urllib.request import Request, build_opener, HTTPRedirectHandler
from deployment import (Blocked, Installer, SuiteInstaller, MANAGED_MODULES as MODULES, DEPLOY_ACTIONS,
                        HomeAssistant, DEFAULT_CONTACTOR, DEFAULT_POWER, DEFAULT_IDLE_POWER_WATTS)

MAX_BYTES = 1024 * 1024
APP_VERSION = "0.6.2"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate_release(manifest, payload):
    if not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1:
        raise ValueError("Unsupported schema")
    if manifest.get("kind") != "dry-run":
        raise ValueError("Only dry-run releases accepted")
    if manifest.get("path") != "releases/probe.json":
        raise ValueError("Path not allowed")
    if not isinstance(manifest.get("version"), str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", manifest["version"]):
        raise ValueError("Invalid version")
    if len(payload) > MAX_BYTES:
        raise ValueError("Payload too large")
    if hashlib.sha256(payload).hexdigest() != manifest.get("sha256"):
        raise ValueError("Checksum mismatch")
    probe = json.loads(payload.decode("utf-8"))
    if not isinstance(probe, dict) or probe.get("purpose") != "connectivity-test":
        raise ValueError("Invalid probe")
    return {"version": manifest["version"], "sha256": manifest["sha256"]}


def validate_record(record):
    if not isinstance(record, dict) or not isinstance(record.get("commit"), str) or not re.fullmatch(r"[0-9a-f]{40}", record["commit"]):
        raise ValueError("Invalid commit")
    return dict(validate_release(record["manifest"], record["payload"].encode("utf-8")), commit=record["commit"])


class GitHub:
    def __init__(self, repository, token):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise ValueError("Invalid repository")
        self.root = "https://api.github.com/repos/" + repository
        self.token = token
        self.opener = build_opener(NoRedirect())

    def get(self, suffix, raw=False):
        headers = {"Accept": "application/vnd.github.raw+json" if raw else "application/vnd.github+json", "User-Agent": "Home-Energy-Observer/" + APP_VERSION}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        with self.opener.open(Request(self.root + suffix, headers=headers), timeout=20) as response:
            data = response.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError("Response too large")
        return data if raw else json.loads(data)

    def poll(self, branch):
        commit = self.get("/commits/" + quote(branch, safe=""))["sha"]
        if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise ValueError("Invalid commit")
        manifest = json.loads(self.get("/contents/release.json?ref=" + commit, raw=True))
        payload = self.get("/contents/releases/probe.json?ref=" + commit, raw=True)
        return dict(validate_release(manifest, payload), commit=commit, manifest=manifest, payload=payload.decode("utf-8"))


class ReleaseStore:
    """One atomic snapshot contains active payload, three predecessors and pause.

    An interrupted write leaves the prior snapshot intact. No external commands
    or repository paths are executed. This is not a multi-file HA installer.
    """
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "state.json"
        self.lock = threading.RLock()
        self.state = {"schema": 1, "active": None, "history": [], "paused": False}
        if self.path.exists():
            if self.path.stat().st_size > MAX_BYTES * 30:
                raise ValueError("Stored state too large")
            self.state = json.loads(self.path.read_text(encoding="utf-8"))
            self._validate(self.state)

    def _validate(self, state):
        if not isinstance(state, dict) or state.get("schema") != 1 or type(state.get("paused")) is not bool:
            raise ValueError("Invalid stored state")
        if not isinstance(state.get("history"), list) or len(state["history"]) > 3:
            raise ValueError("Invalid recovery history")
        for record in ([state["active"]] if state.get("active") else []) + state["history"]:
            validate_record(record)

    def _save(self, state):
        self._validate(state)
        encoded = json.dumps(state, ensure_ascii=True).encode("utf-8")
        fd, temporary = tempfile.mkstemp(prefix=".stage-", dir=self.directory)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            # Read back staged bytes before the only commit point.
            staged = json.loads(Path(temporary).read_bytes())
            self._validate(staged)
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        self.state = state

    def install(self, candidate):
        validate_record(candidate)
        record = {key: copy.deepcopy(candidate[key]) for key in ("manifest", "payload", "commit")}
        with self.lock:
            if self.state["paused"]:
                return "Updates paused"
            if self.state["active"] and self.state["active"]["commit"] == record["commit"]:
                return "Already installed in sandbox"
            state = copy.deepcopy(self.state)
            if state["active"]:
                state["history"] = [state["active"]] + [item for item in state["history"] if item["commit"] != record["commit"]]
            state["history"] = state["history"][:3]
            state["active"] = record
            self._save(state)
            return "Installed in sandbox"

    def rollback(self, expected_commit):
        with self.lock:
            if not self.state["active"] or self.state["active"]["commit"] != expected_commit:
                raise ValueError("Active release changed; refresh before rollback")
            if not self.state["history"]:
                raise ValueError("No previous release")
            state = copy.deepcopy(self.state)
            state["active"] = state["history"].pop(0)
            # Rollback consumes the most recent recovery point; polling must not undo it.
            state["paused"] = True
            self._save(state)
            return "Rolled back; automatic updates paused"

    def set_paused(self, value):
        with self.lock:
            state = copy.deepcopy(self.state)
            state["paused"] = value
            self._save(state)

    def summary(self):
        with self.lock:
            return {"active": validate_record(self.state["active"]) if self.state["active"] else None,
                    "history": [validate_record(item) for item in self.state["history"]],
                    "paused": self.state["paused"]}


class Observer:
    def __init__(self, store=None, installer=None):
        self.store = store
        self.installer = installer
        self.lock = threading.RLock()
        self.operation = threading.Lock()
        self.wakeup = threading.Event()
        self.csrf = secrets.token_urlsafe(32)
        self.state = {"status": "Starting", "last_verified": None, "last_checked": None}

    def poll(self, client, branch):
        with self.operation:
            try:
                candidate = client.poll(branch)
                # A validated candidate is not necessarily a successful installation.
                verified = {key: candidate[key] for key in ("version", "sha256", "commit") if key in candidate}
                with self.lock:
                    self.state["last_verified"] = verified
                status = self.store.install(candidate) if self.store else "Verified dry-run candidate"
                with self.lock:
                    self.state["status"] = status
            except HTTPError as error:
                with self.lock:
                    self.state["status"] = "GitHub access failed (HTTP %d). Check branch and credentials." % error.code
            except Exception:
                with self.lock:
                    self.state["status"] = "Candidate rejected, connection or storage failed. Active release unchanged."
            with self.lock:
                self.state["last_checked"] = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
            self._poll_installer(client, branch)

    def _poll_installer(self, client, branch):
        if not self.installer:
            return
        try:
            self.installer.poll(client, quote(branch, safe=""))
        except HTTPError as error:
            self.installer.candidate = None
            self.installer.grant = None
            self.installer.status = "Deployment unavailable (HTTP %d); inspect pending recovery if any" % error.code
        except Exception as error:
            self.installer.candidate = None
            self.installer.grant = None
            self.installer.status = str(error) if isinstance(error, Blocked) else "Deployment validation, connection or storage failed. Inspect pending recovery."

    def poll_deployment(self, client, branch):
        """Runtime path: the retired dry-run sandbox is no longer polled."""
        with self.operation:
            self._poll_installer(client, branch)

    def action(self, name, expected_commit="", pending_commit=""):
        if name == "check":
            self.wakeup.set()
            return
        with self.operation:
            try:
                if name.startswith(("deploy-", "restart-", "rollback-")):
                    if not self.installer:
                        raise ValueError("Deployment unavailable")
                    try:
                        if name.split("--", 1)[0] == "deploy-replace":
                            self.installer.action(name, expected_commit, pending_commit)
                        else:
                            self.installer.action(name, expected_commit)
                        if name.split("--", 1)[0] == "deploy-resume":
                            self.wakeup.set()
                    except Exception as error:
                        self.installer.status = str(error) if isinstance(error, Blocked) else "Deployment action failed or restart response uncertain. Inspect HA and recovery status."
                    return
                if name == "rollback":
                    status = self.store.rollback(expected_commit)
                elif name == "resume":
                    self.store.set_paused(False)
                    status = "Automatic sandbox updates enabled"
                    self.wakeup.set()
                elif name == "pause":
                    self.store.set_paused(True)
                    status = "Automatic sandbox updates paused"
                else:
                    raise ValueError("Unknown action")
            except Exception:
                status = "Action failed. Refresh and check available recovery versions."
            with self.lock:
                self.state["status"] = status

    def render(self):
        page = '''<!doctype html><html lang="nl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Home Energy Observer</title>
<style>
:root{font-family:Inter,"Segoe UI",Arial,sans-serif;color:#18303a;background:#edf3f2}*{box-sizing:border-box}body{font:15px system-ui,sans-serif;background:#edf3f2;color:#18303a;margin:0;padding:20px}main{max-width:1392px;margin:auto}h1{margin-bottom:8px}p{line-height:1.55}small{color:#648078}.actions{display:flex;flex-wrap:wrap;gap:12px}button{background:#0b685e;color:#fff;border:0;border-radius:8px;padding:10px 15px;font:inherit;cursor:pointer}button:disabled{opacity:.4;cursor:default}code{overflow-wrap:anywhere}li{margin:12px 0}
.observer-app-version{font-size:10px;color:#8ca199;text-align:right;margin:0 0 6px}.observer-overview{margin:0 auto;max-width:1392px}.observer-guidance{border-radius:8px;background:#e7f4ef;color:#145b4b;padding:10px 12px;font-size:12px;font-weight:700}.observer-heading{display:flex;justify-content:space-between;align-items:center;gap:18px;border-bottom:1px solid #d5e2df;padding:3px 0 15px}.observer-heading h2{font-size:26px;letter-spacing:-.04em;margin:2px 0 1px}.observer-heading p,.observer-heading form{margin:0}.observer-heading p{font-size:13px;color:#657e77}.observer-eyebrow{text-transform:uppercase;font-size:10px;letter-spacing:.1em;font-weight:800;color:#748b83}.observer-check{padding:10px 15px;box-shadow:0 5px 14px #0b685e24}.observer-hero{display:grid;grid-template-columns:1.35fr 1fr;gap:14px;margin-top:15px}.observer-alert,.observer-safety{border:1px solid #d5e3df;border-radius:15px;padding:17px 20px;background:#fff;min-width:0}.observer-alert{border-left:4px solid #e9a33c}.observer-alert-clear{border-left-color:#12a77e}.observer-alert h3{font-size:17px;margin:5px 0}.observer-alert p{font-size:13px;color:#556f67;margin:6px 0 12px}.primary-action{font-size:13px;font-weight:750;padding:9px 13px}.primary-action span{margin-left:10px}.observer-safety{background:#0b3f3e;color:#fff}.observer-safety .observer-eyebrow{color:#b4d6ce}.observer-safety h3{font-size:14px;margin:6px 0}.observer-safety p{font-size:12px;margin:7px 0;color:#d2e8e2;overflow-wrap:anywhere}.observer-safety .observer-note,.observer-note{font-size:11px;color:#b8d8d0}.observer-flow,.observer-modules{margin-top:13px;background:#fff;border:1px solid #d5e3df;border-radius:14px;overflow:hidden}.observer-section-head{display:flex;justify-content:space-between;align-items:center;padding:12px 18px 8px;gap:12px}.observer-section-head h3{font-size:15px;margin:0}.observer-section-head span{font-size:11px;color:#748a82}.observer-steps{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;padding:5px 18px 14px}.observer-step{display:flex;align-items:center;gap:8px;color:#81968e;font-size:12px;font-weight:700}.observer-step span{width:23px;height:23px;display:grid;place-items:center;border:1px solid #cbdcd5;border-radius:50%;font-size:11px;flex:none}.observer-step.done span{background:#0c9d7b;color:white;border-color:#0c9d7b}.observer-step.current{color:#104f49}.observer-step.neutral{color:#9aaba4}.observer-step.current span{border-color:#0c9d7b;color:#0c846e}.observer-table-head,.observer-row{display:grid;grid-template-columns:1.7fr .8fr .8fr .8fr 1.15fr;gap:12px;align-items:center;padding:8px 18px}.observer-table-head{background:#f7faf8;color:#7a8e86;font-size:9px;letter-spacing:.09em;text-transform:uppercase;font-weight:800;border-top:1px solid #edf2ef;border-bottom:1px solid #edf2ef}.observer-row{min-height:39px;border-bottom:1px solid #e9f0ec;font-size:12px}.observer-row:last-child{border:0}.observer-module{display:flex;align-items:center;gap:9px}.observer-icon{width:23px;height:23px;display:grid;place-items:center;background:#e8f4ef;color:#18765f;border-radius:6px;font-size:11px;font-weight:800}.observer-status{display:inline-block;padding:4px 7px;border-radius:6px;font-size:10px;font-weight:750}.observer-status.ok{background:#e4f6ed;color:#14735c}.observer-status.warn{background:#fff1d7;color:#96620f}.observer-status.blue{background:#e6f0fa;color:#3c6d92}.observer-status.neutral{background:#edf1ef;color:#657e77}.observer-advanced{margin-top:13px;border:1px solid #d5e3df;border-radius:13px;background:#fff;padding:0 16px 12px}.observer-advanced>summary,.observer-detail>summary{cursor:pointer;font-weight:750;color:#0d685d;padding:12px 2px;font-size:13px}.observer-advanced>.observer-note{color:#71877f;margin:0 0 10px}.observer-detail{border-top:1px solid #e5eeea}.observer-detail>summary{font-size:12px;padding:10px 2px}.observer-detail section{background:#f7faf8;border:1px solid #e2ece7;border-radius:10px;padding:14px;margin:0 0 12px}.observer-detail section h3{margin-top:0}.observer-detail .actions form,.observer-detail form{display:inline-flex;margin:4px}.observer-detail button{font-size:12px;padding:8px 10px}.observer-detail code{font-size:11px}
.observer-access-warning{border:1px solid #ebca91;border-left:4px solid #d59121;border-radius:10px;background:#fff5e5;color:#704c18;padding:11px 15px;margin:0 0 13px}.observer-access-warning p{font-size:12px;margin:3px 0 0}
@media(max-width:760px){body{padding:12px}.observer-heading{align-items:flex-start}.observer-hero{grid-template-columns:1fr}.observer-table-head{display:none}.observer-row{grid-template-columns:1fr 1fr;gap:6px;padding:10px 14px}.observer-row>div:before{content:attr(data-label);display:block;color:#81958e;font-size:9px;text-transform:uppercase;margin-bottom:2px}.observer-module{grid-column:1/-1}.observer-module:before{display:none}.observer-steps{grid-template-columns:1fr;gap:8px}.observer-section-head{align-items:flex-start;flex-direction:column}.observer-check{white-space:nowrap}}
</style></head><body><main><div class="observer-app-version">Observer app ''' + APP_VERSION + ''' · Beheerde integratie-implementatie</div>'''
        if self.installer:
            page += self.installer.render(self.csrf)
        else:
            check = ('<form method="post" action="check"><input type="hidden" name="csrf" value="' + self.csrf +
                     '"><button>Controleer nu</button></form>')
            page += ('<section class="observer-alert observer-alert-clear"><h2>Home Energy Observer</h2>'
                     '<p>Beheert alleen de gecontroleerde Home Energy-integratiebundel. Er worden geen laadopdrachten verstuurd.</p>'
                     '<h3>Releasecontrole</h3><p>Haal de kandidaat op en controleer deze vanaf de ingestelde releasebranch.</p>'
                     + check + '</section>')
        page += "</main></body></html>"
        return page.encode("utf-8")


def make_handler(observer, allowed_peer="172.30.32.2", deployment_admin=""):
    class Handler(BaseHTTPRequestHandler):
        def authorized(self):
            return self.client_address[0] == allowed_peer

        def do_GET(self):
            if not self.authorized():
                self.send_error(403)
                return
            if self.path not in ("/", ""):
                self.send_error(404)
                return
            with observer.operation:
                body = observer.render()
            if observer.installer:
                user_id = self.headers.get("X-Remote-User-Id", "")
                if not user_id:
                    message = ("Home Assistant gaf geen Ingress user ID door. Ververs de pagina via de HA-zijbalk "
                               "terwijl je als beheerder bent ingelogd; beheeracties blijven tot dan geblokkeerd.")
                elif not deployment_admin:
                    message = ("Stel dit Ingress user ID in als deployment_admin_user_id in app Configuration: "
                               + html.escape(user_id))
                elif not secrets.compare_digest(user_id, deployment_admin):
                    message = "Dit Ingress user ID komt niet overeen met de ingestelde beheerder; beheeracties zijn geblokkeerd."
                else:
                    message = ""
                if message:
                    warning = ('<section class="observer-access-warning" role="alert"><strong>'
                               'Beheeracties geblokkeerd</strong><p>' + message + '</p></section>')
                    body = body.replace(b'<main>', b'<main>' + warning.encode(), 1)
                    body = body.replace(b'<button class="primary-action">',
                                        b'<button class="primary-action" disabled aria-disabled="true">')
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'self'")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if not self.authorized():
                self.send_error(403)
                return
            deploy_actions = ("/deploy-install", "/deploy-override", "/deploy-restart", "/restart-override",
                              "/deploy-confirm", "/deploy-rollback", "/rollback-override", "/deploy-recover",
                              "/deploy-pause", "/deploy-resume", "/deploy-replace")
            deploy_actions += tuple("/" + action + "--" + domain
                                    for action in DEPLOY_ACTIONS for domain in MODULES)
            if self.path not in ("/check",) + deploy_actions:
                self.send_error(404)
                return
            if self.path in deploy_actions and (not deployment_admin or not secrets.compare_digest(
                    self.headers.get("X-Remote-User-Id", ""), deployment_admin)):
                self.send_error(403)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 4096:
                    raise ValueError()
                self.connection.settimeout(5)
                fields = parse_qs(self.rfile.read(length).decode("utf-8"), max_num_fields=5)
                if not secrets.compare_digest(fields.get("csrf", [""])[0], observer.csrf):
                    self.send_error(403)
                    return
            except (ValueError, OSError):
                self.send_error(400)
                return
            observer.action(self.path[1:], fields.get("commit", [""])[0],
                            fields.get("pending_commit", [""])[0])
            self.send_response(303)
            # Relative redirect preserves the ingress session prefix.
            self.send_header("Location", "./")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format, *args):
            pass
    return Handler


def main():
    options = json.loads(Path("/data/options.json").read_text())
    client = GitHub(options["repository"], options.get("github_token", ""))
    interval = max(60, min(3600, int(options.get("interval_seconds", 60))))
    admin = options.get("deployment_admin_user_id", "")
    if admin and not re.fullmatch("[0-9a-f]{32}", admin):
        raise SystemExit("deployment_admin_user_id must be the 32-character HA administrator user ID")
    try:
        installer = SuiteInstaller("/data/deployment", "/homeassistant",
                              HomeAssistant(os.environ.get("SUPERVISOR_TOKEN", ""),
                                            options.get("charging_contactor_entity", DEFAULT_CONTACTOR),
                                            options.get("charging_power_entity", DEFAULT_POWER),
                                            options.get("charging_idle_power_watts", DEFAULT_IDLE_POWER_WATTS)),
                              enabled=bool(options.get("enable_deployment", False) and admin))
    except Exception:
        raise SystemExit("Deployment state invalid. Files preserved; repair stored state before restarting.")
    observer = Observer(installer=installer)
    def worker():
        while True:
            observer.wakeup.clear()
            observer.poll_deployment(client, options.get("branch", "production"))
            observer.wakeup.wait(interval)
    threading.Thread(target=worker, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", 8099), make_handler(observer, deployment_admin=admin)).serve_forever()


if __name__ == "__main__":
    main()
