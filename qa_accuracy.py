#!/usr/bin/env python3
"""
ARACHNID - ACCURACY TEST  (does the crawler FIND what it should, and stay QUIET where it should?)
==================================================================================================
    python qa_accuracy.py            build a practice project with known, planted problems + clean look-alikes
                                     ("decoys"), run `arachnid.py scan`, and score the result.

Two numbers matter, in plain words:
  FOUND     of the problems we hid on purpose, how many did the crawler catch?      (want: all of them)
  FALSE     how many complaints did it raise about files that are actually fine?    (want: zero)
The project lives in a temporary folder and is deleted afterwards.  Your own files are never touched.
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
LEDGER = HERE / "reports" / "ledger.jsonl"


def w(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="")


def build(d):
    # ---- planted problems --------------------------------------------------------------------------------------
    w(d / "docs/guide.md", "# Guide\n\nWe recieve teh data from the server every minute.\n")
    w(d / "README.md", "# Demo\n\nTheir is a problem with the setup. See the github page and the Github wiki and the github issues.\n")
    w(d / "bad.py", "def f(:\n    pass\n")
    w(d / "bad.js", "const a = {x: 1,, y: 2};\n")
    w(d / "web/index.html", '<!doctype html><html><head><link rel="stylesheet" href="missing.css">'
                            '<script src="http://cdn.example.com/lib.js"></script></head><body><p>Hi</p></body></html>')
    w(d / "notes/dup1.txt", "This exact note is stored twice in the project, word for word, line for line.\n" * 5)
    w(d / "notes/dup2.txt", "This exact note is stored twice in the project, word for word, line for line.\n" * 5)
    w(d / "util.py", "import os\nimport json\n\n\ndef g():\n    return json.dumps(1)\n")
    w(d / "twice.py", "def h():\n    return 1\n\n\ndef h():\n    return 2\n")
    w(d / "api.py", "import requests\n\n\ndef pull():\n"
                    "    return requests.get('https://api.example.com/v1/items?api_key=sk_live_51HxQabcdefghijklmnop', verify=False)\n")
    w(d / "rules.py", "ALLOWED = ['red', 'green', 'blue', 'black', 'white']\n\n\ndef ok(c):\n    return c in ALLOWED\n")
    # ---- decoys: look suspicious, are actually fine -------------------------------------------------------------
    w(d / "docs/clean.md", "# Clean\n\nWe receive the data. The variable is called `recieve_buf` in code:\n\n```\nteh = 1\n```\n\nSee GitHub.\n")
    w(d / "good.py", "import os\n\n\ndef here():\n    return os.getcwd()\n")
    w(d / "good.js", "export const add = (a, b) => a + b;\n")
    w(d / "web/ok.html", '<!doctype html><html><head><link rel="stylesheet" href="ok.css"></head><body><p>Fine page.</p></body></html>')
    w(d / "web/ok.css", "p { color: #333; }\n")
    w(d / "safe_api.py", "import requests\n\n\ndef pull(token):\n"
                         "    return requests.get('https://api.example.com/v1/items', headers={'Authorization': token}, timeout=10)\n")
    w(d / "names.py", "def describe(cat):\n    return 'the cat is ' + cat\n")


# (what, file, tools that may report it, words one of which must appear in the message)
PLANTED = [
    ("misspelling 'recieve'", "docs/guide.md", {"spelling"}, ["recieve"]),
    ("misspelling 'teh'", "docs/guide.md", {"spelling"}, ["teh"]),
    ("'Their is' grammar", "README.md", {"text"}, ["their"]),
    ("inconsistent 'Github' spelling", "README.md", {"terms"}, ["github"]),
    ("python syntax error", "bad.py", {"syntax"}, [""]),
    ("javascript syntax error", "bad.js", {"syntax"}, [""]),
    ("missing stylesheet", "web/index.html", {"links"}, ["missing.css"]),
    ("insecure http script", "web/index.html", {"net"}, ["http"]),
    ("duplicate files", "notes/dup", {"dedupe"}, [""]),
    ("unused import os", "util.py", {"logic"}, ["os"]),
    ("function defined twice", "twice.py", {"logic"}, ["h"]),
    ("secret key in URL", "api.py", {"net"}, ["secret", "key", "token", "credential"]),
    ("TLS check switched off", "api.py", {"net"}, ["verify", "tls", "certificate", "ssl"]),
    ("request without timeout", "api.py", {"net", "advisor"}, ["timeout"]),
    ("constant list used for lookups", "rules.py", {"advisor"}, ["set"]),
]
DECOYS = ["docs/clean.md", "good.py", "good.js", "web/ok.html", "web/ok.css", "safe_api.py", "names.py"]
IGNORE_KINDS_ON_DECOYS = {"unused-file"}          # "nothing links to this file" ideas are true for a practice project


def main():
    tmp = Path(tempfile.mkdtemp(prefix="arachnid-acc-"))
    proj = tmp / "proj"
    build(proj)
    if "--esbuild" in sys.argv:       # test the stronger JavaScript reader too (needs an esbuild install to point at)
        import os
        os.symlink(sys.argv[sys.argv.index("--esbuild") + 1], str(proj / "node_modules"), target_is_directory=True)
    keep = LEDGER.read_bytes() if LEDGER.exists() else None
    rep_backup = tmp / "reports-backup"
    if (HERE / "reports").exists():
        shutil.copytree(HERE / "reports", rep_backup)
    try:
        r = subprocess.run([sys.executable, str(HERE / "arachnid.py"), "scan", str(proj)], capture_output=True, text=True, errors="replace", timeout=600)
        out = r.stdout + r.stderr          # (scan starts a fresh ledger, so everything in it is from this scan)
        if "Traceback (most recent call last)" in out:
            print("CRASH during scan:\n" + out[out.rindex("Traceback"):][:1500])
        lines = LEDGER.read_text("utf-8", "replace").splitlines() if LEDGER.exists() else []
        ents = []
        for ln in lines:
            try:
                ents.append(json.loads(ln))
            except ValueError:
                pass
        found = 0
        print("PLANTED PROBLEMS (should be found)")
        for what, f, tools, words in PLANTED:
            hit = [e for e in ents if e.get("file", "").replace("\\", "/").startswith(f) and e.get("tool") in tools
                   and any(wd.lower() in (e.get("what", "") + " " + e.get("why", "")).lower() for wd in words)]
            found += bool(hit)
            print("  %-6s %-34s %s" % ("FOUND" if hit else "MISSED", what, ("[%s/%s] %s" % (hit[0]["tool"], hit[0]["severity"], hit[0]["what"][:70])) if hit else ""))
        false = []
        for e in ents:
            fe = e.get("file", "").replace("\\", "/")
            if fe in DECOYS and e.get("kind") not in IGNORE_KINDS_ON_DECOYS and e.get("status") != "idea":
                false.append(e)
        print("\nDECOYS (should stay quiet)")
        for dcy in DECOYS:
            fs = [e for e in false if e["file"].replace("\\", "/") == dcy]
            print("  %-6s %-34s %s" % ("QUIET" if not fs else "FALSE", dcy, "; ".join("[%s] %s" % (e["tool"], e["what"][:60]) for e in fs)))
        sev = {e["what"][:40]: e["severity"] for e in ents if e.get("file") == "api.py" and e.get("tool") == "net"}
        crit_ok = any(s == "critical" for s in sev.values())
        print("\nSCORE: found %d of %d planted problems, %d false complaint(s) on clean files; secret marked critical: %s"
              % (found, len(PLANTED), len(false), "yes" if crit_ok else "NO"))
        return 0 if (found == len(PLANTED) and not false and crit_ok) else 1
    finally:
        if rep_backup.exists():
            shutil.rmtree(HERE / "reports", ignore_errors=True)
            shutil.copytree(rep_backup, HERE / "reports", dirs_exist_ok=True)
        elif keep is not None:
            LEDGER.write_bytes(keep)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
