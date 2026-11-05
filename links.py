#!/usr/bin/env python3
"""
ARACHNID - BROKEN REFERENCES  (looks only, never changes anything)
==================================================================
    python links.py [folder]

A page that asks for a file that does not exist is broken for every visitor: a missing picture, a missing
script, a missing style sheet.  This tool reads every HTML and CSS file, collects every file it points to, and
checks that the file is really there.  It uses the same browser-correct path rules as the duplicate merger.

How sure it is
  CRITICAL  a path written relative to the page (images/a.png, ../css/site.css) that leads nowhere
  WARNING   a path starting with /  that leads nowhere in the project - it might be created by the build
Links to other sites, #anchors, mailto:, data: and template placeholders are ignored.
"""
import argparse
import json
import posixpath
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import dedupe  # noqa: E402
import ledger  # noqa: E402

TEMPLATE = re.compile(r"\{\{|\{%|<%|<\?|\$\{|\[\[|#\{|%\{|\{\$")
HAS_EXT = re.compile(r"\.[A-Za-z0-9]{1,6}$")
NAME_BEFORE = re.compile(r"""\b(src|href|poster|data-src|data-poster|data-bg|xlink:href)\s*=\s*['"]?$""", re.I)
SKIP_SCHEME = re.compile(r"^(?:[a-z][a-z0-9+.\-]*:|//|#)", re.I)


def tag_before(text, pos):
    i = text.rfind("<", 0, pos)
    if i < 0:
        return ""
    m = re.match(r"<\s*/?\s*([A-Za-z][\w:-]*)", text[i:i + 40])
    return m.group(1).lower() if m else ""


def check(root):
    project = dedupe.Project(root)
    files = project.files
    by_name = {}
    for rel in files:
        by_name.setdefault(posixpath.basename(rel).lower(), []).append(rel)
    exists = lambda p: p in files
    out = []
    pages = 0
    for rel, size in files.items():
        ext = rel.rsplit(".", 1)[-1].lower() if "." in rel else ""
        if ext not in ("html", "htm", "css") or size > 3_000_000:
            continue
        try:
            text = (project.root / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        pages += 1
        for r in dedupe.find_refs(rel, text):
            raw, _s = dedupe.split_suffix(r.raw)
            if r.kind not in ("attr", "url", "srcset") or not raw or SKIP_SCHEME.match(raw) or TEMPLATE.search(raw):
                continue
            if not HAS_EXT.search(raw):
                continue
            if r.kind == "attr":
                if not NAME_BEFORE.search(text[max(0, r.start - 24):r.start]):
                    continue
                if tag_before(text, r.start) in ("a", "area", "form", "meta", "base"):
                    continue                               # links to other pages may be served by routes
            if dedupe.resolve(project, r, exists):
                continue
            line = text.count("\n", 0, r.start) + 1
            name = posixpath.basename(raw).lower()
            elsewhere = [p for p in by_name.get(name, []) if p != rel][:2]
            if raw.startswith("/") and not elsewhere:
                sev, note = "warning", "starts with / and matches nothing in the project (it may be created when the site is built)"
            else:
                sev, note = "critical", "the file is not there"
            hint = (" - a file with that name exists at %s (was it moved?)" % ", ".join(elsewhere)) if elsewhere else ""
            out.append({"file": rel, "line": line, "ref": r.raw, "severity": sev, "note": note + hint})
    return pages, out


def main():
    ap = argparse.ArgumentParser(description="Arachnid broken-reference finder (looks only)")
    ap.add_argument("root", nargs="?", default=str(HERE.parent))
    ap.add_argument("--out", default=str(HERE / "reports" / "links-report.json"))
    a = ap.parse_args()
    print("Arachnid broken-reference finder - looking at:", Path(a.root).resolve())
    pages, found = check(a.root)
    ledger.reset("links")
    for f in found:
        print("\n  %s  line %d   [%s]" % (f["file"], f["line"], f["severity"].upper()))
        print("     points to : %s" % f["ref"])
        print("     problem   : %s" % f["note"])
        ledger.record("links", "found", f["severity"], f["file"], f["line"], "points to %s, but %s" % (f["ref"], f["note"]),
                      "visitors get a missing picture, script or style sheet on this page",
                      "restore the file, or correct the path in the page", "missing-file")
    crit = sum(1 for f in found if f["severity"] == "critical")
    print("\n---------------- Summary ----------------")
    print("HTML/CSS files read: %d    References to missing files: %d   (critical %d, warning %d)" % (pages, len(found), crit, len(found) - crit))
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps({"root": str(Path(a.root).resolve()), "findings": found}, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
