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
 10. Quarkus web UI, third scan: `var(--lumo-contrast-20pct)` called undefined although the page links lumo.css (through a
     server path the scan cannot open), and z-index on `#swagger-ui .download-url-input` called useless although Swagger
     builds that markup (in a flex box) itself. Real counterparts: a misspelled variable of the project's own, and z-index
     without position on the project's own markup.
 11. ASML dagster-delta: "try the new import, else the old one" (try/except and if/else fallbacks) called an
     overwritten import (so was `import click` + `import click.core`), and a list default that is only looped over called a shared mutable default.
     Real counterparts: two plain imports of the same name, and list/dict defaults that are changed, stored into,
     returned or handed on.
 12. ASML dagster-uc: kube / kwargs / buildah / acr / webserver called typos (kube -> tube), "noone" offered the
     fix "none" instead of "no one", and `from kr8s.objects import Role as Role` (the standard "shared on purpose"
     form) called an unused import. Real counterparts: a real typo next to them, and a plain unused import.
 13. ASML Julia/Bazel repos: a rude word offered as a spelling fix ("dict" -> ...), hundreds of "typos" in a bundled
     highlight.pack.js and in word lists (nouns.txt), "an XML" called wrong, Franklin.jl site pieces (_layout/,
     ~~~<h4>..</h4>~~~, /libs/ built from _libs/) called broken, and numbered steps / <pre> code / sentences whose
     `code` differs called repeated sentences. Real counterparts: "an new", a real typo, a truly repeated sentence.
 14. Quarkus, full scan: ~1,400 "typos" in the Dev UI translation files (i18n/de.js, fr.js, pt-BR.js ...) and in
     words with accents (Priorität -> "Priorit"), the Maven option `-Dquickly`, "log in in the browser" (a two-word verb)
     and "day-of-month month" called repeated words - and "log in in" would even have been auto-fixed. Real counterparts:
     typos in the English file next to them, a real "the the".
 15. Angular, full scan: ~1,480 "broken links" that are angular.dev routes written from the content root
     (guide/di/overview, /api/core/inject), an anchor made by <docs-step title="..."> called missing, an icon font
     word next to its label ("edit Edit"), package names in <docs-code> ("@tailwindcss/postcss postcss"), JSDoc
     "@see See", "</a>." on a new line read as " .", and TypeScript's ts.createProgram() called a WebGL leak.
     Real counterparts: a link to a missing .md file, a missing heading, a real "run run", a real WebGL leak.
"""
import base64
import json
import os
import posixpath
import re
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

        print("Case: Quarkus web UI, third scan (what was wrongly reported)")
        qw = tmp / "quarkus-web"
        (qw / "dev-ui").mkdir(parents=True)
        (qw / "branding").mkdir(parents=True)
        (qw / "dev-ui" / "index.html").write_text(
            "<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n<title>Dev UI</title>\n"
            "<link rel=\"stylesheet\" href=\"{nonApplicationRoot}_static/at/vaadin/vaadin-lumo-styles/{vaadinVersion}/lumo.css\">\n"
            "<style>\n:root { --lumo-contrast-70pct: rgba(0,0,0,.7); }\n"
            "::-webkit-scrollbar-thumb { background: var(--lumo-contrast-20pct); }\n</style>\n</head>\n"
            "<body><main><h1>Dev UI</h1></main></body>\n</html>\n", encoding="utf-8")
        (qw / "branding" / "open-api-ui.css").write_text(
            "#swagger-ui .download-url-input {\n    z-index: 1;\n    min-width: 150px;\n}\n", encoding="utf-8")
        got = scan(qw)
        check("--lumo-* from the linked lumo.css is defined", not kinds("css-undefined-var"), str(kinds("css-undefined-var")))
        check("z-index on a library widget (#swagger-ui ...) is not reported", not kinds("css-zindex-no-position"),
              str(kinds("css-zindex-no-position")))

        print("Case: the same shapes, but real problems")
        rw = tmp / "real-web"
        rw.mkdir()
        (rw / "index.html").write_text(
            "<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n<title>Shop</title>\n"
            "<link rel=\"stylesheet\" href=\"https://cdn.example.com/lumo.css\">\n<link rel=\"stylesheet\" href=\"style.css\">\n</head>\n"
            "<body><div id=\"shop\"><p class=\"badge\">New</p></div></body>\n</html>\n", encoding="utf-8")
        (rw / "style.css").write_text(
            ":root { --brand-color: #2ea84a; }\nh1 { color: var(--brand-colour); }\n#shop .badge {\n    z-index: 5;\n}\n", encoding="utf-8")
        got = scan(rw)
        check("a misspelled variable of the project's own is still found", bool(kinds("css-undefined-var")), str(sorted({e.get('kind') for e in got})))
        check("z-index without position on the project's own markup is still found", bool(kinds("css-zindex-no-position")),
              str(sorted({e.get('kind') for e in got})))

        print("Case: ASML dagster-delta (what was wrongly reported)")
        dd = tmp / "dagster-delta"
        dd.mkdir()
        (dd / "compat.py").write_text(
            "import sys\nimport click\nimport click.core\n"
            "try:\n    from dagster._core.definitions.partitions.utils import TimeWindow\n"
            "except ModuleNotFoundError:\n    from dagster._core.definitions.time_window_partitions import TimeWindow\n"
            "if sys.version_info >= (3, 11):\n    from typing import NotRequired\n"
            "else:\n    from typing_extensions import NotRequired\n\n\n"
            "def find_keys(meta, keys=[\"merge_predicate\", \"merge_operations_config\"]):\n"
            "    result = {}\n    for key in keys:\n        if key in meta:\n            result[key] = meta[key]\n"
            "    return result, len(keys)\n\n\n"
            "print(TimeWindow, NotRequired, click, find_keys({}))\n", encoding="utf-8")
        got = scan(dd)
        check("an import fallback (try/except, if/else) or `import x` + `import x.y` is not an overwritten import", not kinds("import-overwritten"),
              str(kinds("import-overwritten")))
        check("a list default that is only read is not a shared mutable default", not kinds("mutable-default"),
              str(kinds("mutable-default")))

        print("Case: the same shapes, but real problems")
        rp = tmp / "real-py"
        rp.mkdir()
        (rp / "bad.py").write_text(
            "from os.path import join\nfrom posixpath import join\n\n\n"
            "def add(x, bucket=[]):\n    bucket.append(x)\n    return bucket\n\n\n"
            "def keep(x, seen={}):\n    seen[x] = 1\n    return len(seen)\n\n\n"
            "def give(acc=[]):\n    return acc\n\n\n"
            "print(join, add(1), keep(2), give())\n", encoding="utf-8")
        got = scan(rp)
        check("two plain imports of the same name are still found", bool(kinds("import-overwritten")),
              str(sorted({e.get('kind') for e in got})))
        check("list/dict defaults that are changed or returned are still found (3)", len(kinds("mutable-default")) == 3,
              str(kinds("mutable-default")))

        print("Case: ASML dagster-uc (what was wrongly reported)")
        du = tmp / "dagster-uc"
        du.mkdir()
        (du / "README.md").write_text(
            "# Deploy tool\n\nSet the kube context first. The webserver reads the image from acr, built with buildah.\n\n"
            "Extra kwargs are passed on. It used to have a special meaning, but noone uses it. This depoyment is new.\n",
            encoding="utf-8")
        (du / "handler.py").write_text(
            "import json\nfrom collections import OrderedDict as OrderedDict\n\n\n"
            "def run():\n    return 1\n", encoding="utf-8")
        got = scan(du)
        bad = [k for k in kinds("spelling") if any(w in k[3] for w in ("kube", "kwargs", "buildah", "acr ", "webserver"))]
        check("kube / kwargs / buildah / acr / webserver are not typos", not bad, str(bad))
        check("'noone' is fixed as 'no one', not 'none'", any("no one" in k[3] for k in kinds("spelling")), str(kinds("spelling")))
        check("a real typo next to them is still found", any("depoyment" in k[3] for k in kinds("spelling")), str(kinds("spelling")))
        un = [k[3] for k in kinds("unused-import")]
        check("`import X as X` is not an unused import", not any("OrderedDict" in u for u in un), str(un))
        check("a plain unused import is still found", any("json" in u for u in un), str(un))

        print("Case: ASML Julia / Bazel repos (what was wrongly reported)")
        jl = tmp / "julia-site"
        (jl / "page" / "_layout").mkdir(parents=True)
        (jl / "page" / "_libs" / "highlight").mkdir(parents=True)
        (jl / "tools").mkdir()
        (jl / "page" / "_libs" / "highlight" / "highlight.pack.js").write_text(
            "var k='ifdef ifndef strcpy strncpy memcpy Cuint putchar';\n" + "x();\n" * 3, encoding="utf-8")
        (jl / "page" / "_libs" / "scrollspy.min.js").write_text("var a=1;\n", encoding="utf-8")
        (jl / "page" / "_layout" / "foot.html").write_text(
            "  {{ if hascode }}\n  {{ insert foot_highlight.html }}\n  {{ end }}\n"
            "  <script src=\"/libs/scrollspy.min.js\"></script>\n  </body>\n</html>\n", encoding="utf-8")
        (jl / "tools" / "nouns.txt").write_text("\n".join(
            ["aardvark", "brocolli", "bird", "bird-house", "chainstay", "fedelini"] + ["word%d" % i for i in range(60)]) + "\n",
            encoding="utf-8")
        (jl / "README.md").write_text(
            "# Xml tools\n\nEach dict maps a name to a value, and this reads an XML file into a struct for you.\n\n"
            "The dikc and cuint values are passed on as they are, without any change at all.\n\n"
            "~~~<h4>Use a <code>::Ref{T}</code> field</h4>~~~\n\nThis does not work for every kind of field we have here.\n\n"
            "## First example\n\n1. We load the struct definitions generated by `XsdToStruct`.\n"
            "2. We write the struct instance to a file.\n\n"
            "## Second example\n\n1. We load the struct definitions generated by `XsdToStruct.jl`.\n"
            "2. We write the struct instance to a file.\n\n"
            "<pre>\nload(\"@rules_variant//variant:defs.bzl\", \"flags\")\n</pre>\n\n"
            "<pre>\nload(\"@rules_variant//variant:defs.bzl\", \"flags\")\n</pre>\n", encoding="utf-8")
        got = scan(jl)
        rude = [k for k in kinds("spelling") if re.search(r"(?i)\b(dick|cunt)\b", k[3])]
        check("the misspelled words next to them are still reported (with a clean fix)",
              sum(1 for k in kinds("spelling") if re.search(r"dikc|cuint", k[3])) == 2, str(kinds("spelling")))
        check("a rude word is never offered as a spelling fix", not rude, str(rude))
        lib = [k for k in got if "_libs/" in (k.get("file") or "") and k.get("kind") != "unused-file"]
        check("a bundled library (_libs/, .pack.js) is not checked as the project's own text", not lib,
              str([(k.get("kind"), k.get("file")) for k in lib][:5]))
        wl = [k for k in got if (k.get("file") or "").endswith("nouns.txt")]
        check("a word list (one word per line) is not checked as sentences", not wl, str([(k.get("kind"), k.get("line")) for k in wl][:5]))
        check("'an XML' is not called wrong", not kinds("article-an-a"), str(kinds("article-an-a")))
        lay = [k for k in got if "_layout/" in (k.get("file") or "") and k.get("kind") in ("missing-file", "page-missing-file", "page-error", "syntax")]
        check("a Franklin.jl _layout piece is not called broken", not lay, str(lay))
        check("~~~<h4>..</h4>~~~ (Franklin raw HTML) is not an unclosed code block", not kinds("md-unclosed-fence", "syntax"),
              str(kinds("md-unclosed-fence", "syntax")))
        check("repeated numbered steps, <pre> code and sentences with different `code` are not repeated sentences",
              not kinds("repeated-sentence"), str(kinds("repeated-sentence")))

        print("Case: the same shapes, but real problems")
        jr = tmp / "julia-real"
        jr.mkdir()
        (jr / "README.md").write_text(
            "# Xml tools\n\nWe construct an new instance of the struct with the data copied from the file.\n\n"
            "The loader reads the whole file into memory before it starts.\n\nIt works the same way across different enviroments today.\n\n"
            "The loader reads the whole file into memory before it starts.\n", encoding="utf-8")
        got = scan(jr)
        check("'an new' is still found", bool(kinds("article-an-a")), str(sorted({e.get('kind') for e in got})))
        check("a real typo (enviroments) is still found", any("enviroments" in k[3] for k in kinds("spelling")), str(kinds("spelling")))
        check("a truly repeated sentence is still found", bool(kinds("repeated-sentence")), str(sorted({e.get('kind') for e in got})))

        print("Case: Quarkus full scan (what was wrongly reported)")
        qf = tmp / "quarkus-full"
        (qf / "dev-ui" / "i18n").mkdir(parents=True)
        (qf / "dev-ui" / "i18n" / "de.js").write_text(
            "export const templates = {\n  'quarkus-agroal-chart': 'Diagramm anzeigen und lesen',\n"
            "  'quarkus-agroal-start': 'Beginn der Abfrage, bitte filtern',\n};\n", encoding="utf-8")
        (qf / "dev-ui" / "i18n" / "pt-BR.js").write_text(
            "export const templates = {\n  'quarkus-hello': 'Olá mundo, o progresso dos passos',\n};\n", encoding="utf-8")
        (qf / "dev-ui" / "i18n" / "en.js").write_text(
            "export const templates = {\n  'quarkus-hello': 'Shows the curent datasource when you asume it is ready',\n};\n",
            encoding="utf-8")
        (qf / "dev-ui" / "labels.js").write_text(
            "export const label = 'Priorität der Ausführung für alle Aufträge';\n", encoding="utf-8")
        (qf / "CONTRIBUTING.md").write_text(
            "# Contributing\n\nBuild it quickly with `./mvnw` and the [-Dquickly](#-dquickly) option when you only want the jars.\n\n"
            "Then log in in the browser window it will open, if you are not already logged in.\n\n"
            "Quartz cron uses six fields (seconds minutes hours day-of-month month day-of-week) by default here.\n\n"
            "Run the the tests before you send the pull request to the team.\n", encoding="utf-8")
        got = scan(qf)
        tr = [k for k in kinds("spelling") if "/i18n/de.js" in (k[1] or "") or "pt-BR" in (k[1] or "")]
        check("translation files (i18n/de.js, pt-BR.js) are not spell-checked as English", not tr, str(tr))
        acc = [k for k in kinds("spelling") if re.search(r"Priorit|Ausf|Auftr", k[3])]
        check("pieces of words with accents (Priorität) are not typos", not acc, str(acc))
        check("the Maven option -Dquickly is not a typo", not [k for k in kinds("spelling") if "Dquickly" in k[3]], str(kinds("spelling")))
        rw = [k[3] for k in kinds("repeated-word")]
        check("'log in in the browser' and 'day-of-month month' are not repeated words",
              not any(("in in" in r) or ("month month" in r) for r in rw), str(rw))
        check("a real 'the the' is still found", any("the the" in r.lower() for r in rw), str(rw))
        en = [k[3] for k in kinds("spelling") if "en.js" in (k[1] or "")]
        check("typos in the English translation file are still found (2)", sum(1 for w in en if re.search(r"curent|asume", w)) == 2, str(en))

        print("Case: Angular full scan (what was wrongly reported)")
        ng = tmp / "angular"
        (ng / "content" / "guide" / "di").mkdir(parents=True)
        (ng / "content" / "guide" / "di" / "overview.md").write_text("# DI\n\nDependency injection explained in a short page.\n", encoding="utf-8")
        (ng / "content" / "guide" / "images.md").write_text(
            "# Images\n\nRead the [DI guide](guide/di/overview) and the [inject API](/api/core/inject) before you start here.\n\n"
            "Generated with a [loader](#optional-set-up-a-loader) when one is set.\n\n"
            "<docs-step title=\"(Optional) Set up a Loader\">\nPick a loader for the images.\n</docs-step>\n\n"
            "<docs-code header=\"npm\" language=\"shell\">\n  npm install tailwindcss @tailwindcss/postcss postcss\n</docs-code>\n\n"
            "@see See what polyfills you might need in the browser support guide.\n\n"
            "<a href=\"https://developer.mozilla.org/\">\n  Standard JavaScript class constructor\n</a>. Runs when the component is made.\n",
            encoding="utf-8")
        (ng / "app").mkdir()
        (ng / "app" / "menu.html").write_text(
            "<div class=\"menu\">\n  <div class=\"item\">\n    <span class=\"icon material-symbols-outlined\" aria-hidden=\"true\">edit</span>\n"
            "    <span class=\"label\">Edit</span>\n  </div>\n</div>\n", encoding="utf-8")
        (ng / "app" / "compile.ts").write_text(
            "import * as ts from 'typescript';\nexport function build(files: string[]) {\n"
            "  const program = ts.createProgram(files, {});\n  return program.emit();\n}\n", encoding="utf-8")
        got = scan(ng)
        check("docs-site links from the content root (guide/di/overview, /api/core/inject) are not broken links",
              not kinds("md-broken-link"), str(kinds("md-broken-link")))
        check("an anchor made by <docs-step title=...> is not a missing heading", not kinds("md-broken-anchor"), str(kinds("md-broken-anchor")))
        check("an icon word next to its label, <docs-code> commands and '@see See' are not repeated words",
              not kinds("repeated-word"), str(kinds("repeated-word")))
        check("'</a>.' on the next line is not a space before a full stop", not kinds("space-before-punct"), str(kinds("space-before-punct")))
        check("TypeScript's ts.createProgram() is not a WebGL leak",
              not [e for e in got if e.get("kind") == "leak" and "WebGL" in json.dumps(e)], "")

        print("Case: the same shapes, but real problems")
        nr = tmp / "angular-real"
        (nr / "content" / "guide").mkdir(parents=True)
        (nr / "content" / "guide" / "start.md").write_text(
            "# Start\n\nSee the [setup notes](./setup.md) and the [missing section](#install-steps) for more.\n\n"
            "The benchmark will run run the garbage collector before each sample.\n", encoding="utf-8")
        (nr / "gl.js").write_text(
            "const gl = document.querySelector('canvas').getContext('webgl');\n"
            "export function draw() {\n  const buf = gl.createBuffer();\n  gl.bindBuffer(gl.ARRAY_BUFFER, buf);\n}\n", encoding="utf-8")
        got = scan(nr)
        check("a link to a missing .md file is still found", bool(kinds("md-broken-link")), str(sorted({e.get('kind') for e in got})))
        check("a link to a missing heading is still found", bool(kinds("md-broken-anchor")), str(sorted({e.get('kind') for e in got})))
        check("a real 'run run' is still found", any("run run" in k[3] for k in kinds("repeated-word")), str(kinds("repeated-word")))
        check("a real WebGL buffer that is never deleted is still found",
              bool([e for e in got if e.get("kind") == "leak" and "WebGL" in json.dumps(e)]), str(sorted({e.get('kind') for e in got})))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\nREGRESSION TEST: %d passed, %d failed" % (len(OK), len(BAD)))
    return 1 if BAD else 0


if __name__ == "__main__":
    sys.exit(main())
