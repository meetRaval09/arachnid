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
    w(d / "shop.py", "def checkout(cart):\n    total = sum(i['price'] for i in cart)\n    return gateway.charge(amount=total)\n")
    w(d / "orders.py", "def confirm(answer):\n    if answer == 'yes' or 'y':\n        return True\n    return False\n")
    w(d / "web/cart.js", "export function total(items) {\n  return\n    items.length;\n}\n")
    w(d / "web/theme.css", ".card { colr: #333; }\n")
    w(d / "docs/links.md", "# Links\n\nRead the [setup guide](setup.md).\n")
    w(d / "server/users.py", "from flask import request\n\n\ndef find(db):\n    name = request.args.get('name')\n"
                             "    return db.execute(f\"SELECT * FROM users WHERE name = '{name}'\")\n")
    w(d / "web/comments.js", "export function show(el, comment) {\n  el.innerHTML = comment;\n}\n")
    w(d / "server/keys.py", "AWS_KEY = '%s'\n" % ("AKIA" + "2JQ7R4XKBM6PLN3T"))      # built here, so this file holds no key
    w(d / "server/routes.js", "const app = require('express')();\napp.delete('/orders/:id', async (req, res) => {\n  await Order.deleteOne({ _id: req.params.id });\n  res.end();\n});\n")
    # ---- decoys: look suspicious, are actually fine -------------------------------------------------------------
    w(d / "server/users_ok.py", "from flask import request\n\n\ndef find(db):\n    name = request.args.get('name')\n"
                                "    return db.execute('SELECT * FROM users WHERE name = ?', (name,))\n")
    w(d / "web/comments_ok.js", "export function show(el, comment) {\n  el.textContent = comment;\n}\n")
    w(d / "server/keys_ok.py", "import os\nAWS_KEY = os.environ['AWS_KEY']\nPASSWORD_FIELD = 'password'\n")
    w(d / "server/routes_ok.js", "const app = require('express')();\napp.delete('/orders/:id', requireAuth, async (req, res) => {\n  await Order.deleteOne({ _id: req.params.id });\n  res.end();\n});\n")
    w(d / "orders_ok.py", "def confirm(answer):\n    if answer == 'yes' or answer == 'y':\n        return True\n    return False\n")
    w(d / "web/cart_ok.js", "export function total(items) {\n  return (\n    items.length\n  );\n}\n")
    w(d / "web/theme_ok.css", ".card { color: #333; width: 0; }\n")
    w(d / "docs/links_ok.md", "# Links\n\nRead the [guide](guide.md) and [clean notes](clean.md).\n")
    w(d / "shop_ok.py", "def checkout(cart):\n    total = sum(i['price'] for i in cart)\n    if total <= 0:\n        return None\n"
                        "    return gateway.charge(amount=total)\n\n\ndef history(user):\n    return get_payment_history(user)\n")
    w(d / "docs/clean.md", "# Clean\n\nWe receive the data. The variable is called `recieve_buf` in code:\n\n```\nteh = 1\n```\n\nSee GitHub.\n")
    w(d / "good.py", "import os\n\n\ndef here():\n    return os.getcwd()\n")
    w(d / "good.js", "export const add = (a, b) => a + b;\n")
    w(d / "web/ok.html", '<!doctype html><html><head><link rel="stylesheet" href="ok.css"></head><body><p>Fine page.</p></body></html>')
    w(d / "web/ok.css", "p { color: #333; }\n")
    w(d / "safe_api.py", "import requests\n\n\ndef pull(token):\n"
                         "    return requests.get('https://api.example.com/v1/items', headers={'Authorization': token}, timeout=10)\n")
    w(d / "names.py", "def describe(cat):\n    return 'the cat is ' + cat\n")
    # HTML the browser quietly rewrites, and a page whose script crashes while loading
    w(d / "web/about.html", '<!doctype html><html lang="en"><head><title>About</title></head><body>\n'
                            '<p class="lead">Our story\n  <div class="note">since 2020</div>\n</p>\n'
                            '<p>Price &nbps; 10</p>\n<script src="about.js"></script>\n</body></html>\n')
    w(d / "web/about.js", 'const box = document.getElementById("story");\nbox.textContent = "Hello";\n')
    w(d / "web/about_ok.html", '<!doctype html><html lang="en"><head><title>About</title></head><body>\n'
                               '<div class="lead">Our story\n  <div class="note">since 2020</div>\n</div>\n'
                               '<p>One<p>Two &nbsp; &copy; 2024 R&amp;D</p>\n<a href="/x"><div>whole card</div></a>\n'
                               '<table><tr><td>1</td></tr></table>\n<script src="about_ok.js"></script>\n</body></html>\n')
    w(d / "web/about_ok.js", 'const box = document.querySelector(".lead");\nif (box) box.title = "Hello";\n')
    # works on Windows, 404 on a Linux server; a switch that stays on; text saved in the wrong encoding
    w(d / "web/img/logo.png", "png")
    w(d / "web/contact.html", '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Contact</title></head><body>\n'
                              '<img src="img/Logo.PNG" alt="logo">\n<button type="button" disabled="false">Send</button>\n'
                              '<p>We\u00e2\u20ac\u2122re open every day</p>\n</body></html>\n')
    w(d / "web/contact_ok.html", '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Contact</title></head><body>\n'
                                 '<img src="img/logo.png" alt="logo">\n<button type="button" disabled>Send</button>\n'
                                 '<p>We\u2019re open every day \u2014 caf\u00e9</p>\n'
                                 '<form action="/up" method="post" enctype="multipart/form-data"><input type="file" name="f"></form>\n</body></html>\n')
    # a page using an installed package and an inline script that sets .src from a variable (both fine)
    w(d / "package.json", json.dumps({"name": "demo", "private": True, "dependencies": {"@fontsource/press-start-2p": "5.0.0"}}))
    w(d / "web/player.html", '<!doctype html><html><head><link rel="stylesheet" href="../node_modules/@fontsource/press-start-2p/index.css">'
                             '</head><body><p>Player</p><script>function play(songs, i, track) { const a = new Audio(); a.src = songs[i].blob; a.src = track.url; }</script></body></html>')


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
    ("payment without an amount check", "shop.py", {"money"}, ["above 0"]),
    ("always-true `or \"y\"` condition", "orders.py", {"bugs"}, ["always true"]),
    ("JavaScript return before a line break", "web/cart.js", {"bugs"}, ["return"]),
    ("misspelled CSS property", "web/theme.css", {"bugs"}, ["colr"]),
    ("Markdown link to a missing file", "docs/links.md", {"bugs"}, ["setup.md"]),
    ("block inside a paragraph (browser ends the <p> early)", "web/about.html", {"bugs"}, ["paragraph"]),
    ("misspelled character code &nbps;", "web/about.html", {"bugs"}, ["nbps"]),
    ("JavaScript error while the page loads (real browser)", "web/about.js", {"pages"}, ["null"]),
    ("file path whose capital letters differ (404 on Linux servers)", "web/contact.html", {"links"}, ["capital letters"]),
    ("disabled=\"false\" still disables", "web/contact.html", {"bugs"}, ["still on"]),
    ("garbled characters (wrong encoding)", "web/contact.html", {"bugs"}, ["garbled"]),
    ("SQL injection from a web request", "server/users.py", {"security"}, ["database query"]),
    ("visitor text put into the page as HTML", "web/comments.js", {"security"}, ["html"]),
    ("AWS key written in the code", "server/keys.py", {"security"}, ["aws"]),
    ("delete route with no login check", "server/routes.js", {"security"}, ["login"]),
]
DECOYS = ["server/users_ok.py", "web/comments_ok.js", "server/keys_ok.py", "server/routes_ok.js", "orders_ok.py", "web/cart_ok.js", "web/theme_ok.css", "docs/links_ok.md", "shop_ok.py", "docs/clean.md", "good.py", "good.js", "web/ok.html", "web/ok.css", "safe_api.py", "names.py", "web/player.html", "web/about_ok.html", "web/about_ok.js", "web/contact_ok.html"]
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
