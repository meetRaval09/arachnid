#!/usr/bin/env python3
"""
ARACHNID - BRUTAL CSS TEST
==========================
    python qa_css_brutal.py

A small studio website (1 page, 4 style sheets) with 30 CSS bugs hidden in it: a misspelled variable, a media query
without a unit, calc() without spaces, a // comment, a missing semicolon, a late @import, text-align: centre,
a sticky header trapped by overflow: hidden, white text on white, a page wider than a phone ... and its CLEAN TWIN:
the same site with every bug fixed plus correct CSS that LOOKS like the bugs.

Passes only if a full scan finds EVERY hidden bug on its exact line, and finds NOTHING AT ALL on the clean twin.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
LEDGER = HERE / "reports" / "ledger.jsonl"

SHOP = {
 "index.html": "<!doctype html>\n<html lang=\"en\">\n<head>\n  <meta charset=\"utf-8\">\n  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n  <title>Studio</title>\n  <link rel=\"stylesheet\" href=\"css/style.css\">\n  <link rel=\"stylesheet\" href=\"css/Theme.css\">\n  <link rel=\"stylesheet\" href=\"css/print.css\" media=\"pritn\">\n  <link rel=\"stylesheet\" href=\"css/fonts.css\">\n  <style>\n    .wrap { overflow: hidden; }\n    .topbar { position: sticky; top: 0; background: #111; color: #fff; padding: 8px; }\n    .sidebar { position: sticky; background: #f5f5f5; }\n    .banner { width: 640px; height: 60px; background: #2ea84a; }\n    .ghost { color: #ffffff; }\n    .faint { color: #c8c8c8; }\n    .promo { colr: red; }\n  </style>\n</head>\n<body>\n  <div class=\"wrap\">\n    <div class=\"topbar\">Studio menu</div>\n    <p>Welcome to the studio.</p>\n  </div>\n  <aside class=\"sidebar\">Quick links</aside>\n  <div class=\"banner\">New collection</div>\n  <p class=\"ghost\">Free delivery this week</p>\n  <p class=\"faint\">Prices include GST</p>\n  <p class=\"promo\">Sale</p>\n  <div class=\"pop\">Cookie notice</div>\n</body>\n</html>\n",
 "img/hero.jpg": "",
 "css/print.css": "@media print { .menu { display: none; } }\n",
 "css/style.css": ":root { --brand-color: #2ea84a; --space: 16px; }\nbody { margin: 0; font-family: system-ui, sans-serif; color: #222; background: #fff; }\n.logo { color: var(--brand-colour); }\n.cards { display: grid; gap: var(--space); }\n@media (max-width: 768) { .cards { grid-template-columns: 1fr; } }\n.main { width: calc(100%-2rem); }\n// footer styles\n.footer { padding: 24px; background: #111; color: #eee; }\n.price { color: #c00\n         font-weight: bold; }\n@import url(\"extra.css\");\n.row { display: flexx; }\n.badge { position: absolut; top: 0; }\n.tag { margin: 10pxx; }\n.chip { padding: 10 px; }\n.box { widht: 200px; }\n.alert { color: #ggg; }\n.lead { font-size: 18; }\n.btn { transition: background 0.3; }\n.menu { justify-content: space-betwen; display: flex; }\n.title { text-align: centre; }\n.side { width: calc(100% -20px); }\n.pop { z-index: 10; background: #fff; }\n.note { margin: 8px; color: #333; margin: 12px; }\ntable > tr { background: #f5f5f5; }\n",
 "css/theme.css": ".theme-dark { background: #000; color: #fff; }\n.theme-card { border: 1px solid #ccc;\n.theme-footer { padding: 8px; }\n",
 "css/extra.css": ".extra { color: #111; }\n"
}

CLEAN = {
 "index.html": "<!doctype html>\n<html lang=\"en\">\n<head>\n  <meta charset=\"utf-8\">\n  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n  <title>Studio</title>\n  <link rel=\"stylesheet\" href=\"css/style.css\">\n  <link rel=\"stylesheet\" href=\"css/theme.css\">\n  <link rel=\"stylesheet\" href=\"css/print.css\" media=\"print\">\n  <style>\n    .topbar { position: sticky; top: 0; background: #111; color: #fff; padding: 8px; }\n    .scroller { height: 100px; overflow: auto; }\n    .scroller h3 { position: sticky; top: 0; background: #fff; margin: 0; }\n    .banner { width: 640px; max-width: 100%; height: 60px; background: #2ea84a; color: #fff; }\n    .clip { overflow: hidden; } .clip div { width: 900px; }\n    .dark { background: #111; color: #eee; padding: 8px; }\n    .grad { background: linear-gradient(90deg, #c00, #00c); -webkit-background-clip: text; background-clip: text; color: transparent; }\n    .sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); color: #fff; }\n    .promo { color: #a00; }\n  </style>\n</head>\n<body>\n  <div class=\"topbar\">Studio menu</div>\n  <div class=\"scroller\"><h3>Sticky inside a scroller</h3><p>a</p><p>b</p><p>c</p><p>d</p><p>e</p></div>\n  <div class=\"banner\">New collection</div>\n  <div class=\"clip\"><div>wide but clipped</div></div>\n  <ul class=\"menu\"><li>One</li><li>Two</li></ul>\n  <div class=\"row\"><span class=\"badge\">New</span><span>Shoes</span></div>\n  <p class=\"dark\">Light text on dark</p>\n  <p class=\"grad\">Gradient heading</p>\n  <span class=\"sr\">Skip to content</span>\n  <p class=\"promo\">Sale</p>\n  <p class=\"price\">Rs 499</p>\n</body>\n</html>\n",
 "css/print.css": "@media print { .menu { display: none; } }\n",
 "css/style.css": "@charset \"utf-8\";\n@import url(\"extra.css\");\n@layer base, components;\n:root { --brand-color: #2ea84a; --space: 16px; --space-2: 8px; }\n@property --angle { syntax: \"<angle>\"; inherits: false; initial-value: 0deg; }\nbody { margin: 0; font-family: system-ui, \"Segoe UI 2\", sans-serif; color: #222; background: #fff; }\n.logo { color: var(--brand-color); border-color: var(--not-set-yet, currentColor); transform: rotate(var(--angle)); }\n.cards { display: grid; gap: var(--space); grid-template-columns: repeat(auto-fit, minmax(min(100%, 240px), 1fr)); }\n@media (max-width: 768px) and (min-width: 0) { .cards { grid-template-columns: 1fr; } }\n@media (min-resolution: 2dppx), (orientation: landscape), print { .cards { gap: 8px; } }\n.main { width: calc(100% - 2rem); margin-inline: calc(var(--space-2) * -1); height: calc(-1 * var(--space)); top: calc(1e-3px + 2px); }\n/* footer styles // not a problem inside a comment */\n.footer { padding: 24px; background: #111 url(https://example.com/a.png?x=1//y) no-repeat; color: #eee; }\n.price { color: #c00; font-weight: bold; }\n.row { position: relative; display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; }\n.badge { position: absolute; top: 0; z-index: 2; }\n.tag { margin: 10px 1.5rem 0 auto; line-height: 1.5; font: 600 12px/1.4 system-ui, sans-serif; }\n.chip { padding: 10px; aspect-ratio: 16 / 9; width: 50cqw; }\n.box { width: 200px; max-width: 100%; }\n.alert { color: #f80; background: #fff8ee; }\n.lead { font-size: 18px; font-style: oblique 10deg; }\n.btn { transition: background 0.3s ease-in-out, color 200ms cubic-bezier(0.1, 0.7, 1, 0.1); }\n.menu { justify-content: space-between; display: flex; }\n.menu li { z-index: 1; }\n.title { text-align: center; text-transform: uppercase; }\n.side { width: calc(100% - 20px); }\n.note { margin: 8px; color: #333; }\ntable > tbody > tr { background: #f5f5f5; }\n.quote::before { content: \"10 px: a // b\"; }\n.cols { display: grid-lanes; }\n",
 "css/theme.css": ".theme-dark { background: #000; color: #fff; }\n.theme-card { border: 1px solid #ccc; }\n.theme-footer { padding: 8px; }\n",
 "css/extra.css": ".extra { color: #111; }\n"
}

# (file, text on the line of the bug, what the bug is)
TRUTH = [
 [
  "css/style.css",
  "var(--brand-colour)",
  "variable misspelled (--brand-colour vs --brand-color)"
 ],
 [
  "css/style.css",
  "(max-width: 768)",
  "media query without a unit - never applies"
 ],
 [
  "css/style.css",
  "calc(100%-2rem)",
  "calc without spaces - line ignored"
 ],
 [
  "css/style.css",
  "// footer styles",
  "// comment kills the footer rule"
 ],
 [
  "css/style.css",
  "color: #c00",
  "missing ; - both lines die"
 ],
 [
  "css/style.css",
  "@import",
  "@import after rules - ignored"
 ],
 [
  "css/style.css",
  "flexx",
  "display: flexx"
 ],
 [
  "css/style.css",
  "absolut",
  "position: absolut"
 ],
 [
  "css/style.css",
  "10pxx",
  "unit that does not exist"
 ],
 [
  "css/style.css",
  "10 px",
  "space between number and unit"
 ],
 [
  "css/style.css",
  "widht",
  "misspelled property"
 ],
 [
  "css/style.css",
  "#ggg",
  "impossible colour"
 ],
 [
  "css/style.css",
  "font-size: 18;",
  "size without a unit"
 ],
 [
  "css/style.css",
  "0.3;",
  "time without a unit"
 ],
 [
  "css/style.css",
  "space-betwen",
  "misspelled keyword"
 ],
 [
  "css/style.css",
  "centre",
  "British spelling of center"
 ],
 [
  "css/style.css",
  "calc(100% -20px)",
  "calc with a space only on one side"
 ],
 [
  "css/style.css",
  "z-index: 10",
  "z-index without position"
 ],
 [
  "css/style.css",
  "margin: 12px",
  "same property twice"
 ],
 [
  "css/style.css",
  "table > tr",
  "selector that never matches (tbody)"
 ],
 [
  "css/theme.css",
  ".theme-card",
  "{ never closed - swallows the rest"
 ],
 [
  "index.html",
  "css/Theme.css",
  "capital letters differ - 404 online"
 ],
 [
  "index.html",
  "media=\"pritn\"",
  "misspelled media type"
 ],
 [
  "index.html",
  "css/fonts.css",
  "stylesheet that does not exist"
 ],
 [
  "index.html",
  "class=\"topbar\"",
  "sticky trapped by overflow: hidden"
 ],
 [
  "index.html",
  "class=\"sidebar\"",
  "sticky without top"
 ],
 [
  "index.html",
  "class=\"banner\"",
  "wider than a phone screen"
 ],
 [
  "index.html",
  "class=\"ghost\"",
  "white text on white"
 ],
 [
  "index.html",
  "class=\"faint\"",
  "text too light to read"
 ],
 [
  "index.html",
  "colr",
  "misspelled property inside <style>"
 ]
]

def write(root, files):
    for rel, text in files.items():
        p = Path(root) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


def scan(root):
    subprocess.run([sys.executable, str(HERE / "arachnid.py"), "scan", str(root)], capture_output=True, text=True, errors="replace", timeout=900)
    ents = []
    for ln in (LEDGER.read_text("utf-8", "replace").splitlines() if LEDGER.exists() else []):
        try:
            ents.append(json.loads(ln))
        except ValueError:
            pass
    return ents


def main():
    tmp = Path(tempfile.mkdtemp(prefix="arachnid-brutal-css-"))
    keep = tmp / "reports-backup"
    if (HERE / "reports").exists():
        shutil.copytree(HERE / "reports", keep)
    try:
        write(tmp / "shop", SHOP)
        write(tmp / "clean", CLEAN)
        ents = scan(tmp / "shop")
        caught = 0
        print("HIDDEN BUGS (each must be found on its exact line)")
        for f, needle, what in TRUTH:
            line = next(i for i, l in enumerate(SHOP[f].splitlines(), 1) if needle in l)
            hits = [e for e in ents if e["file"].replace(chr(92), "/").endswith(f) and int(e.get("line") or 0) == line]
            caught += bool(hits)
            print("  %-6s %-14s %3d  %s" % ("FOUND" if hits else "MISSED", f, line, what))
        # shorter-code suggestions (slim) are not bug reports: they have their own brutal test (qa_slim_brutal.py)
        noise = [e for e in scan(tmp / "clean") if e.get("tool") != "slim"]
        print("\nCLEAN TWIN (must stay completely quiet)")
        for e in noise:
            print("  FALSE ALARM  [%s] %s:%s %s" % (e["tool"], e["file"], e.get("line"), e["what"][:100]))
        print("  %d finding(s)" % len(noise))
        print("\nBRUTAL SCORE: %d of %d hidden bugs found, %d false alarm(s) on the clean twin" % (caught, len(TRUTH), len(noise)))
        return 0 if caught == len(TRUTH) and not noise else 1
    finally:
        if keep.exists():
            shutil.rmtree(HERE / "reports", ignore_errors=True)
            shutil.copytree(keep, HERE / "reports", dirs_exist_ok=True)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
