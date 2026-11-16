#!/usr/bin/env python3
"""
ARACHNID - REAL-WORLD REGRESSION TEST
=====================================
    python qa_regressions.py

Every false alarm a real person reported becomes a permanent check here, so it can never come back.
Each case is taken from a real project; next to it sits a REAL problem of the same shape that must still be found.

  1. pixel-tunes-app (Electron music player): `if(!force&&key===lastKey&&...)` was raised as an ALARM
     "secret key in an address" - it is JavaScript (&& and ===), not an address.
     Still found: a real key inside an address  ?city=pune&key=a8f3k29dk3ld9
  2. pixel-tunes-app: "dist" and "playlist" were flagged as spelling mistakes.
     Still found: a real misspelling ("recieve").
  3. pixel-tunes-app: dist/win-unpacked/LICENSES.chromium.html (Electron's own licence file inside the packaged app)
     was weighed as a too-heavy web page.
     Still found: a real web page in dist/ that is too heavy.
  4. Scanning one project after another: a checker that found nothing in the second project left the FIRST project's
     report behind (a nested folder from project A showed up in project B).
     Still found: the nested folder when project A itself is scanned.
  5. ponytail (AI agent plugin) and Quarkus Dev UI: six text/Markdown/package.json false alarms and 29 wrong "dead code"
     warnings - a file inside a hidden folder (.opencode/) called missing, "</a>." read as "space before a full stop",
     "one email to many subscribers" read as "too many", "Claude Code `2.1`" read as a repeated word, a line of inline
     code that starts with ```` read as an unclosed code block, "../benchmarks/" read as doubled punctuation, and the
     text of a multi-line return html`...` read as code that can never run.
     Still found: a real missing file, a real space before a comma, "this is to much", "the the", a real unclosed code
     block, ",,", real code after a return - and Quarkus's two real broken translation files.
  6. Field test (docsify, bootstrap, alpine, jotai, express, mkdocs): VALID files called broken - and some offered a
     "repair" that would have damaged them: a tsconfig.json with comments (JSONC is allowed there), a Node script with a
     top-level `return` and a variable named `package` (CommonJS), React JSX in a .js file, an EJS partial
     (footer.html = "</body></html>") and a Jinja template.
     Still found: a tsconfig.json that is really broken, a .js file that is really broken, a package.json with a
     comment (npm refuses it) and a real page with a stray </div>.
"""
import base64
import json
import os
import posixpath
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
LEDGER = HERE / "reports" / "ledger.jsonl"
OK, BAD = [], []


def check(name, cond, detail=""):
    (OK if cond else BAD).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (("\n        " + detail) if detail and not cond else ""))


def heavy(n_bytes):
    return base64.b64encode(os.urandom(n_bytes)).decode()      # random text: it does not compress


PAGE = '''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Pixel Tunes</title>
</head>
<body>
  <p id="now">Ready</p>
  <script>
    let lastKey = "", lastPush = 0;
    function pushState(title, playing, force) {
      const key = title + "|" + playing;
      const now = Date.now();
      if(!force&&key===lastKey&&now-lastPush<1000)return;
      lastKey = key; lastPush = now;
      document.getElementById("now").textContent = title;
    }
    pushState("Song", true, false);
  </script>
</body>
</html>
'''


def main():
    tmp = Path(tempfile.mkdtemp(prefix="arachnid-regress-"))
    try:
        app = tmp / "pixel-tunes"
        (app / "dist" / "win-unpacked").mkdir(parents=True)
        (app / "index.html").write_text(PAGE, encoding="utf-8")
        (app / "README.txt").write_text("Pixel Tunes\n\nBuild the app with npm run build. The finished app is in the dist folder.\n"
                                        "Drop songs into the window to make a playlist.\n", encoding="utf-8")
        (app / "dist" / "win-unpacked" / "LICENSES.chromium.html").write_text(
            "<!doctype html><html><head><title>Licenses</title></head><body><pre>" + heavy(1_500_000) + "</pre></body></html>",
            encoding="utf-8")
        # the same shapes, but REAL problems - they must still be found
        real = tmp / "real-problems"
        (real / "dist").mkdir(parents=True)
        (real / "weather.js").write_text('fetch("https://api.weather.example.org/v1/now?city=pune&key=a8f3k29dk3ld9")\n'
                                         '  .then((r) => r.json());\n', encoding="utf-8")
        (real / "README.txt").write_text("Weather\n\nYou will recieve the forecast every morning.\n", encoding="utf-8")
        (real / "dist" / "index.html").write_text(
            '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Weather</title></head><body><p>'
            + heavy(1_500_000) + "</p></body></html>", encoding="utf-8")

        def scan(root):
            env = dict(os.environ, PYTHONIOENCODING="utf-8")
            subprocess.run([sys.executable, str(HERE / "arachnid.py"), "scan", str(root)], cwd=str(HERE), capture_output=True,
                           text=True, encoding="utf-8", errors="replace", env=env, timeout=1800)
            if not LEDGER.exists():
                return []                                   # nothing found at all
            return [json.loads(ln) for ln in LEDGER.read_text(encoding="utf-8").splitlines() if ln.strip()]

        print("Case: pixel-tunes-app (what was wrongly reported)")
        got = scan(app)
        secret = [e for e in got if e.get("kind") == "secret-in-url"]
        check("`key===lastKey` is not reported as a secret", not secret, str(secret))
        spell = [e for e in got if e.get("tool") == "spelling" and any(w in (e.get("what") or "") for w in ("dist ", "playlist"))]
        check("'dist' and 'playlist' are not spelling mistakes", not spell, str(spell))
        budget = [e for e in got if e.get("kind") == "page-budget" and "win-unpacked" in (e.get("file") or "")]
        check("Electron's packaged app (win-unpacked) is not weighed as a web page", not budget, str(budget))

        print("Case: the same shapes, but real problems")
        got = scan(real)
        check("a real key inside an address is still an alarm",
              any(e.get("kind") == "secret-in-url" and e.get("severity") == "critical" for e in got),
              str([e for e in got if e.get("tool") == "net"]))
        check("a real misspelling is still found", any(e.get("tool") == "spelling" and "recieve" in (e.get("what") or "") for e in got))
        check("a too-heavy web page in dist/ is still weighed", any(e.get("kind") == "page-budget" and "dist/index.html" in (e.get("file") or "") for e in got),
              str([e for e in got if e.get("tool") == "bundle"]))

        print("Case: one project after another")
        a_proj = tmp / "project-a"
        (a_proj / "site" / "site").mkdir(parents=True)
        (a_proj / "site" / "site" / "notes.txt").write_text("Notes for the site.\n", encoding="utf-8")
        (a_proj / "site" / "about.txt").write_text("About the site.\n", encoding="utf-8")
        got = scan(a_proj)
        check("project A: its nested folder is found", any(e.get("kind") == "nested-folder" for e in got),
              str([e.get("kind") for e in got]))
        got = scan(app)
        check("project B right after: project A's nested folder does NOT appear", not any(e.get("kind") == "nested-folder" for e in got),
              str([e for e in got if e.get("kind") == "nested-folder"]))

        print("Case: ponytail and Quarkus Dev UI (what was wrongly reported)")
        pt = tmp / "ponytail"
        (pt / ".opencode" / "plugins").mkdir(parents=True)
        (pt / ".opencode" / "plugins" / "plugin.mjs").write_text("export const Plugin = async () => ({});\n", encoding="utf-8")
        (pt / "package.json").write_text(json.dumps({"name": "ponytail", "version": "1.0.0", "main": "./.opencode/plugins/plugin.mjs"},
                                                   indent=2) + "\n", encoding="utf-8")
        (pt / "README.md").write_text(
            "# Ponytail\n\n"
            "Benchmarked on 39 tasks, five runs each. <a href=\"#numbers\">Details</a>.\n\n"
            "It handles a privacy case: one email to many subscribers without leaking addresses.\n\n"
            "The engine was Claude Code `2.1.177`, run headless.\n\n"
            "The raw numbers are in [../benchmarks/](../benchmarks/).\n\n"
            "## Numbers\n\n"
            "The old gate only matched\n"
            "   ```` ```fenced``` ```` blocks, so bare code failed.\n\n"
            "```js\nconsole.log(1);\n```\n", encoding="utf-8")
        (pt / "page.js").write_text(
            "import { html } from 'lit';\n\n"
            "export function render(enabled) {\n"
            "    if (!enabled) {\n"
            "        return html`\n"
            "        Build metrics not enabled.\n"
            "        `;\n"
            "    } else {\n"
            "        return html`\n"
            "        <p>Ready</p>\n"
            "        `;\n"
            "    }\n"
            "}\n", encoding="utf-8")
        got = scan(pt)
        def kinds(*ks):
            return [(e.get("kind"), e.get("file"), e.get("line"), (e.get("what") or "")[:60]) for e in got if e.get("kind") in ks]
        check("a file in a hidden folder (.opencode/) is not called missing", not kinds("json-missing-file"), str(kinds("json-missing-file")))
        check("'</a>.' is not a space before a full stop", not kinds("space-before-punct"), str(kinds("space-before-punct")))
        check("'one email to many subscribers' is not 'too many'", not kinds("to-much"), str(kinds("to-much")))
        check("'Claude Code `2.1.177`' is not a repeated word", not kinds("repeated-word"), str(kinds("repeated-word")))
        check("a line of inline code starting with ```` is not an unclosed code block", not kinds("md-unclosed-fence", "syntax"),
              str(kinds("md-unclosed-fence", "syntax")))
        check("'../benchmarks/' is not doubled punctuation", not kinds("double-punct"), str(kinds("double-punct")))
        check("the text of a multi-line `return html`...`` is not dead code", not kinds("dead-code"), str(kinds("dead-code")))

        print("Case: the same shapes, but real problems")
        rp = tmp / "real-problems-2"
        (rp / ".opencode").mkdir(parents=True)
        (rp / "i18n").mkdir()
        (rp / "package.json").write_text(json.dumps({"name": "plugin", "version": "1.0.0", "main": "./.opencode/plugins/gone.mjs"},
                                                   indent=2) + "\n", encoding="utf-8")
        (rp / "README.md").write_text(
            "# Plugin\n\n"
            "Hello , this plugin is small.\n\n"
            "Setting it up is to much work for most people.\n\n"
            "Read the the guide first.\n\n"
            "Lists use commas,, not semicolons.\n\n"
            "```js\nconsole.log(1);\n", encoding="utf-8")
        (rp / "page.js").write_text(
            "export function total(items) {\n"
            "    return items.length;\n"
            "    console.log(items);\n"
            "}\n", encoding="utf-8")
        (rp / "i18n" / "fr-CA.js").write_text("export const templates = {\n    'info-arch': 'Arche\n\n',\n    'info-commit-id': 'ID',\n};\n",
                                              encoding="utf-8")
        (rp / "i18n" / "fr-BE.js").write_text("export const templates = {\n    'info-name': 'Nom',\n    'info-cleared': 'Vid\u00e9'\n",
                                              encoding="utf-8")
        got = scan(rp)
        check("a package.json pointing to a file that is really missing is still found", bool(kinds("json-missing-file")))
        check("a real space before a comma is still found", bool(kinds("space-before-punct")))
        check("'is to much' is still found", bool(kinds("to-much")))
        check("'the the' is still found", bool(kinds("repeated-word")))
        check("',,' is still found", bool(kinds("double-punct")))
        check("a real unclosed code block is still found", bool(kinds("md-unclosed-fence", "syntax")))
        check("real code after a return is still found", any(e.get("kind") in ("dead-code", "js-unreachable") for e in got),
              str(sorted({e.get("kind") for e in got})))
        broken = {posixpath.basename(e.get("file") or "") for e in got if e.get("kind") == "syntax"}
        check("Quarkus's two broken translation files (a split text, a cut-off file) are still found",
              {"fr-CA.js", "fr-BE.js"} <= broken, str(broken))

        print("Case: valid files that were called broken (field test)")
        vf = tmp / "valid-files"
        for d in (".vscode", "scripts", "src", "views", "theme"):
            (vf / d).mkdir(parents=True)
        (vf / "tsconfig.json").write_text('{\n  "compilerOptions": {\n    "target": "es2022", // modern browsers only\n'
                                          '    /* strict type checks */\n    "strict": true,\n  },\n  "include": ["src/**/*"],\n}\n',
                                          encoding="utf-8")
        (vf / ".vscode" / "launch.json").write_text('{\n  // debug the server\n  "version": "0.2.0",\n  "configurations": [],\n}\n',
                                                    encoding="utf-8")
        (vf / "scripts" / "release.js").write_text(
            "const fs = require('fs')\nlet version = process.argv[2]\n\nif (! version) {\n"
            "    return console.log('pass a version number')\n}\n\n['core', 'ui'].forEach(package => {\n"
            "    console.log(package, fs.existsSync(`./packages/${package}`))\n})\n", encoding="utf-8")
        (vf / "src" / "button.js").write_text(
            "import React from 'react'\n\nexport default function Button({ label, onClick }) {\n  return (\n"
            "    <button className=\"btn\" onClick={onClick}>\n      {label}\n    </button>\n  )\n}\n", encoding="utf-8")
        (vf / "views" / "footer.html").write_text("</body>\n</html>\n", encoding="utf-8")
        (vf / "theme" / "base.html").write_text(
            "<!DOCTYPE html>\n<html>\n<body{% if page %} class=\"page\"{% endif %}>\n  {% block content %}{% endblock %}\n"
            "  <script>gtag('config', {{ config.gtag|tojson }});</script>\n"
            "  <ul{% if nav %} class=\"nav\"{% endif %}>\n    {% for item in nav %}<li>{{ item }}</li>{% endfor %}\n  </ul>\n"
            "</body>\n</html>\n", encoding="utf-8")
        before = {q.relative_to(vf).as_posix(): q.read_bytes() for q in vf.rglob("*") if q.is_file()}
        got = scan(vf)
        syn = [(e.get("file"), e.get("line"), (e.get("what") or "")[:70]) for e in got if e.get("kind") == "syntax"]
        def syn_in(name):
            return [s for s in syn if (s[0] or "").endswith(name)]
        check("tsconfig.json with comments and trailing commas (JSONC) is not broken", not syn_in("tsconfig.json"), str(syn_in("tsconfig.json")))
        check(".vscode/launch.json with comments is not broken", not syn_in("launch.json"), str(syn_in("launch.json")))
        check("a CommonJS script (top-level return, a variable named package) is not broken", not syn_in("release.js"), str(syn_in("release.js")))
        check("React JSX in a .js file is not broken", not syn_in("button.js"), str(syn_in("button.js")))
        check("an EJS partial ('</body></html>') is not broken", not syn_in("footer.html"), str(syn_in("footer.html")))
        check("a Jinja template is not broken", not syn_in("base.html"), str(syn_in("base.html")))
        check("nothing in these valid files was changed", before == {q.relative_to(vf).as_posix(): q.read_bytes() for q in vf.rglob("*") if q.is_file()})

        print("Case: the same shapes, but really broken")
        rb = tmp / "really-broken"
        (rb / "scripts").mkdir(parents=True)
        (rb / "tsconfig.json").write_text('{\n  "compilerOptions": {\n    "target": "es2022", // modern browsers only\n'
                                          '    "strict": true\n  \n  "include": ["src"]\n}\n', encoding="utf-8")
        (rb / "package.json").write_text('{\n  // npm cannot read comments\n  "name": "shop",\n  "version": "1.0.0"\n}\n', encoding="utf-8")
        (rb / "scripts" / "release.js").write_text("const fs = require('fs')\nfunction run(version {\n  return fs.existsSync(version)\n}\n",
                                                   encoding="utf-8")
        (rb / "index.html").write_text('<!doctype html>\n<html lang="en">\n<head><meta charset="utf-8"><title>Shop</title></head>\n'
                                       '<body>\n  <div>\n    <p>Hi</p>\n  </div>\n  </div>\n</body>\n</html>\n', encoding="utf-8")
        got = scan(rb)
        syn = [(e.get("file"), e.get("line"), (e.get("what") or "")[:70]) for e in got if e.get("kind") == "syntax"]
        check("a tsconfig.json that is really broken is still found", bool(syn_in("tsconfig.json")), str(syn))
        check("a package.json with a comment (npm refuses it) is still found", bool(syn_in("package.json")), str(syn))
        check("a .js file that is really broken is still found", bool(syn_in("release.js")), str(syn))
        check("a real page with a stray </div> is still found", bool(syn_in("index.html")), str(syn))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\nREGRESSION TEST: %d passed, %d failed" % (len(OK), len(BAD)))
    return 1 if BAD else 0


if __name__ == "__main__":
    sys.exit(main())
