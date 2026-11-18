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
  7. Quarkus Dev UI (second scan): a fetch whose .catch sits 25 lines further down its .then() chain, a fetch whose
     answer is checked with `if (!response.ok)`, a deliberate one-line "ping" fetch, and localhost inside Dev UI (which
     only runs on the developer's own computer) were all raised as warnings.
     Still found: a fetch with no handling at all, localhost in real app code - and a MISSED leak, now found: one
     revokeObjectURL in a file no longer hides a second object URL that is never revoked.
  8. Quarkus, the rest of the repo: Qute server templates (`{#for}`, `href="{contextRoot}favicon.ico"`) raised as
     broken pages with missing files, `classList.replace(a, b)` called "a line that does nothing", the light and dark
     logos side by side called "Quarkus Quarkus", "tested in the <a>Dev UI</a>" called unfinished, and
     `return fetch(...)` (the caller handles errors) called unhandled.
     Still found: a real missing file and a real broken tag in a normal page, `name.trim();` doing nothing, a sentence
     that really stops on "the", and a repeated word in real text.
  9. Keycloak: `onSubmit={handleSubmit(save)}` (react-hook-form returns the handler) raised 100 times as a handler that
     runs immediately; a heading underlined with ----- not seen, so its #anchor looked broken; `return function
     cancelTimeout()` called never used; `await fetch` in an async function that returns its result called unhandled;
     localhost in the dev server's own start script.
     Still found: `onClick={save()}`, a link to a heading that really is missing, a function nobody uses, an awaited
     fetch whose error nobody gets, localhost in app code - and Keycloak's real `.forEach(async ...)`.
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

        print("Case: Quarkus Dev UI, second scan (what was wrongly reported)")
        dv = tmp / "devui-app"
        (dv / "src" / "main" / "resources" / "dev-ui").mkdir(parents=True)
        (dv / "src" / "main" / "resources" / "dev-ui" / "qwc-provider.js").write_text(
            "export class Provider {\n"
            "    static check(onIn, onOut) {\n"
            "        fetch(window.location.origin + '/session')\n"
            "            .then(response => response.json())\n"
            "            .then(result => {\n" + "                this.step(result);\n" * 22 +
            "                onIn();\n"
            "            })\n"
            "            .catch(error => {\n"
            "                console.warn(error);\n"
            "                onOut();\n"
            "            });\n"
            "    }\n\n"
            "    async load() {\n"
            "        const response = await fetch('/endpoints/routes.json');\n"
            "        if (!response.ok) {\n"
            "            this.failed = true;\n"
            "            return;\n"
            "        }\n"
            "        this.routes = await response.json();\n"
            "    }\n\n"
            "    saved(root) {\n"
            "        fetch(root);\n"
            "        this.notify('saved');\n"
            "    }\n\n"
            "    static serviceUrl(port, path) {\n"
            "        return 'http://localhost:' + port + path;\n"
            "    }\n\n"
            "    download(blob) {\n"
            "        const url = URL.createObjectURL(blob);\n"
            "        const a = document.createElement('a');\n"
            "        a.href = url;\n"
            "        a.click();\n"
            "        URL.revokeObjectURL(url);\n"
            "    }\n"
            "}\n", encoding="utf-8")
        got = scan(dv)
        check("a fetch whose .catch is far down its .then() chain is handled", not kinds("no-error-handling"), str(kinds("no-error-handling")))
        check("localhost inside Dev UI (runs on the developer's computer) is fine", not kinds("localhost-left"), str(kinds("localhost-left")))
        check("an object URL revoked right after the click is not a leak", not [e for e in got if e.get("kind") == "leak"],
              str([e for e in got if e.get("kind") == "leak"]))

        print("Case: the same shapes, but real problems")
        rv = tmp / "real-app"
        (rv / "src").mkdir(parents=True)
        (rv / "src" / "api.js").write_text(
            "export function loadItems(show) {\n"
            "    fetch('/api/items')\n"
            "        .then(response => response.json())\n"
            "        .then(items => show(items));\n"
            "}\n\n"
            "export function serviceUrl(port) {\n"
            "    return 'http://localhost:' + port + '/api';\n"
            "}\n\n"
            "export function save(blob) {\n"
            "    const url = URL.createObjectURL(blob);\n"
            "    const a = document.createElement('a');\n"
            "    a.href = url;\n"
            "    a.click();\n"
            "    URL.revokeObjectURL(url);\n"
            "}\n" + "\n" * 40 +
            "export function cell(value) {\n"
            "    const url = URL.createObjectURL(new Blob([value]));\n"
            "    return '<a href=\"' + url + '\" download>download</a>';\n"
            "}\n", encoding="utf-8")
        got = scan(rv)
        check("a fetch with no error handling at all is still found", bool(kinds("no-error-handling")), str(sorted({e.get('kind') for e in got})))
        check("localhost in real app code is still found", bool(kinds("localhost-left")), str(sorted({e.get('kind') for e in got})))
        leak = [e for e in got if e.get("kind") == "leak" and "object URL" in (e.get("what") or "")]
        check("an object URL that is never revoked is found even when another one in the file is",
              bool(leak) and leak[0].get("line") == 59, str(leak))

        print("Case: Quarkus, rest of the repo (what was wrongly reported)")
        qt = tmp / "server-templates"
        (qt / "dev-ui-templates" / "build-time").mkdir(parents=True)
        (qt / "web").mkdir()
        (qt / "dev-ui-templates" / "build-time" / "index.html").write_text(
            '<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n<title>Dev UI</title>\n'
            '<link rel="shortcut icon" type="image/png" href="{contextRoot}favicon.ico">\n'
            '<script type="importmap">\n{importmap}\n</script>\n</head>\n<body>\n'
            '<img src="quarkus-logo.png" alt="Quarkus">\n<div>\n</body>\n</html>\n', encoding="utf-8")
        (qt / "dev-ui-templates" / "build-time" / "build-time-data.js").write_text(
            "// Generated by Quarkus during Build\n{#for d in buildTimeData}\nexport const {d.key} = {d.value};\n{/for}\n",
            encoding="utf-8")
        (qt / "README.md").write_text(
            "[![Quarkus](https://example.org/logo-light.png#gh-light-mode-only)](https://quarkus.io/#gh-light-mode-only)\n"
            "[![Quarkus](https://example.org/logo-dark.png#gh-dark-mode-only)](https://quarkus.io/#gh-dark-mode-only)\n\n"
            "Quarkus is a Java framework for building small and fast services.\n", encoding="utf-8")
        (qt / "web" / "index.html").write_text(
            '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n<title>Services</title>\n</head>\n<body>\n'
            '<p>Your service is ready. It can be tested in the <a href="/q/dev-ui">Dev UI</a> (available in dev mode only).</p>\n'
            '<button id="more">Show more</button>\n<i id="icon" class="fa-chevron-down"></i>\n'
            '<script src="toggle.js"></script>\n</body>\n</html>\n', encoding="utf-8")
        (qt / "web" / "toggle.js").write_text(
            "const icon = document.getElementById('icon');\n"
            "document.getElementById('more').addEventListener('click', () => {\n"
            "    icon.classList.replace('fa-chevron-down', 'fa-chevron-up');\n"
            "});\n\n"
            "export function fetchWithToken(path, options) {\n"
            "    options.headers = { Authorization: 'Bearer x' };\n"
            "    return fetch(path, options);\n"
            "}\n", encoding="utf-8")
        got = scan(qt)
        tpl = [(e.get("kind"), e.get("file"), e.get("line")) for e in got if "dev-ui-templates" in (e.get("file") or "")
               and e.get("severity") != "info"]
        check("a Qute server template is not called broken, and its files are not called missing", not tpl, str(tpl))
        check("classList.replace(a, b) is not 'a line that does nothing'", not kinds("js-no-effect"), str(kinds("js-no-effect")))
        check("two logos (light and dark) side by side are not 'Quarkus Quarkus'", not kinds("repeated-word"), str(kinds("repeated-word")))
        check("a sentence that goes on inside a link is not unfinished", not kinds("ends-mid-thought"), str(kinds("ends-mid-thought")))
        check("`return fetch(...)` hands its errors to the caller", not kinds("no-error-handling"), str(kinds("no-error-handling")))

        print("Case: the same shapes, but real problems")
        rq = tmp / "real-pages"
        rq.mkdir()
        (rq / "index.html").write_text(
            '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n<title>Shop</title>\n</head>\n<body>\n'
            '<img src="logo.png" alt="Shop">\n<p>To place your order now, please click the</p>\n<p>Read the the guide first, it is short.</p>\n'
            '<div>\n<p>Hi</p>\n</div>\n</div>\n<script src="app.js"></script>\n</body>\n</html>\n', encoding="utf-8")
        (rq / "app.js").write_text("const name = ' Asha ';\nname.trim();\nconsole.info(name);\n", encoding="utf-8")
        got = scan(rq)
        check("a real missing picture in a normal page is still found",
              any("logo.png" in (e.get("what") or "") for e in got if e.get("tool") == "links"), str(sorted({e.get('kind') for e in got})))
        check("a real stray </div> in a normal page is still found", any(e.get("kind") == "syntax" for e in got))
        check("`name.trim();` doing nothing is still found", bool(kinds("js-no-effect")), str(sorted({e.get('kind') for e in got})))
        check("a sentence that really stops on 'the' is still found", bool(kinds("ends-mid-thought")), str(sorted({e.get('kind') for e in got})))
        check("a repeated word in real text is still found", bool(kinds("repeated-word")), str(sorted({e.get('kind') for e in got})))

        print("Case: Keycloak (what was wrongly reported)")
        kc = tmp / "keycloak-like"
        (kc / "src").mkdir(parents=True)
        (kc / "scripts").mkdir()
        (kc / "package.json").write_text(json.dumps({"name": "admin-ui", "private": True, "type": "module"}) + "\n", encoding="utf-8")
        (kc / "src" / "RealmForm.tsx").write_text(
            "import { useForm } from 'react-hook-form';\n\n"
            "export function RealmForm({ save }: { save: (v: unknown) => void }) {\n"
            "  const { handleSubmit, register } = useForm();\n"
            "  const form = useForm();\n"
            "  return (\n"
            "    <>\n"
            "      <form onSubmit={handleSubmit(save)}>\n"
            "        <input {...register('name')} />\n"
            "      </form>\n"
            "      <form onSubmit={form.handleSubmit(save)} />\n"
            "    </>\n"
            "  );\n"
            "}\n", encoding="utf-8")
        (kc / "src" / "useSetTimeout.ts").write_text(
            "export function useSetTimeout() {\n"
            "  return (callback: () => void, delay: number) => {\n"
            "    const timer = setTimeout(callback, delay);\n"
            "    return function cancelTimeout() {\n"
            "      clearTimeout(timer);\n"
            "    };\n"
            "  };\n"
            "}\n", encoding="utf-8")
        (kc / "src" / "fetchContent.ts").write_text(
            "export default async function fetchContentJson(url: string): Promise<unknown> {\n"
            "  const response = await fetch(url + '/content.json');\n"
            "  return await response.json();\n"
            "}\n", encoding="utf-8")
        (kc / "scripts" / "start-server.js").write_text(
            "const ADMIN_URL = 'http://localhost:5173';\nconsole.info('Admin console on ' + ADMIN_URL);\n", encoding="utf-8")
        (kc / "TESTS.md").write_text(
            "# Tests\n\nKeycloak server\n---------------\n\nStart the server first.\n\n"
            "Testing mail\n------------\n\nNeeds the Keycloak server running (see [Keycloak server](#keycloak-server) above).\n",
            encoding="utf-8")
        got = scan(kc)
        check("onSubmit={handleSubmit(save)} is not a handler that runs immediately", not kinds("js-handler-called"), str(kinds("js-handler-called")))
        check("a heading underlined with ----- has its #anchor", not kinds("md-broken-anchor"), str(kinds("md-broken-anchor")))
        check("`return function cancelTimeout()` is not dead code", not [e for e in got if e.get("kind") == "dead-code" and "cancelTimeout" in (e.get("what") or "")],
              str([e.get("what") for e in got if e.get("kind") == "dead-code"]))
        check("an awaited fetch whose result is returned hands errors to the caller", not kinds("no-error-handling"), str(kinds("no-error-handling")))
        check("localhost in the dev server's start script is fine", not kinds("localhost-left"), str(kinds("localhost-left")))

        print("Case: the same shapes, but real problems")
        rk = tmp / "real-admin"
        (rk / "src").mkdir(parents=True)
        (rk / "package.json").write_text(json.dumps({"name": "shop-admin", "private": True, "type": "module"}) + "\n", encoding="utf-8")
        (rk / "src" / "Save.tsx").write_text(
            "export function Save({ save }: { save: () => void }) {\n"
            "  return <button onClick={save()}>Save</button>;\n"
            "}\n", encoding="utf-8")
        (rk / "src" / "groups.ts").write_text(
            "import { api } from './api';\n\n"
            "export async function addGroups(ids: string[]): Promise<void> {\n"
            "  ids.forEach(async (id) => {\n"
            "    await api.addToGroup(id);\n"
            "  });\n"
            "}\n\n"
            "export async function showItems(show: (x: unknown) => void) {\n"
            "  const r = await fetch('/api/items');\n"
            "  show(await r.json());\n"
            "}\n\n"
            "export const API = 'http://localhost:8080/api';\n\n"
            "function formatOldPrice(n: number) {\n"
            "  return n.toFixed(2);\n"
            "}\n", encoding="utf-8")
        (rk / "src" / "api.ts").write_text("export const api = { addToGroup: async (id: string) => id };\n", encoding="utf-8")
        (rk / "README.md").write_text("# Admin\n\nSee [Setup](#setup) first.\n\nUsage\n-----\n\nRun it.\n", encoding="utf-8")
        got = scan(rk)
        check("onClick={save()} is still found", bool(kinds("js-handler-called")), str(sorted({e.get('kind') for e in got})))
        check("a link to a heading that really is missing is still found", bool(kinds("md-broken-anchor")), str(sorted({e.get('kind') for e in got})))
        check("a function nobody uses is still found",
              any(e.get("kind") == "dead-code" and "formatOldPrice" in (e.get("what") or "") for e in got), str(sorted({e.get('kind') for e in got})))
        check("an awaited fetch whose error nobody gets is still found", bool(kinds("no-error-handling")), str(sorted({e.get('kind') for e in got})))
        check("localhost in app code is still found", bool(kinds("localhost-left")), str(sorted({e.get('kind') for e in got})))
        check("Keycloak's real `.forEach(async ...)` is still found", bool(kinds("js-foreach-async")), str(sorted({e.get('kind') for e in got})))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\nREGRESSION TEST: %d passed, %d failed" % (len(OK), len(BAD)))
    return 1 if BAD else 0


if __name__ == "__main__":
    sys.exit(main())
