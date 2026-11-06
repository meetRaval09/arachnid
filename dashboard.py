#!/usr/bin/env python3
"""
ARACHNID - DASHBOARD  (control the crawler from your browser)
=============================================================
    python dashboard.py [project folder] [--port 8765] [--no-browser]

Opens a page on YOUR computer only (http://127.0.0.1:8765).  From it you can:
    * start a scan, the safe fixes, undo, and the extra checks (page weight, memory, outside connections, ideas)
    * read the standard report - alarms, decisions, handled, ideas - with who should look at each one
    * approve or reject the crawler's suggestions, then apply the approved ones (always through the safety gate)
    * watch the crawler's output live while it works

Safety
    * The page is only reachable from this computer (127.0.0.1), never from the network.
    * Every action needs a secret key that is created fresh each time the dashboard starts and is only known to the page
      it served - another website open in your browser cannot press the buttons for you.
    * Only one job runs at a time (fix and undo share one history, so two at once would mix them up).
    * The dashboard runs exactly the same commands you would type - fix still goes through the safety gate,
      suggestions still need your approval, undo still restores byte for byte.  Nothing new is allowed here.
"""
import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import select
import socket
import struct
import urllib.error
import urllib.request
import zipfile
import io
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import report  # noqa: E402

PAGE = HERE / "dashboard.html"
SETUP_PAGE = HERE / "setup.html"
STATE = HERE / "reports" / "dashboard-state.json"
EVENTS = HERE / "reports" / "live-events.jsonl"
SKIP_TREE = {"node_modules", ".git", "dist", "build", "__pycache__", ".wrangler", ".vite", ".next", ".cache", ".claude",
             "arachnid", ".venv", "venv", "backups", "reports", ".pytest_cache", "coverage"}
MAX_TREE = 4000
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
PY = sys.executable

# command name -> (arguments for arachnid.py, needs the project folder, plain-words label)
COMMANDS = {
    "scan": (["scan"], True, "Scan"),
    "fix": (["fix"], True, "Fix what is safe"),
    "undo": (["undo"], False, "Undo the last change"),
    "netcheck": (["netcheck"], True, "Check outside connections"),
    "size": (["size"], True, "Measure page weight"),
    "leaks": (["leaks"], True, "Measure memory growth"),
    "advise": (["advise"], True, "Look for lighter / faster code"),
    "insights": (["insights"], True, "Look for dead code and ideas"),
    "apply": (["apply-approved"], True, "Apply approved suggestions"),
    "selftest": (["selftest"], False, "Run the self-test"),
}
NEEDS_RESCAN = {"fix", "undo", "apply"}       # after these the old findings may be out of date


class Job:
    def __init__(self):
        self.lock = threading.Lock()
        self.name = None
        self.label = None
        self.started = None
        self.ended = None
        self.code = None
        self.lines = []
        self.proc = None
        self.run_id = 0
        self.reason = {}
        self.history = []            # (run number, command) of every job, so the watcher can tell what happened

    def names_since(self, run):
        with self.lock:
            return [n for r, n in self.history if r > (run or 0)]

    def snapshot(self, since=0):
        with self.lock:
            return {"run": self.run_id, "name": self.name, "label": self.label, "reason": self.reason, "running": self.proc is not None and self.ended is None,
                    "started": self.started, "ended": self.ended, "code": self.code,
                    "lines": self.lines[since:], "next": len(self.lines)}

    def start(self, name, root, label=None, reason=None):
        args, needs_root, default_label = COMMANDS[name]
        label = label or default_label
        with self.lock:
            if self.proc is not None and self.ended is None:
                return False, "The crawler is already busy with: %s. Wait for it to finish." % self.label
            self.name, self.label, self.started, self.ended, self.code, self.lines = name, label, time.time(), None, None, []
            self.reason = reason or {}
            cmd = [PY, str(HERE / "arachnid.py")] + args + ([str(root)] if needs_root else [])
            env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
            # the live trail: every tool writes "I am reading this file now" into EVENTS (see live/sitecustomize.py)
            EVENTS.parent.mkdir(parents=True, exist_ok=True)
            EVENTS.write_text("", encoding="utf-8")
            self.run_id += 1
            self.history.append((self.run_id, name))
            del self.history[:-50]
            env.update(ARACHNID_EVENTS=str(EVENTS), ARACHNID_LIVE_ROOT=str(root), ARACHNID_SELF=str(HERE),
                       PYTHONPATH=os.pathsep.join([str(HERE / "live")] + ([os.environ["PYTHONPATH"]] if os.environ.get("PYTHONPATH") else [])))
            self.proc = subprocess.Popen(cmd, cwd=str(HERE), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                         encoding="utf-8", errors="replace", env=env, bufsize=1)
        threading.Thread(target=self._pump, args=(name, root), daemon=True).start()
        return True, label

    def _pump(self, name, root):
        p = self.proc
        for line in p.stdout:
            with self.lock:
                self.lines.append(line.rstrip("\n"))
                if len(self.lines) > 6000:              # keep memory small on very long runs
                    del self.lines[:1000]
        p.wait()
        with self.lock:
            self.code, self.ended = p.returncode, time.time()
        st = read_state()
        st.update({"root": str(root), "last_%s" % name: datetime.now().isoformat(timespec="seconds")})
        if name == "scan":
            st["stale"] = False
        elif name in NEEDS_RESCAN:
            st["stale"] = True
        write_state(st)


def read_state():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_state(st):
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(st, indent=2), encoding="utf-8")
    except OSError:
        pass


def proposals():
    try:
        d = json.loads((HERE / "reports" / "proposals.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for pid, p in d.items():
        out.append({"id": pid, "status": p.get("status"), "title": p.get("title", ""), "file": p.get("file", ""), "line": p.get("line", 0),
                    "benefit": p.get("benefit", ""), "risk": p.get("risk", ""), "confidence": p.get("confidence", ""),
                    "exact": bool(p.get("patches")), "tool": p.get("tool", ""), "held_why": p.get("held_why", ""),
                    "root": p.get("root", "")})
    order = {"pending": 0, "approved": 1, "held": 2, "applied": 3, "rejected": 4}
    out.sort(key=lambda x: (order.get(x["status"], 9), x["file"], x["line"]))
    return out


def project_tree(root):
    """Every file the crawler would visit, as project-relative paths (folders first, A-Z) - the map the spider walks."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_TREE and not os.path.islink(os.path.join(dirpath, d)))
        rel = os.path.relpath(dirpath, root).replace("\\", "/")
        for f in sorted(filenames):
            if f in IGNORE_NAMES or f.endswith(IGNORE_ENDS) or f.startswith((".#", "~$")):
                continue                                   # editor scratch files are not part of the project
            out.append(f if rel == "." else rel + "/" + f)
            if len(out) >= MAX_TREE:
                return out, True
    return out, False


def read_events(offset):
    """New lines of the live trail since `offset` (bytes).  Returns (events, new offset)."""
    try:
        with open(EVENTS, "rb") as fh:
            fh.seek(offset)
            data = fh.read(400_000)
    except OSError:
        return [], offset
    cut = data.rfind(b"\n")
    if cut < 0:
        return [], offset
    evs = []
    for line in data[:cut].splitlines():
        try:
            evs.append(json.loads(line))
        except ValueError:
            pass
    return evs, offset + cut + 1


# ------------------------------------------------------------------------------------------------- GitHub (read only)
class GitHub:
    """Reads the Arachnid runs of this project on GitHub.  It only READS.  The key (token) is taken from the
    GITHUB_TOKEN / GH_TOKEN setting or from the GitHub command-line tool (`gh auth token`), kept in memory only,
    and never sent to the browser."""
    API = os.environ.get("ARACHNID_GITHUB_API", "https://api.github.com")      # GitHub Enterprise: https://<host>/api/v3

    def __init__(self, root, repo=None, workflow="arachnid.yml"):
        self.repo = repo or self._detect(root)
        self.workflow = workflow
        self._token = None
        self._token_checked = False
        self.cache = {}

    @staticmethod
    def _detect(root):
        try:
            url = subprocess.run(["git", "-C", str(root), "remote", "get-url", "origin"], capture_output=True, text=True, timeout=10).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            return None
        m = re.search(r"github\.com[:/]([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$", url)
        return "%s/%s" % (m.group(1), m.group(2)) if m else None

    def token(self):
        if not self._token_checked:
            self._token_checked = True
            self._token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
            if not self._token:
                try:
                    r = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10)
                    self._token = r.stdout.strip() or None
                except (OSError, subprocess.TimeoutExpired):
                    self._token = None
        return self._token

    def get(self, path, raw=False, max_age=0):
        hit = self.cache.get(path)
        if max_age and hit and time.time() - hit[0] < max_age:
            return hit[1]
        req = urllib.request.Request(self.API + path, headers={"Accept": "application/vnd.github+json", "User-Agent": "arachnid-dashboard",
                                                               "X-GitHub-Api-Version": "2022-11-28"})
        if self.token():
            req.add_header("Authorization", "Bearer " + self.token())
        with urllib.request.urlopen(req, timeout=20) as r:
            data = r.read() if raw else json.loads(r.read().decode("utf-8"))
        if max_age:
            self.cache[path] = (time.time(), data)
        return data

    def download(self, url):
        """Artifact zips live behind a redirect to storage; the key must NOT be sent to that second address."""
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        opener = urllib.request.build_opener(NoRedirect)
        req = urllib.request.Request(url, headers={"User-Agent": "arachnid-dashboard", "Authorization": "Bearer " + (self.token() or "")})
        try:
            with opener.open(req, timeout=30) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308) and e.headers.get("Location"):
                with urllib.request.urlopen(urllib.request.Request(e.headers["Location"], headers={"User-Agent": "arachnid-dashboard"}), timeout=60) as r:
                    return r.read()
            raise

    def status(self):
        if not self.repo:
            return {"ok": False, "why": "no-repo", "message": "This folder is not linked to a GitHub repository (no 'origin' remote). Start the dashboard with --repo owner/name."}
        try:
            runs = self.get("/repos/%s/actions/workflows/%s/runs?per_page=8" % (self.repo, self.workflow), max_age=8 if self.token() else 25)
        except urllib.error.HTTPError as e:
            msg = {401: "GitHub did not accept the key. Log in again with: gh auth login",
                   403: "GitHub is limiting requests right now. Log in with 'gh auth login' (or set GITHUB_TOKEN) for a higher limit.",
                   404: "No Arachnid workflow found in %s. Copy github/arachnid.yml to .github/workflows/ in that repository first." % self.repo}.get(e.code, "GitHub answered %s." % e.code)
            return {"ok": False, "why": "http-%d" % e.code, "message": msg, "repo": self.repo}
        except (urllib.error.URLError, OSError, ValueError) as e:
            return {"ok": False, "why": "offline", "message": "Could not reach GitHub (%s)." % str(e)[:80], "repo": self.repo}
        out = []
        for r in runs.get("workflow_runs", [])[:8]:
            item = {"id": r["id"], "status": r["status"], "conclusion": r.get("conclusion"), "event": r["event"],
                    "branch": r.get("head_branch"), "title": r.get("display_title") or "", "actor": (r.get("actor") or {}).get("login"),
                    "sha": (r.get("head_sha") or "")[:7], "url": r.get("html_url"), "started": r.get("run_started_at"), "updated": r.get("updated_at")}
            out.append(item)
        live = next((x for x in out if x["status"] != "completed"), None)
        if live:
            try:
                jobs = self.get("/repos/%s/actions/runs/%d/jobs" % (self.repo, live["id"]), max_age=5)
                live["jobs"] = [{"name": j["name"], "status": j["status"], "conclusion": j.get("conclusion"),
                                 "steps": [{"name": s_["name"], "status": s_["status"], "conclusion": s_.get("conclusion")} for s_ in j.get("steps", [])]}
                                for j in jobs.get("jobs", [])]
            except Exception:
                live["jobs"] = []
        return {"ok": True, "repo": self.repo, "signed_in": bool(self.token()), "runs": out}

    def replay(self, run_id):
        arts = self.get("/repos/%s/actions/runs/%d/artifacts" % (self.repo, run_id), max_age=60)
        art = next((a for a in arts.get("artifacts", []) if a["name"] in ("arachnid-reports", "arachnid-fix-reports") and not a.get("expired")), None)
        if not art:
            return {"ok": False, "message": "This run has no saved reports (they are kept for 14 days)."}
        if not self.token():
            return {"ok": False, "message": "Downloading a run's reports needs you to be logged in: gh auth login (or set GITHUB_TOKEN)."}
        blob = self.download(art["archive_download_url"])
        z = zipfile.ZipFile(io.BytesIO(blob))
        rep, evs = None, []
        for name in z.namelist():
            base = name.rsplit("/", 1)[-1]
            if base == "arachnid-report.json":
                rep = json.loads(z.read(name).decode("utf-8", "replace"))
            elif base == "live-events.jsonl":
                for line in z.read(name).decode("utf-8", "replace").splitlines():
                    try:
                        evs.append(json.loads(line))
                    except ValueError:
                        pass
        return {"ok": True, "report": rep, "events": evs[:20000]}


# ------------------------------------------------------------------------------------------------- WebSocket (tiny, standard library only)
def ws_accept(key):
    return base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()


def ws_frame(text):
    data = text.encode("utf-8")
    n = len(data)
    if n < 126:
        head = struct.pack("!BB", 0x81, n)
    elif n < 65536:
        head = struct.pack("!BBH", 0x81, 126, n)
    else:
        head = struct.pack("!BBQ", 0x81, 127, n)
    return head + data


def ws_read_frame(sock):
    """Reads one frame from the browser. Returns the opcode (8 = close, 9 = ping) or None if the connection ended."""
    def recv(n):
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError
            buf += chunk
        return buf
    b1, b2 = recv(2)
    op, n = b1 & 0x0F, b2 & 0x7F
    if n == 126:
        n = struct.unpack("!H", recv(2))[0]
    elif n == 127:
        n = struct.unpack("!Q", recv(8))[0]
    if n > 1 << 20:
        raise ConnectionError
    mask = recv(4) if b2 & 0x80 else b""
    recv(n)
    return op


# ------------------------------------------------------------------------------------------------- watch mode
IGNORE_NAMES = (".DS_Store", "Thumbs.db", "desktop.ini")
IGNORE_ENDS = ("~", ".swp", ".swo", ".tmp", ".temp", ".crdownload", ".part", ".pyc")


def snapshot_files(root):
    """{relative path: (size, modified time)} of every file the crawler would look at."""
    snap = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_TREE and not os.path.islink(os.path.join(dirpath, d))]
        rel = os.path.relpath(dirpath, root).replace("\\", "/")
        for f in filenames:
            if f in IGNORE_NAMES or f.endswith(IGNORE_ENDS) or f.startswith((".#", "~$")):
                continue                                   # editor scratch files, not real changes
            p = os.path.join(dirpath, f)
            try:
                st = os.stat(p)
            except OSError:
                continue
            snap[f if rel == "." else rel + "/" + f] = (st.st_size, st.st_mtime_ns)
    return snap


class Watcher:
    """Looks at the project every few seconds.  When files are added or changed, and stay quiet for a moment
    (so a half-saved file or a copy in progress is not scanned too early), it starts a SCAN by itself.
    A scan only reads, so it needs no permission.  Anything that changes files (fix, apply, undo) still needs a person.
    Changes made by Arachnid's own fix / undo are not counted as 'new' - the picture is simply taken again after them."""
    QUIET = 2.0

    def __init__(self, root, job, on=True, interval=2.0):
        self.root, self.job, self.on, self.interval = str(root), job, on, interval
        self.base = snapshot_files(self.root)
        self.pending = {}            # path -> "added" / "changed" / "removed", waiting for the folder to go quiet
        self.last_change = 0.0
        self.last_auto = None        # what the last automatic scan was started for
        self.seen_run = job.snapshot(10 ** 9)["run"]
        threading.Thread(target=self._loop, daemon=True).start()

    def info(self):
        return {"on": self.on, "pending": len(self.pending), "last": self.last_auto}

    def _loop(self):
        while True:
            time.sleep(self.interval)
            try:
                self._tick()
            except Exception:
                pass                 # watching must never take the dashboard down

    def _tick(self):
        snap = self.job.snapshot(10 ** 9)
        if snap["running"]:
            return
        if snap["run"] != self.seen_run:
            # one or more jobs ran since the last look (even a very quick one that started and ended between two looks)
            names = self.job.names_since(self.seen_run)
            self.seen_run = snap["run"]
            if any(n in ("fix", "undo", "apply") for n in names):
                self.base, self.pending = snapshot_files(self.root), {}     # Arachnid's own changes are not "new work"
                return
            if "scan" in names:
                self.pending = {}    # that scan already looked at everything that was waiting
        now = snapshot_files(self.root)
        diff = {}
        for k, v in now.items():
            if k not in self.base:
                diff[k] = "added"
            elif self.base[k] != v:
                diff[k] = "changed"
        for k in self.base:
            if k not in now:
                diff[k] = "removed"
        self.base = now
        if diff:
            self.pending.update(diff)
            self.last_change = time.time()
            return
        if self.pending and self.on and time.time() - self.last_change >= self.QUIET:
            changes = dict(self.pending)
            self.pending = {}
            n_add = sum(1 for v in changes.values() if v == "added")
            n_chg = sum(1 for v in changes.values() if v == "changed")
            n_rm = sum(1 for v in changes.values() if v == "removed")
            words = ", ".join(w for w in ("%d new" % n_add if n_add else "", "%d changed" % n_chg if n_chg else "",
                                          "%d removed" % n_rm if n_rm else "") if w)
            label = "Automatic scan (%s file%s)" % (words, "" if len(changes) == 1 else "s")
            files = sorted(changes.items())[:500]
            self.last_auto = {"at": time.time(), "label": label, "files": files}
            self.job.start("scan", self.root, label=label, reason={"auto": True, "files": files})


def make_handler(root, token, job, gh, watcher=None, setup=None):
    page_cache = {}

    class H(BaseHTTPRequestHandler):
        server_version = "Arachnid"

        def log_message(self, *a):
            pass

        def _host_ok(self):
            host = (self.headers.get("Host") or "").split(":")[0]
            return host in ("127.0.0.1", "localhost")           # blocks "DNS rebinding" tricks from other websites

        def _send(self, code, body, ctype="application/json; charset=utf-8"):
            data = body if isinstance(body, bytes) else (json.dumps(body, ensure_ascii=False) if not isinstance(body, str) else body).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if not self._host_ok():
                return self._send(403, {"error": "wrong address"})
            path = self.path.split("?")[0]
            if path in ("/", "/index.html", "/setup"):
                page = SETUP_PAGE if path == "/setup" else PAGE
                mt = page.stat().st_mtime_ns                    # read the page again whenever the file has been updated,
                if page_cache.get(str(page), (None,))[0] != mt:  # so a new version shows up with a simple browser refresh
                    page_cache[str(page)] = (mt, page.read_text(encoding="utf-8"))
                html = page_cache[str(page)][1].replace("__ARACHNID_TOKEN__", token)
                self.send_response(200)
                data = html.encode("utf-8")
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                                 "font-src https://fonts.gstatic.com; script-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self' ws://127.0.0.1:* ws://localhost:*; frame-ancestors 'none'")
                self.end_headers()
                return self.wfile.write(data)
            if path == "/api/state":
                st = read_state()
                try:
                    rep = report.build(root) if (HERE / "reports" / "ledger.jsonl").exists() else None
                except Exception as e:          # a broken ledger must not take the dashboard down
                    rep, st["report_error"] = None, str(e)[:200]
                return self._send(200, {"root": str(root), "project": os.environ.get("ARACHNID_PROJECT_NAME") or Path(root).name, "report": rep, "proposals": proposals(),
                                        "job": job.snapshot(0), "state": st,
                                        "same_project": st.get("root") in (None, str(root)),
                                        "commands": {k: v[2] for k, v in COMMANDS.items()},
                                        "watch": watcher.info() if watcher else None})
            if path == "/ws":
                return self._websocket()
            if path == "/api/tree":
                files, cut = project_tree(root)
                return self._send(200, {"files": files, "cut": cut})
            if path == "/api/github":
                return self._send(200, gh.status())
            if path == "/api/github/replay":
                try:
                    rid = int(self.path.split("run=")[1].split("&")[0])
                    return self._send(200, gh.replay(rid))
                except (IndexError, ValueError):
                    return self._send(400, {"ok": False, "message": "which run?"})
                except Exception as e:
                    return self._send(200, {"ok": False, "message": "Could not fetch that run's reports: %s" % str(e)[:120]})
            if path.startswith("/api/setup/") and setup is not None:
                return self._setup_get(path[len("/api/setup/"):])
            if path == "/api/job":
                try:
                    since = int(self.path.split("since=")[1].split("&")[0]) if "since=" in self.path else 0
                except ValueError:
                    since = 0
                snap = job.snapshot(since)
                try:
                    ev = int(self.path.split("ev=")[1].split("&")[0]) if "ev=" in self.path else 0
                except ValueError:
                    ev = 0
                snap["events"], snap["ev_next"] = read_events(ev)      # the live trail, for browsers without WebSocket
                return self._send(200, snap)
            return self._send(404, {"error": "not found"})

        def _setup_call(self, fn):
            import setup_api
            try:
                return self._send(200, fn())
            except setup_api.GitHubError as e:
                msg = {401: "GitHub did not accept the key - sign in again.", 403: "GitHub refused (missing permission or rate limit): %s" % e,
                       404: "Not found on GitHub (or no permission): %s" % e, 422: "GitHub could not do that: %s" % e}.get(e.code, str(e))
                return self._send(200, {"ok": False, "message": msg})
            except (urllib.error.URLError, OSError) as e:
                return self._send(200, {"ok": False, "message": "Could not reach GitHub (%s)." % str(e)[:100]})
            except Exception as e:                                # never let the page see a raw crash
                return self._send(200, {"ok": False, "message": "Something went wrong: %s" % str(e)[:160]})

        def _setup_get(self, what):
            if what == "state":
                return self._setup_call(setup.state)
            if what == "repos":
                return self._setup_call(setup.repos)
            if what == "people":
                return self._setup_call(setup.people)
            return self._send(404, {"error": "not found"})

        def _setup_post(self, what, body):
            acts = {
                "login-cli": setup.login_cli, "logout": setup.logout, "device-start": setup.device_start, "device-poll": setup.device_poll,
                "login-token": lambda: setup.login_token(body.get("token")),
                "repo": lambda: setup.choose_repo(str(body.get("repo", ""))),
                "test-email": lambda: setup.test_email(body.get("mail") or {}, body.get("password"), str(body.get("to", ""))),
                "preview": lambda: {"ok": True, "files": setup.build_files(body.get("plan") or {})},
                "finish": lambda: setup.finish(body.get("plan") or {}, body.get("password")),
            }
            if what not in acts:
                return self._send(404, {"error": "not found"})
            res = self._setup_call(acts[what])
            if setup.token:                                     # the GitHub panel can use the same login
                gh._token, gh._token_checked = setup.token, True
                if setup.repo:
                    gh.repo = setup.repo
            return res

        def _websocket(self):
            """Live channel: pushes the trail (which file is being read), the crawler's output and the job state."""
            q = self.path.split("?", 1)[1] if "?" in self.path else ""
            given = dict(p.split("=", 1) for p in q.split("&") if "=" in p).get("token", "")
            origin = self.headers.get("Origin", "")
            host = self.headers.get("Host", "")
            if not secrets.compare_digest(given, token) or origin not in ("http://" + host,) or self.headers.get("Upgrade", "").lower() != "websocket":
                return self._send(403, {"error": "not allowed"})
            key = self.headers.get("Sec-WebSocket-Key", "")
            self.send_response(101, "Switching Protocols")
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", ws_accept(key))
            self.end_headers()
            self.wfile.flush()
            sock = self.connection
            sock.settimeout(None)
            ev_off, log_next, last_job, run_seen, last_watch = 0, 0, None, None, None
            try:
                while True:
                    snap = job.snapshot(log_next)
                    if run_seen is None and not snap["running"]:
                        # the page opened while nothing is running: the trail on disk is an OLD job's - do not replay it
                        run_seen = snap["run"]
                        try:
                            ev_off = EVENTS.stat().st_size
                        except OSError:
                            ev_off = 0
                        log_next = snap["next"]
                        sock.sendall(ws_frame(json.dumps({"type": "reset", "run": run_seen, "idle": True})))
                    if snap["run"] != run_seen:                      # a new job started: start reading from the top
                        run_seen, ev_off, log_next = snap["run"], 0, 0
                        snap = job.snapshot(0)
                        sock.sendall(ws_frame(json.dumps({"type": "reset", "run": run_seen})))
                    msgs = []
                    evs, ev_off = read_events(ev_off)
                    if evs:
                        msgs.append({"type": "trail", "events": evs})
                    if snap["lines"]:
                        msgs.append({"type": "log", "lines": snap["lines"]})
                        log_next = snap["next"]
                    js = {k: snap[k] for k in ("run", "name", "label", "running", "started", "ended", "code", "reason")}
                    if js != last_job:
                        msgs.append(dict(js, type="job"))
                        last_job = js
                    if watcher is not None:
                        wi = watcher.info()
                        if wi != last_watch:
                            msgs.append(dict(wi, type="watch"))
                            last_watch = wi
                    for m in msgs:
                        sock.sendall(ws_frame(json.dumps(m, ensure_ascii=False)))
                    r, _, _ = select.select([sock], [], [], 0.12)
                    if r:
                        op = ws_read_frame(sock)
                        if op == 8:
                            break
            except (ConnectionError, OSError, ValueError):
                pass
            self.close_connection = True

        def do_POST(self):
            if not self._host_ok() or not secrets.compare_digest(self.headers.get("X-Arachnid-Token", ""), token):
                return self._send(403, {"error": "This action needs the dashboard's key. Reload the page."})
            try:
                n = min(int(self.headers.get("Content-Length") or 0), 262144)
                body = json.loads(self.rfile.read(n) or b"{}")
            except (ValueError, OSError):
                return self._send(400, {"error": "bad request"})
            path = self.path.split("?")[0]
            if path == "/api/run":
                name = body.get("command")
                if name not in COMMANDS:
                    return self._send(400, {"error": "unknown command"})
                ok, msg = job.start(name, root)
                return self._send(200 if ok else 409, {"ok": ok, "message": msg})
            if path.startswith("/api/setup/") and setup is not None:
                return self._setup_post(path[len("/api/setup/"):], body)
            if path == "/api/watch":
                if watcher is None:
                    return self._send(400, {"error": "watching is not available"})
                watcher.on = bool(body.get("on"))
                if watcher.on:                                  # start fresh: changes made while it was off are not lost -
                    watcher.last_change = time.time()            # they are still in 'pending' and get scanned after the quiet moment
                return self._send(200, {"ok": True, "on": watcher.on})
            if path == "/api/proposals":
                action, ids = body.get("action"), [i for i in body.get("ids", []) if isinstance(i, str) and i.startswith("P-")][:200]
                if action not in ("approve", "reject") or not ids:
                    return self._send(400, {"error": "say approve or reject, and which suggestions"})
                if job.snapshot()["running"]:
                    return self._send(409, {"error": "The crawler is busy. Try again when it has finished."})
                r = subprocess.run([PY, str(HERE / "proposals.py"), action] + ids, capture_output=True, text=True, errors="replace", cwd=str(HERE))
                return self._send(200, {"ok": r.returncode == 0, "message": (r.stdout + r.stderr).strip()[-400:]})
            return self._send(404, {"error": "not found"})

    return H


def main():
    ap = argparse.ArgumentParser(description="Arachnid dashboard")
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--host", default="127.0.0.1", help="address to listen on (Docker uses 0.0.0.0 inside the container; "
                                                          "the port is still published to this computer only)")
    ap.add_argument("--no-watch", action="store_true", help="do not scan by itself when files are added or changed")
    ap.add_argument("--repo", default=None, help="GitHub repository (owner/name) whose Arachnid runs to show; found from 'origin' if left out")
    a = ap.parse_args()
    root = Path(a.root).resolve()
    if not root.is_dir():
        sys.exit("That folder does not exist: %s" % root)
    token = secrets.token_urlsafe(24)
    job = Job()
    gh = GitHub(root, a.repo)
    watcher = Watcher(root, job, on=not a.no_watch)
    import setup_api
    setup = setup_api.Setup(root, a.repo)
    srv = None
    for port in range(a.port, a.port + 20):
        try:
            srv = ThreadingHTTPServer((a.host, port), make_handler(root, token, job, gh, watcher, setup))
            break
        except OSError:
            continue
    if srv is None:
        sys.exit("Could not find a free port near %d." % a.port)
    url = "http://127.0.0.1:%d/" % srv.server_address[1]      # (inside Docker too: that is the address on YOUR computer)
    print("Arachnid dashboard for %s\n  open: %s\n  (only this computer can open it; press Ctrl+C here to stop)" % (root, url), flush=True)
    if not a.no_watch:
        print("  watching for new and changed files - each time, a scan starts by itself (scans only read)", flush=True)
    if not a.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
