#!/usr/bin/env python3
"""
ARACHNID - SETUP  (the brains behind the dashboard's setup page: /setup)
=======================================================================
Connects a project to GitHub and writes everything a team needs, in one go:
    1  sign in to GitHub     - with the GitHub command-line tool's login, GitHub's own device sign-in, or a token
    2  teams and owners      - reads the organisation's teams, lets you give folders to teams  -> .github/CODEOWNERS
    3  e-mail                - one address for the whole team, or one per team; mail server presets; a test e-mail
    4  who gets what         - alarms / decisions -> owners, the person who changed it, always-receivers
    5  finish                - ONE pull request with .github/workflows/arachnid.yml, .github/CODEOWNERS and
                               arachnid.config.json (never straight into main), plus the mail server secrets

What is kept where
    * The GitHub key lives in this program's memory only (or comes from the GitHub CLI's own store). Never in a file.
    * The mail password is used for the test e-mail and then sent to GitHub's encrypted secrets
      (needs the small PyNaCl library: pip install pynacl). Without PyNaCl you get links to paste it yourself.
    * arachnid.config.json holds only addresses and rules - never a password.
"""
import base64
import json
import re
import os
import smtplib
import ssl
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage
from pathlib import Path

HERE = Path(__file__).resolve().parent
API = os.environ.get("ARACHNID_GITHUB_API", "https://api.github.com")
WEB = os.environ.get("ARACHNID_GITHUB_WEB", "https://github.com")
CLIENT_ID = os.environ.get("ARACHNID_GITHUB_CLIENT_ID", "")         # an OAuth app's public id enables "Sign in with GitHub"
SCOPES = "repo workflow read:org"
BRANCH = "arachnid/setup"
MAIL_PRESETS = {
    "outlook": {"label": "Outlook / Microsoft 365", "host": "smtp.office365.com", "port": 587, "tls": "starttls"},
    "gmail": {"label": "Gmail / Google Workspace", "host": "smtp.gmail.com", "port": 587, "tls": "starttls"},
    "zoho": {"label": "Zoho Mail", "host": "smtp.zoho.com", "port": 587, "tls": "starttls"},
    "other": {"label": "Other", "host": "", "port": 587, "tls": "starttls"},
}
SECRET_NAMES = ["ARACHNID_SMTP_HOST", "ARACHNID_SMTP_PORT", "ARACHNID_SMTP_USER", "ARACHNID_SMTP_PASSWORD", "ARACHNID_MAIL_FROM"]


class GitHubError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


def has_nacl():
    try:
        import nacl.public  # noqa: F401
        return True
    except ImportError:
        return False


class Setup:
    def __init__(self, root, repo=None):
        self.root = Path(root)
        self.token = None
        self.token_source = None
        self.user = None
        self.scopes = []
        self.repo = repo or self._detect_repo()
        self.device = None

    # ------------------------------------------------------------------ GitHub plumbing
    def _detect_repo(self):
        try:
            url = subprocess.run(["git", "-C", str(self.root), "remote", "get-url", "origin"], capture_output=True, text=True, timeout=10).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            return None
        import re
        m = re.search(r"github\.com[:/]([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$", url)
        return "%s/%s" % (m.group(1), m.group(2)) if m else None

    def call(self, method, path, body=None, headers_out=None):
        url = path if path.startswith("http") else API + path
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Accept": "application/vnd.github+json", "User-Agent": "arachnid-setup", "X-GitHub-Api-Version": "2022-11-28"})
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if self.token:
            req.add_header("Authorization", "Bearer " + self.token)
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                if headers_out is not None:
                    headers_out.update({k.lower(): v for k, v in r.headers.items()})
                raw = r.read()
                return json.loads(raw.decode("utf-8")) if raw else {}
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read().decode("utf-8")).get("message", "")
            except Exception:
                msg = ""
            raise GitHubError(e.code, msg or "GitHub answered %d" % e.code)

    def _use_token(self, token, source):
        self.token = token
        hdr = {}
        try:
            u = self.call("GET", "/user", headers_out=hdr)
        except GitHubError as e:
            self.token = None
            return {"ok": False, "message": "GitHub did not accept that key (%s)." % e}
        self.user, self.token_source = u.get("login"), source
        self.scopes = [x.strip() for x in hdr.get("x-oauth-scopes", "").split(",") if x.strip()]
        return {"ok": True, "user": self.user, "scopes": self.scopes}

    # ------------------------------------------------------------------ step 1: sign in
    def login_cli(self):
        try:
            tok = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=15).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            tok = ""
        if not tok:
            return {"ok": False, "message": "The GitHub command-line tool is not installed or not logged in."}
        return self._use_token(tok, "gh")

    def login_token(self, token):
        token = (token or "").strip()
        if not token or len(token) > 255:
            return {"ok": False, "message": "That does not look like a GitHub key."}
        return self._use_token(token, "token")

    def device_start(self):
        if not CLIENT_ID:
            return {"ok": False, "message": "GitHub sign-in is not configured (no ARACHNID_GITHUB_CLIENT_ID)."}
        body = urllib.parse.urlencode({"client_id": CLIENT_ID, "scope": SCOPES}).encode()
        req = urllib.request.Request(WEB + "/login/device/code", data=body, headers={"Accept": "application/json", "User-Agent": "arachnid-setup"})
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.loads(r.read().decode())
        if "device_code" not in d:
            return {"ok": False, "message": d.get("error_description") or "GitHub did not start the sign-in."}
        self.device = {"code": d["device_code"], "interval": int(d.get("interval", 5)), "until": time.time() + int(d.get("expires_in", 900))}
        return {"ok": True, "user_code": d["user_code"], "url": d.get("verification_uri", WEB + "/login/device"), "interval": self.device["interval"]}

    def device_poll(self):
        if not self.device:
            return {"ok": False, "state": "none"}
        if time.time() > self.device["until"]:
            self.device = None
            return {"ok": False, "state": "expired", "message": "The code expired. Start again."}
        body = urllib.parse.urlencode({"client_id": CLIENT_ID, "device_code": self.device["code"],
                                       "grant_type": "urn:ietf:params:oauth:grant-type:device_code"}).encode()
        req = urllib.request.Request(WEB + "/login/oauth/access_token", data=body, headers={"Accept": "application/json", "User-Agent": "arachnid-setup"})
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.loads(r.read().decode())
        if d.get("access_token"):
            self.device = None
            res = self._use_token(d["access_token"], "device")
            res["state"] = "done" if res["ok"] else "error"
            return res
        err = d.get("error", "")
        if err in ("authorization_pending", "slow_down"):
            return {"ok": False, "state": "waiting"}
        self.device = None
        return {"ok": False, "state": "error", "message": d.get("error_description") or err}

    def logout(self):
        self.token = self.user = self.token_source = None
        self.scopes = []
        return {"ok": True}

    # ------------------------------------------------------------------ what the page shows
    def state(self):
        cfg = load_json(self.root / "arachnid.config.json")
        st = {"signed_in": bool(self.token), "user": self.user, "source": self.token_source, "scopes": self.scopes,
              "repo": self.repo, "device_available": bool(CLIENT_ID), "nacl": has_nacl(), "presets": MAIL_PRESETS,
              "config": cfg, "codeowners": read_codeowners(self.root), "folders": folders(self.root),
              "workflow_local": (self.root / ".github" / "workflows" / "arachnid.yml").is_file(),
              "tool_in_project": (self.root / "arachnid" / "arachnid.py").is_file(),
              "token_url": WEB + "/settings/tokens/new?scopes=" + urllib.parse.quote("repo,workflow,read:org") + "&description=Arachnid"}
        return st

    def repos(self):
        out = []
        for page in (1, 2, 3):
            got = self.call("GET", "/user/repos?per_page=100&sort=pushed&page=%d" % page)
            out += [{"name": r["full_name"], "private": r.get("private"), "default_branch": r.get("default_branch")} for r in got]
            if len(got) < 100:
                break
        return {"ok": True, "repos": out}

    def choose_repo(self, name):
        try:
            r = self.call("GET", "/repos/" + name)
        except GitHubError as e:
            return {"ok": False, "message": "Could not open %s (%s)." % (name, e)}
        self.repo = r["full_name"]
        return {"ok": True, "repo": self.repo, "default_branch": r.get("default_branch"), "org": (r.get("owner") or {}).get("type") == "Organization",
                "can_admin": bool((r.get("permissions") or {}).get("admin"))}

    def people(self):
        """Teams of the organisation (if it is one) and the people who can work on the repository."""
        if not self.repo:
            return {"ok": False, "message": "Choose a repository first."}
        owner = self.repo.split("/")[0]
        teams, members, notes = [], [], []
        try:
            teams = [{"handle": "@%s/%s" % (owner, t["slug"]), "name": t.get("name", t["slug"])} for t in self.call("GET", "/orgs/%s/teams?per_page=100" % owner)]
        except GitHubError as e:
            notes.append("teams: " + ("not an organisation" if e.code == 404 else "needs the read:org permission" if e.code in (401, 403) else str(e)))
        try:
            members = [{"handle": "@" + c["login"], "name": c["login"]} for c in self.call("GET", "/repos/%s/collaborators?per_page=100" % self.repo)]
        except GitHubError as e:
            notes.append("people: " + str(e))
        return {"ok": True, "teams": teams, "members": members, "notes": notes}

    # ------------------------------------------------------------------ step 3: test e-mail
    def test_email(self, mail, password, to):
        host, port = (mail.get("host") or "").strip(), int(mail.get("port") or 587)
        user, sender, tls = (mail.get("user") or "").strip(), (mail.get("from") or mail.get("user") or "").strip(), (mail.get("tls") or "starttls")
        if not host or not to or "@" not in to:
            return {"ok": False, "message": "Mail server and a receiver address are needed."}
        m = EmailMessage()
        m["Subject"], m["From"], m["To"] = "[Arachnid] Test e-mail", sender or user, to
        m.set_content("This is a test from the Arachnid setup page. If you can read it, alarms will reach this inbox.")
        try:
            ctx = ssl.create_default_context()
            s = smtplib.SMTP_SSL(host, port, timeout=20, context=ctx) if tls == "ssl" else smtplib.SMTP(host, port, timeout=20)
            try:
                if tls == "starttls":
                    s.starttls(context=ctx)
                if user:
                    s.login(user, password or "")
                s.send_message(m)
            finally:
                try:
                    s.quit()
                except Exception:
                    pass
        except (OSError, smtplib.SMTPException) as e:
            return {"ok": False, "message": "The test e-mail did not go out: %s" % str(e)[:160]}
        return {"ok": True, "message": "Test e-mail sent to %s." % to}

    # ------------------------------------------------------------------ step 5: finish
    def build_files(self, plan):
        """The three files the pull request will contain, made from what was chosen on the page."""
        default = plan.get("default_branch") or "main"
        wf = (HERE / "github" / "arachnid.yml").read_text(encoding="utf-8")
        if default != "main":
            wf = wf.replace("  pull_request:\n    branches: [main]", "  pull_request:\n    branches: [%s]" % default)
            wf = wf.replace("  push:\n    branches: [main]", "  push:\n    branches: [%s]" % default)
            wf = wf.replace("github.ref == 'refs/heads/main'", "github.ref == 'refs/heads/%s'" % default)
            wf = wf.replace("--base main", "--base %s" % default)
        tool_repo = (plan.get("arachnid_repo") or "").strip().strip("/")
        if tool_repo.startswith("https://github.com/"):
            tool_repo = tool_repo[len("https://github.com/"):].removesuffix(".git")
        if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", tool_repo or ""):        # empty or odd -> keep the official home
            wf = re.sub(r"ARACHNID_REPO: \S+", "ARACHNID_REPO: %s" % tool_repo, wf, count=1)
        lines = ["# Who owns which part of the code - written by the Arachnid setup page.",
                 "# The LAST line that matches a file wins (GitHub's rule)."]
        if plan.get("default_owner"):
            lines.append("%-30s %s" % ("*", plan["default_owner"]))
        for o in plan.get("owners", []):
            raw, who = o.get("folder", "").strip(), " ".join(x for x in o.get("owners", []) if x)
            if not raw.strip("/") or not who:
                continue
            if any(c in raw for c in "*?"):              # a pattern such as *.md or /docs/* is kept exactly as written
                pattern = raw
            else:                                        # a plain folder name: that folder and everything inside it
                pattern = "/" + raw.strip("/") + "/"
            lines.append("%-30s %s" % (pattern, who))
        codeowners = "\n".join(lines) + "\n"
        cfg = {"email_mode": plan.get("email_mode", "teams")}
        if cfg["email_mode"] == "one":
            cfg["team_email"] = plan.get("team_email", "")
        cfg["teams"] = {k: v for k, v in (plan.get("teams") or {}).items() if v}
        cfg["people"] = {k: v for k, v in (plan.get("people") or {}).items() if v}
        cfg["always"] = {"alarm": [x for x in plan.get("always_alarm", []) if x]}
        domains = sorted({a.rsplit("@", 1)[1].lower() for a in list(cfg["teams"].values()) + list(cfg["people"].values()) + [cfg.get("team_email", "")]
                          if isinstance(a, str) and "@" in a})
        if domains:
            cfg["email_domains"] = domains
        cfg["send"] = {"alarm": plan.get("send_alarm", ["owners", "author", "always"]), "attention": plan.get("send_attention", ["author"])}
        mail = plan.get("mail") or {}
        if mail.get("host"):
            cfg["mail"] = {"host": mail["host"], "port": int(mail.get("port") or 587), "from": mail.get("from") or mail.get("user") or ""}
            if mail.get("tls") and mail["tls"] != "starttls":
                cfg["mail"]["tls"] = mail["tls"]
        return {".github/workflows/arachnid.yml": wf, ".github/CODEOWNERS": codeowners,
                "arachnid.config.json": json.dumps(cfg, indent=2) + "\n"}

    def finish(self, plan, password=None):
        if not self.token or not self.repo:
            return {"ok": False, "message": "Sign in and choose a repository first."}
        steps = []
        r = self.call("GET", "/repos/" + self.repo)
        default = r.get("default_branch") or "main"
        plan = dict(plan, default_branch=default)
        files = self.build_files(plan)
        base_sha = self.call("GET", "/repos/%s/git/ref/heads/%s" % (self.repo, urllib.parse.quote(default)))["object"]["sha"]
        # the setup branch: made fresh from main every time (it only ever holds Arachnid's setup files)
        try:
            self.call("PATCH", "/repos/%s/git/refs/heads/%s" % (self.repo, BRANCH), {"sha": base_sha, "force": True})
        except GitHubError as e:
            if e.code not in (404, 422):
                raise
            self.call("POST", "/repos/%s/git/refs" % self.repo, {"ref": "refs/heads/" + BRANCH, "sha": base_sha})
        steps.append("Branch %s made from %s." % (BRANCH, default))
        for path, text in files.items():
            body = {"message": "Arachnid setup: %s" % path, "content": base64.b64encode(text.encode("utf-8")).decode(), "branch": BRANCH}
            try:
                cur = self.call("GET", "/repos/%s/contents/%s?ref=%s" % (self.repo, urllib.parse.quote(path), BRANCH))
                if isinstance(cur, dict) and cur.get("sha"):
                    old = base64.b64decode(cur.get("content", "")).decode("utf-8", "replace") if cur.get("content") else None
                    if old == text:
                        steps.append("%s is already up to date." % path)
                        continue
                    body["sha"] = cur["sha"]
            except GitHubError as e:
                if e.code != 404:
                    raise
            self.call("PUT", "/repos/%s/contents/%s" % (self.repo, urllib.parse.quote(path)), body)
            steps.append("Wrote %s." % path)
        # the pull request (one, reused)
        owner = self.repo.split("/")[0]
        open_prs = self.call("GET", "/repos/%s/pulls?state=open&head=%s:%s" % (self.repo, owner, urllib.parse.quote(BRANCH)))
        pr_body = ("Set up by the Arachnid setup page.\n\n- `.github/workflows/arachnid.yml` - the automatic checks\n"
                   "- `.github/CODEOWNERS` - who owns which folder\n- `arachnid.config.json` - team e-mail addresses and who gets what "
                   "(no passwords)\n\nNothing changes in `%s` until this pull request is merged." % default)
        if open_prs:
            pr = open_prs[0]
            steps.append("Updated pull request #%d." % pr["number"])
        else:
            pr = self.call("POST", "/repos/%s/pulls" % self.repo, {"title": "Set up Arachnid", "head": BRANCH, "base": default, "body": pr_body})
            steps.append("Opened pull request #%d." % pr["number"])
        # let the automation open its fix pull requests (a repository setting) - only when asked
        if plan.get("allow_fix_prs"):
            try:
                self.call("PUT", "/repos/%s/actions/permissions/workflow" % self.repo,
                          {"default_workflow_permissions": "read", "can_approve_pull_request_reviews": True})
                steps.append("Allowed GitHub Actions to open pull requests.")
            except GitHubError as e:
                steps.append("Could not change the Actions setting (%s) - turn it on in Settings > Actions > General." % e)
        # mail server secrets
        secrets = {}
        mail = plan.get("mail") or {}
        if mail.get("host"):
            secrets = {"ARACHNID_SMTP_HOST": mail["host"], "ARACHNID_SMTP_PORT": str(mail.get("port") or 587),
                       "ARACHNID_SMTP_USER": mail.get("user") or "", "ARACHNID_MAIL_FROM": mail.get("from") or mail.get("user") or ""}
            if password:
                secrets["ARACHNID_SMTP_PASSWORD"] = password
        secret_links = []
        if secrets:
            if has_nacl():
                from nacl import encoding, public
                key = self.call("GET", "/repos/%s/actions/secrets/public-key" % self.repo)
                box = public.SealedBox(public.PublicKey(key["key"].encode(), encoding.Base64Encoder()))
                for name, value in secrets.items():
                    if not value:
                        continue
                    enc = base64.b64encode(box.encrypt(value.encode("utf-8"))).decode()
                    self.call("PUT", "/repos/%s/actions/secrets/%s" % (self.repo, name), {"encrypted_value": enc, "key_id": key["key_id"]})
                steps.append("Saved %d mail setting(s) in GitHub's encrypted secrets." % len([v for v in secrets.values() if v]))
            else:
                secret_links = [{"name": n, "value": ("(your password)" if n == "ARACHNID_SMTP_PASSWORD" else secrets.get(n, "")),
                                 "url": "%s/%s/settings/secrets/actions/new" % (WEB, self.repo)} for n in SECRET_NAMES if n in secrets or n == "ARACHNID_SMTP_PASSWORD"]
                steps.append("PyNaCl is not installed, so the mail secrets must be added on GitHub by hand.")
        return {"ok": True, "pr": pr.get("html_url"), "number": pr.get("number"), "steps": steps, "secret_links": secret_links,
                "files": {k: v for k, v in files.items() if k != ".github/workflows/arachnid.yml"}}


# ---------------------------------------------------------------------- local helpers
def load_json(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}


def read_codeowners(root):
    for cand in (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS"):
        p = Path(root) / cand
        if p.is_file():
            out = []
            for raw in p.read_text(encoding="utf-8-sig", errors="replace").splitlines():
                line = raw.split(" #")[0].strip()
                if line and not line.startswith("#"):
                    parts = line.split()
                    out.append({"pattern": parts[0], "owners": parts[1:]})
            return out
    return []


def folders(root, depth=2):
    skip = {"node_modules", ".git", "dist", "build", "__pycache__", ".venv", "venv", "arachnid", ".cache", ".next", "coverage", ".claude"}
    out = []
    base = Path(root)
    for dirpath, dirnames, _ in os.walk(base):
        rel = os.path.relpath(dirpath, base).replace("\\", "/")
        lvl = 0 if rel == "." else rel.count("/") + 1
        dirnames[:] = sorted(d for d in dirnames if d not in skip and not d.startswith(".") or d == ".github")
        if rel != ".":
            out.append(rel)
        if lvl >= depth:
            dirnames[:] = []
        if len(out) > 400:
            break
    return out
