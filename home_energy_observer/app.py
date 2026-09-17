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
from deployment import (Blocked, Installer, HomeAssistant, DEFAULT_CONTACTOR,
                        DEFAULT_POWER, DEFAULT_IDLE_POWER_WATTS)

MAX_BYTES = 1024 * 1024
APP_VERSION = "0.3.4"


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

    def action(self, name, expected_commit=""):
        if name == "check":
            self.wakeup.set()
            return
        with self.operation:
            try:
                if name.startswith(("deploy-", "restart-", "rollback-")):
                    if not self.installer:
                        raise ValueError("Deployment unavailable")
                    try:
                        self.installer.action(name, expected_commit)
                        if name == "deploy-resume":
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
        check = ('<form method="post" action="check"><input type="hidden" name="csrf" value="' + self.csrf +
                 '"><button>Check now</button></form>')
        page = '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Home Energy</title>
<style>body{font:16px system-ui,sans-serif;background:#101923;color:#e4edf6;margin:0;padding:32px}main{max-width:900px;margin:auto}section{background:#1b2938;padding:24px;border-radius:12px;margin:20px 0}h1{margin-bottom:8px}p{line-height:1.6}small{color:#b4c5d5}.actions{display:flex;flex-wrap:wrap;gap:12px}button{background:#80d8ff;color:#10202c;border:0;border-radius:6px;padding:12px 18px;font:inherit;cursor:pointer}button:disabled{opacity:.4;cursor:default}code{overflow-wrap:anywhere}li{margin:12px 0}</style></head><body><main>
<h1>Home Energy</h1><small>App ''' + APP_VERSION + ''' · Managed integration deployment</small>
<p>Manages only the verified Home Energy integration bundle. No charging commands are sent.</p>
<section><h2>Deployment check</h2><p>Fetch and verify the current candidate from the configured release branch.</p><div class="actions">''' + check + '''</div></section></main></body></html>'''
        if self.installer:
            page = page.replace("</main>", self.installer.render(self.csrf) + "</main>")
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
                identity = html.escape(self.headers.get("X-Remote-User-Id", "not supplied"))
                body = body.replace(b"</main>", ("<section><p>Ingress user ID: <code>" + identity + "</code>. Configure your administrator ID once in app Configuration.</p></section></main>").encode())
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
                              "/deploy-pause", "/deploy-resume")
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
            observer.action(self.path[1:], fields.get("commit", [""])[0])
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
        installer = Installer("/data/deployment", "/homeassistant",
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
