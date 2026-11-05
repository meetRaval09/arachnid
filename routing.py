#!/usr/bin/env python3
"""
ARACHNID - ROUTING  (who should hear about which finding)
=========================================================
    python routing.py [folder] FILE[:LINE] ...     show who owns these files and who last changed these lines

In a firm, not everybody should get every message.  This module answers two questions for each finding:

  1. WHO OWNS this part of the code?
     GitHub's standard CODEOWNERS file (looked for in .github/, the project root and docs/) lists it, e.g.
         /payments/   @firm/payments-team
         *.css        @firm/web-team
     The LAST line that matches a file wins, exactly as on GitHub.

  2. WHO CHANGED it?
     In a pull request: the person who opened it (GitHub tells us - ARACHNID_AUTHOR).
     Otherwise: git remembers who last changed every line ("git blame"), so we ask git.

Optional settings - arachnid.config.json in the project root (no passwords in here, ever):
    {
      "teams":   {"@firm/payments-team": "payments@firm.com"},     team handle -> the team's group e-mail address
      "people":  {"@meet": "meet@firm.com"},                        person handle -> work e-mail
      "always":  {"alarm": ["@firm/security"]},                     extra receivers for every alarm
      "email_domains": ["firm.com"],                                only ever e-mail addresses ending in these
      "send":    {"alarm": ["owners", "author", "always"], "attention": ["author"]},
      "mail":    {"host": "smtp.office365.com", "port": 587, "from": "arachnid@firm.com"}
    }
"""
import json
import os
import re
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

DEFAULT_SEND = {"alarm": ["owners", "author", "always"], "attention": ["author"], "handled": [], "ideas": []}


# ---------------------------------------------------------------- settings
def load_config(root):
    p = Path(root) / "arachnid.config.json"
    if not p.is_file():
        return {}
    try:
        cfg = json.loads(p.read_text(encoding="utf-8-sig"))
        return cfg if isinstance(cfg, dict) else {}
    except (OSError, ValueError) as e:
        print("arachnid.config.json could not be read (%s) - using defaults" % e, file=sys.stderr)
        return {}


# ---------------------------------------------------------------- CODEOWNERS
def _pattern_regex(pat):
    """GitHub CODEOWNERS / gitignore-style pattern -> compiled regex over a project-relative path."""
    dir_only = pat.endswith("/")
    anchored = pat.startswith("/") or "/" in pat.strip("/")
    body = pat.strip("/")
    out, i = [], 0
    while i < len(body):
        if body.startswith("**/", i):
            out.append("(?:.*/)?"); i += 3
        elif body.startswith("/**", i) and i + 3 == len(body):
            out.append("/.*"); i += 3
        elif body.startswith("**", i):
            out.append(".*"); i += 2
        elif body[i] == "*":
            out.append("[^/]*"); i += 1
        elif body[i] == "?":
            out.append("[^/]"); i += 1
        else:
            out.append(re.escape(body[i])); i += 1
    last = body.rsplit("/", 1)[-1]
    if dir_only:
        tail = "/.*"
    elif "*" in last:               # "docs/*" covers docs/a.md but not docs/sub/b.md (GitHub's rule)
        tail = ""
    else:                           # "docs" or "/src/app" covers that file or everything inside that folder
        tail = "(?:/.*)?"
    return re.compile(("^" if anchored else "^(?:.*/)?") + "".join(out) + tail + "$")


def load_codeowners(root):
    for cand in (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS"):
        p = Path(root) / cand
        if p.is_file():
            rules = []
            for raw in p.read_text(encoding="utf-8-sig", errors="replace").splitlines():
                line = raw.split(" #")[0].strip() if not raw.strip().startswith("#") else ""
                if not line:
                    continue
                parts = line.split()
                pat, owners = parts[0], [o for o in parts[1:] if o.startswith("@") or "@" in o]
                try:
                    rules.append((_pattern_regex(pat), owners, pat))
                except re.error:
                    continue
            return rules, cand
    return [], None


def owners_of(rules, rel):
    rel = rel.replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    rel = rel.lstrip("/")
    found = []
    for rx, owners, _pat in rules:          # last matching line wins
        if rx.match(rel):
            found = owners
    return list(found)


# ---------------------------------------------------------------- who changed it
def _git(root, *args):
    try:
        r = subprocess.run(["git", "-C", str(root)] + list(args), capture_output=True, text=True, timeout=20, errors="replace")
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def handle_from_email(email, people):
    if not email:
        return None
    for h, e in people.items():
        if isinstance(e, str) and e.lower() == email.lower():
            return h if h.startswith("@") else "@" + h
    m = re.match(r"^(?:\d+\+)?([A-Za-z0-9-]+)@users\.noreply\.github\.com$", email)
    return "@" + m.group(1) if m else None


@lru_cache(maxsize=4096)
def _blame(root, rel, line):
    if line and line > 0:
        out = _git(root, "blame", "--porcelain", "-L", "%d,%d" % (line, line), "--", rel)
        m = re.search(r"^author-mail <([^>]*)>", out, re.M)
        if m and "not.committed.yet" not in m.group(1):
            return m.group(1)
    out = _git(root, "log", "-1", "--format=%ae", "--", rel).strip()
    return out or None


@lru_cache(maxsize=8)
def changed_files(root):
    """In a pull request: the set of files the pull request changes.  None = not a pull request / unknown."""
    base = os.environ.get("GITHUB_BASE_REF", "").strip()
    if not base:
        return None
    for ref in ("origin/%s...HEAD" % base, "%s...HEAD" % base):
        out = _git(root, "diff", "--name-only", ref)
        if out.strip():
            return frozenset(x.strip() for x in out.splitlines() if x.strip())
    return None


def in_change(root, rel):
    ch = changed_files(str(root))
    return None if ch is None else rel.replace("\\", "/") in ch


def author_of(root, rel, line, cfg):
    """(github handle or None, e-mail or None) of the person who changed this spot."""
    pr_author = os.environ.get("ARACHNID_AUTHOR", "").strip()
    if pr_author and in_change(root, rel) is not False:   # a file this pull request touches: its contributor is known for sure
        h = pr_author if pr_author.startswith("@") else "@" + pr_author
        return h, (cfg.get("people") or {}).get(h) or (cfg.get("people") or {}).get(h[1:])
    email = _blame(str(root), rel.replace("\\", "/"), int(line or 0))
    return handle_from_email(email, cfg.get("people") or {}), email


# ---------------------------------------------------------------- put it together
class Router:
    def __init__(self, root):
        self.root = Path(root)
        self.cfg = load_config(root)
        self.rules, self.codeowners_file = load_codeowners(root)
        self.send = dict(DEFAULT_SEND)
        self.send.update({k: v for k, v in (self.cfg.get("send") or {}).items() if isinstance(v, list)})
        self.domains = [d.lower().lstrip("@") for d in (self.cfg.get("email_domains") or [])]

    def route(self, finding, tier):
        rel, line = finding.get("file", ""), finding.get("line", 0)
        owners = owners_of(self.rules, rel) if self.rules else []
        handle, email = author_of(self.root, rel, line, self.cfg) if self.send.get(tier) and "author" in self.send[tier] else (None, None)
        always = list((self.cfg.get("always") or {}).get(tier, []))
        who = []
        for src in self.send.get(tier, []):
            who += {"owners": owners, "author": [handle] if handle else [], "always": always}.get(src, [])
        seen, handles = set(), []
        for h in who:
            if h and h.lower() not in seen:
                seen.add(h.lower()); handles.append(h)
        return {"in_change": in_change(self.root, rel), "owners": owners, "author": handle, "author_email": email, "notify": handles,
                "emails": self.emails_for(handles, email if "author" in self.send.get(tier, []) else None)}

    def allowed(self, addr):
        if not addr or "@" not in addr or addr.endswith("users.noreply.github.com"):
            return False
        if not self.domains:
            return True
        return addr.lower().rsplit("@", 1)[-1] in self.domains

    def one_inbox(self):
        """'One e-mail address for the whole team' mode: every message goes to that inbox (one e-mail per team, so
        the subject still says which team it is for and mail rules can sort them)."""
        addr = self.cfg.get("team_email")
        return addr if self.cfg.get("email_mode") == "one" and isinstance(addr, str) and "@" in addr else None

    def emails_for(self, handles, author_email=None):
        teams, people = self.cfg.get("teams") or {}, self.cfg.get("people") or {}
        inbox = self.one_inbox()
        if inbox:
            return [inbox] if handles and self.allowed(inbox) else []
        out = []
        for h in handles:
            if "@" in h and not h.startswith("@"):          # CODEOWNERS may list a plain e-mail address
                addr = h
            else:
                addr = teams.get(h) or people.get(h) or people.get(h.lstrip("@")) or teams.get(h.lstrip("@"))
            if isinstance(addr, str) and self.allowed(addr) and addr not in out:
                out.append(addr)
        if author_email and self.allowed(author_email) and author_email not in out and self.domains:
            out.append(author_email)                       # a commit e-mail is only used when it is inside the firm's domain
        return out


def main():
    args = sys.argv[1:]
    root = args.pop(0) if args and Path(args[0]).is_dir() else "."
    r = Router(root)
    print("CODEOWNERS:", r.codeowners_file or "(none found)", "  settings:", "arachnid.config.json" if r.cfg else "(none)")
    for a in args:
        f, _, ln = a.partition(":")
        x = r.route({"file": f, "line": int(ln or 0)}, "alarm")
        print("  %-40s owners %-30s changed by %-20s -> notify %s  e-mail %s" % (a, " ".join(x["owners"]) or "-", x["author"] or x["author_email"] or "-",
                                                                         " ".join(x["notify"]) or "-", ", ".join(x["emails"]) or "-"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
