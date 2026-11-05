#!/usr/bin/env python3
"""
ARACHNID - THE STANDARD REPORT  (one document, the same everywhere)
====================================================================
    python report.py [folder] [--format md|html|text|json] [--mode scan|fix|issue] [--out FILE]

Every way Arachnid talks to people - the GitHub comment, the GitHub issue, the e-mail, later the dashboard - is made
from ONE file:  reports/arachnid-report.json.  So the content and the order are always the same:

    1  ALARMS            critical: something is broken or dangerous         -> owners + the person who changed it
    2  NEEDS A DECISION  the crawler will not guess                         -> the person who changed it
    3  HANDLED           the crawler fixed it or can fix it safely          -> nobody is pinged, it is just listed
    4  IDEAS             optional improvements                              -> nobody is pinged

Every finding in the file has the same fields:
    id, tier, severity, area, tool, kind, status, file, line, what, why, todo, owners, author, notify
The report never contains file contents or secret values (only names, line numbers and short descriptions), and never
e-mail addresses - those are looked up only at the moment an e-mail is sent.
"""
import argparse
import hashlib
import html as H
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import triage  # noqa: E402
import routing  # noqa: E402

FORMAT_VERSION = 1
TIERS = [("alarm", "ALARM", "🔴", "Alarms"), ("attention", "DECISION", "🟠", "Needs a decision"),
         ("handled", "HANDLED", "✅", "Handled by the crawler"), ("ideas", "IDEA", "💡", "Ideas")]
TIER_INFO = {t[0]: t for t in TIERS}
MARK = "<!-- arachnid-report -->"
MAX_ROWS = 15
REPORT = HERE / "reports" / "arachnid-report.json"


# ------------------------------------------------------------------------------------------------- build
def build(root="."):
    root = Path(root).resolve()
    piles, _total = triage.build()
    router = routing.Router(root)
    findings = []
    for tier, *_ in TIERS:
        for e in piles.get(tier, []):
            r = router.route(e, tier) if tier in ("alarm", "attention") else {"owners": routing.owners_of(router.rules, e.get("file", "")) if router.rules else [], "author": None, "notify": [],
                                                                             "in_change": routing.in_change(root, e.get("file", ""))}
            # no line number in the id: moving code up or down does not make an old finding "new"
            fid = hashlib.sha1(("%s|%s|%s" % (e.get("tool"), e.get("file"), e.get("what", "")[:80])).encode("utf-8", "replace")).hexdigest()[:10]
            findings.append({"id": fid, "tier": tier, "severity": e.get("severity", "info"), "area": triage.KIND_WORDS.get(e.get("tool"), e.get("tool")),
                             "tool": e.get("tool"), "kind": e.get("kind", ""), "status": e.get("status", ""), "file": e.get("file", ""),
                             "line": int(e.get("line") or 0), "what": e.get("what", ""), "why": e.get("why", ""), "todo": e.get("todo", ""),
                             "in_change": r["in_change"], "owners": r["owners"], "author": r["author"], "notify": r["notify"]})
    sha = os.environ.get("ARACHNID_SHA") or os.environ.get("GITHUB_SHA") or routing._git(root, "rev-parse", "HEAD").strip()
    rep = {"format": "arachnid-report", "version": FORMAT_VERSION,
           "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "project": os.environ.get("GITHUB_REPOSITORY") or root.name, "commit": sha or "",
           "branch": os.environ.get("GITHUB_HEAD_REF") or os.environ.get("GITHUB_REF_NAME") or routing._git(root, "rev-parse", "--abbrev-ref", "HEAD").strip(),
           "event": os.environ.get("GITHUB_EVENT_NAME", "local"), "codeowners": router.codeowners_file,
           "counts": {t: sum(1 for f in findings if f["tier"] == t) for t, *_ in TIERS}, "findings": findings}
    rep["blocking"] = blocking(rep)
    return rep


def blocking(rep):
    """How many alarms should turn the check red.  In a pull request only the alarms in files that pull request changes
    count - an old problem already in main is not the contributor's fault (it is tracked by the 'Arachnid alarm' issue)."""
    A = [f for f in rep["findings"] if f["tier"] == "alarm"]
    if any(f.get("in_change") is not None for f in A):
        return sum(1 for f in A if f.get("in_change"))
    return len(A)


def save(rep):
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(rep, indent=2, ensure_ascii=False), encoding="utf-8")


def load():
    return json.loads(REPORT.read_text(encoding="utf-8"))


# ------------------------------------------------------------------------------------------------- helpers
def short(s, n=140):
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def where(f):
    return f["file"] + (":%d" % f["line"] if f.get("line") else "")


def url(rep, f):
    repo, sha = (os.environ.get("GITHUB_REPOSITORY") or (rep["project"] if "/" in rep["project"] else "")), rep.get("commit")
    if not (repo and sha and f["file"]) or f["file"].startswith(("/", "http")):
        return None
    u = "%s/%s/blob/%s/%s" % (os.environ.get("GITHUB_SERVER_URL", "https://github.com"), repo, sha, f["file"].replace("\\", "/"))
    return (u + ("#L%d" % f["line"] if f.get("line") else "")).replace(" ", "%20")


def subject(rep, subset, group=""):
    top = min((["alarm", "attention", "handled", "ideas"].index(f["tier"]) for f in subset), default=3)
    label = TIERS[top][1]
    g = (" [%s]" % group.lstrip("@").split("/")[-1]) if group else ""
    if len(subset) == 1:
        f = subset[0]
        return "[Arachnid] [%s]%s %s - %s" % (label, g, where(f), short(f["what"], 70))
    c = Counter(f["tier"] for f in subset)
    parts = ["%d %s" % (c[t], {"alarm": "alarm(s)", "attention": "decision(s)", "handled": "handled", "ideas": "idea(s)"}[t]) for t, *_ in TIERS if c[t]]
    return "[Arachnid] [%s]%s %s - %s@%s" % (label, g, ", ".join(parts), rep["project"].split("/")[-1], rep.get("commit", "")[:7])


# ------------------------------------------------------------------------------------------------- "already told" memory
def ids_marker(findings):
    """A hidden line in the GitHub comment / issue listing what people were already told about. The next run reads it back,
    so the same alarm is not e-mailed again on every push - only NEW alarms and decisions are sent."""
    return "<!-- arachnid-ids: %s -->" % ",".join(sorted({f["id"] for f in findings}))


def ids_from_text(text):
    import re
    m = re.search(r"arachnid-ids: ([0-9a-f,]*)", text or "")
    return {x for x in (m.group(1).split(",") if m else []) if x}


# ------------------------------------------------------------------------------------------------- Markdown (GitHub)
def _md_cell(s, n=140):
    return short(s, n).replace("|", "\\|").replace("<", "&lt;").replace(">", "&gt;")


def _md_table(rep, rows, who=True):
    head = "| | Where | What | Area |" + (" Who should look |" if who else "")
    out = [head, "|---|---|---|---|" + ("---|" if who else "")]
    for f in rows[:MAX_ROWS]:
        icon = {"critical": "🔴", "warning": "🟠"}.get(f["severity"], "🔵")
        u = url(rep, f)
        w = "`%s`" % _md_cell(where(f), 80)
        cells = [icon, "[%s](%s)" % (w, u) if u else w, _md_cell(f["what"]), _md_cell(f["area"], 40)]
        if who:
            if f.get("in_change") is False:                     # not this pull request's doing: name them, but do not ping them
                cells.append(" ".join("`%s`" % h for h in f["notify"]) or "-")
            else:
                cells.append(" ".join(f["notify"]) or "-")      # a plain @handle here is what makes GitHub notify them
        out.append("| " + " | ".join(cells) + " |")
    if len(rows) > MAX_ROWS:
        out.append("\n…and %d more - see the full report attached to this run (arachnid-reports)." % (len(rows) - MAX_ROWS))
    return "\n".join(out)


def markdown(rep, mode="scan"):
    F = rep["findings"]
    by = {t: [f for f in F if f["tier"] == t] for t, *_ in TIERS}
    A, T, Hd, I = by["alarm"], by["attention"], by["handled"], by["ideas"]
    title = {"scan": "found something critical" if rep.get("blocking", len(A)) else "check passed", "fix": "suggested fixes", "issue": "alarm in `%s`" % (rep.get("branch") or "main")}[mode]
    lines = [MARK, "## 🕷️ Arachnid %s" % title]
    if mode == "fix":
        lines.append("Every change in this pull request already passed Arachnid's safety gate (the product looked and behaved the same "
                     "before and after, in a real browser or with your tests). Nothing reaches `main` until a person reviews and merges it.\n")
    lines.append(ids_marker(A + T))
    if mode == "issue":
        lines.append("Arachnid found critical problems in `%s` after commit `%s`. This issue closes by itself once a later check finds no alarms.\n"
                     % (rep.get("branch") or "main", rep.get("commit", "")[:7]))
    lines.append("**%d** alarm(s) · **%d** need a decision · **%d** handled by the crawler · **%d** idea(s)\n" % (len(A), len(T), len(Hd), len(I)))
    pr = mode == "scan" and any(f.get("in_change") is not None for f in F)
    if pr:
        new, old = [f for f in A if f.get("in_change")], [f for f in A if not f.get("in_change")]
        if new:
            lines += ["### 🔴 Alarms in files this pull request changes - please fix before merging", _md_table(rep, new), ""]
        if old:
            lines += ["### ⚪ Already in `main` before this pull request (not blocking, tracked separately)", _md_table(rep, old), ""]
    elif A:
        lines += ["### 🔴 " + ("Alarms still open in `main` - these need a person (not part of this pull request)" if mode == "fix" else "Alarms - please fix before merging" if mode == "scan" else "Alarms"),
                  _md_table(rep, A), ""]
    if T and mode != "issue":
        lines += ["### 🟠 Needs a decision", "These are only reported. Arachnid never changes them by itself.\n", _md_table(rep, T), ""]
    if Hd and mode != "issue":
        c = Counter(f["area"] + (" (fixed)" if f["status"] == "fixed" else " (can be fixed automatically)") for f in Hd)
        lines += ["### ✅ Handled by the crawler", "\n".join("- %s: %d" % kv for kv in sorted(c.items())), ""]
    if I and mode != "issue":
        c = Counter(f["area"] for f in I)
        lines += ["<details><summary>💡 %d optional idea(s)</summary>\n" % len(I), "\n".join("- %s: %d" % kv for kv in sorted(c.items())), "\n</details>", ""]
    if not F:
        lines.append("Nothing to report - the project looks clean.")
    lines.append("\n<sub>Arachnid report v%d · %s · commit %s · %s.</sub>" % (rep["version"], rep["project"], rep.get("commit", "")[:7] or "-",
                 {"scan": "updated on every new push to the pull request", "fix": "updated on every new push to main", "issue": "updated on every push to main"}[mode]))
    return "\n".join(lines)


# ------------------------------------------------------------------------------------------------- e-mail (HTML + plain text)
def html(rep, subset, intro=""):
    e = H.escape
    css_td = "padding:6px 8px;border-bottom:1px solid #e5e5e5;vertical-align:top;font:14px/1.4 Arial,sans-serif"
    out = ['<div style="font:14px/1.5 Arial,sans-serif;color:#1a1a1a;max-width:760px">',
           '<h2 style="margin:0 0 4px">🕷️ Arachnid report</h2>',
           '<div style="color:#666;margin-bottom:12px">%s · commit %s · %s</div>' % (e(rep["project"]), e(rep.get("commit", "")[:7] or "-"), e(rep["generated_at"]))]
    if intro:
        out.append("<p>%s</p>" % e(intro))
    for tier, _lab, icon, name in TIERS:
        rows = [f for f in subset if f["tier"] == tier]
        if not rows:
            continue
        out.append('<h3 style="margin:18px 0 6px">%s %s (%d)</h3>' % (icon, e(name), len(rows)))
        out.append('<table style="border-collapse:collapse;width:100%"><tr>' + "".join('<th style="%s;text-align:left;background:#f6f6f6">%s</th>' % (css_td, h) for h in ("Where", "What", "What to do")) + "</tr>")
        out[-1] = out[-1].replace('<table style="border-collapse:collapse;width:100%">', '<table style="border-collapse:collapse;width:100%;table-layout:fixed"><col style="width:24%"><col style="width:50%"><col style="width:26%">')
        for f in rows[:50]:
            u = url(rep, f)
            w = '<a href="%s">%s</a>' % (e(u), e(where(f))) if u else e(where(f))
            out.append('<tr><td style="%s;font-family:Consolas,monospace">%s</td><td style="%s">%s<div style="color:#666">%s</div></td><td style="%s">%s</td></tr>'
                       % (css_td, w, css_td, e(short(f["what"], 200)), e(short(f.get("why", ""), 160)), css_td, e(short(f.get("todo", ""), 120))))
        out.append("</table>")
        if len(rows) > 50:
            out.append("<p>…and %d more in the full report.</p>" % (len(rows) - 50))
    out.append('<p style="color:#888;font-size:12px;margin-top:20px">Sent by Arachnid. This e-mail contains only file names, line numbers and short '
               'descriptions - never your code or any secret values. You received it because you own or changed these files.</p></div>')
    return "\n".join(out)


def text(rep, subset, intro=""):
    out = ["ARACHNID REPORT - %s - commit %s" % (rep["project"], rep.get("commit", "")[:7] or "-"), ""]
    if intro:
        out += [intro, ""]
    for tier, lab, _icon, name in TIERS:
        rows = [f for f in subset if f["tier"] == tier]
        if rows:
            out.append("%s (%d)" % (name.upper(), len(rows)))
            for f in rows[:50]:
                out.append("  - %s  %s" % (where(f), short(f["what"], 160)))
                if f.get("todo"):
                    out.append("      what to do: %s" % short(f["todo"], 140))
                u = url(rep, f)
                if u:
                    out.append("      %s" % u)
            out.append("")
    out.append("Only file names, line numbers and short descriptions are included - never code or secret values.")
    return "\n".join(out)


# ------------------------------------------------------------------------------------------------- CLI
def main():
    ap = argparse.ArgumentParser(description="Arachnid standard report")
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--format", choices=["md", "html", "text", "json"], default="md")
    ap.add_argument("--mode", choices=["scan", "fix", "issue"], default="scan")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    rep = build(a.root)
    save(rep)
    body = {"md": lambda: markdown(rep, a.mode), "html": lambda: html(rep, rep["findings"]), "text": lambda: text(rep, rep["findings"]),
            "json": lambda: json.dumps(rep, indent=2, ensure_ascii=False)}[a.format]()
    if a.out:
        Path(a.out).write_text(body + "\n", encoding="utf-8")
    else:
        try:
            sys.stdout.reconfigure(errors="replace")
        except Exception:
            pass
        print(body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
