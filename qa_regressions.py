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
"""
import base64
import json
import os
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
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\nREGRESSION TEST: %d passed, %d failed" % (len(OK), len(BAD)))
    return 1 if BAD else 0


if __name__ == "__main__":
    sys.exit(main())
