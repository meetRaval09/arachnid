#!/usr/bin/env python3
"""
ARACHNID - QA STRESS TEST: runs the whole debugger through many different projects and situations.
================================================================================================
    python qa_stress.py                 run every round (15 situations)
    python qa_stress.py --round 3 7     run only these rounds
    python qa_stress.py --list          list the rounds

For every project the same checks run:
  CRASH       no tool may ever print a Python error (Traceback) or hang
  READ-ONLY   `scan` must not change a single byte
  FIX         `fix` runs; its time is recorded
  STABLE      running `fix` again must settle (no endless changing)
  BEHAVIOUR   for web projects: the finished product is compared, page by page, with the original in a real browser
              (files loaded, HTML, element positions, browser errors, every pixel) - an independent second opinion of the gate
  UNDO        undoing everything must give back the original project, byte for byte
  SPEED       every command's time is measured and anything slow is reported
  EXTRAS      insights / advise / netcheck / size / leaks / proposals / triage run on the same project without crashing
Everything happens in temporary folders; the real reports/ and backups/ are restored afterwards.
"""
import argparse
import hashlib
import json
import os
import random
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = sys.executable
sys.path.insert(0, str(HERE))

SLOW = {"scan": 150, "fix": 400, "insights": 40, "advise": 40, "netcheck": 40, "size": 40, "leaks": 60, "proposals": 10, "triage": 10, "undo": 60}
RESULTS = []          # (round, level, text)


def note(rnd, level, text):
    RESULTS.append((rnd, level, text))
    print("   [%s] %s" % (level, text), flush=True)


def strip_c2pa(b):
    """This test machine's output folder stamps a provenance chunk (caBX) into PNG files it stores; that is not Arachnid's doing."""
    if not b.startswith(b"\x89PNG\r\n\x1a\n"):
        return b
    out, i = [b[:8]], 8
    while i + 8 <= len(b):
        n = struct.unpack(">I", b[i:i + 4])[0]
        if b[i + 4:i + 8] != b"caBX":
            out.append(b[i:i + 12 + n])
        i += 12 + n
    return b"".join(out)


def snap(base):
    out = {}
    for dp, dn, fn in os.walk(base, followlinks=False):
        for f in fn:
            p = Path(dp) / f
            try:
                if p.is_symlink():
                    out[p.relative_to(base).as_posix()] = b"<symlink>" + os.readlink(p).encode()
                else:
                    if "__pycache__" in p.parts or ".pytest_cache" in p.parts:
                        continue
                    out[p.relative_to(base).as_posix()] = strip_c2pa(p.read_bytes())
            except OSError:
                pass
        for d in dn:
            p = Path(dp) / d
            if p.is_symlink():
                out[p.relative_to(base).as_posix() + "/"] = b"<symlink-dir>" + os.readlink(p).encode()
    return out


def diff(a, b):
    return sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))


def run(rnd, label, args, timeout=420, cwd=None):
    t0 = time.time()
    try:
        r = subprocess.run([PY, str(HERE / "arachnid.py")] + args, capture_output=True, text=True, timeout=timeout, cwd=cwd, errors="replace")
        out, code = (r.stdout or "") + (r.stderr or ""), r.returncode
    except subprocess.TimeoutExpired:
        note(rnd, "BUG", "`%s` did not finish within %d s (hang)" % (label, timeout))
        return "", -9, timeout
    dt = time.time() - t0
    if "Traceback (most recent call last)" in out:
        tb = out[out.rindex("Traceback"):][:900]
        note(rnd, "BUG", "`%s` crashed:\n%s" % (label, tb))
    limit = SLOW.get(label.split()[0], 120)
    if dt > limit:
        note(rnd, "SLOW", "`%s` took %.0f s (budget %d s)" % (label, dt, limit))
    return out, code, dt


# ---------------------------------------------------------------------------------------------------------------
def png(path, rgb, w=40, h=20):
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))

    def ch(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + ch(b"IDAT", zlib.compress(raw)) + ch(b"IEND", b""))


def w(p, text, mode="w", enc="utf-8", newline=None):
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, mode, encoding=enc, newline=newline) as fh:
        fh.write(text)


PAGE = ('<html><head><meta charset="utf-8"><style>img{width:40px}body{font-family:sans-serif}</style></head><body><h1>%s</h1>%s'
        '<script src="%s"></script></body></html>')


def r1(d):       # static website: dup images, broken js, typos
    png(d / "pages/home/logo.png", (200, 30, 30)); png(d / "assets/logo-copy.png", (200, 30, 30))
    w(d / "pages/home/home.html", PAGE % ("Home", '<img src="logo.png"><p>Welcome to our webste</p>', "app.js"))
    w(d / "pages/about/about.html", PAGE % ("About", '<img src="../../assets/logo-copy.png">', "../home/app.js"))
    w(d / "pages/home/app.js", "// we recieve data\nconst a = [1, 2;\ndocument.querySelector('h1').title = 'n' + a.length;\n")
    w(d / "README.md", "# Site\n\nTheir is a note. Built with javascript and github.\n")
    w(d / "package.json", "{}")
    return dict(web=True)


def r2(d):       # python library with tests
    w(d / "lib.py", "# we recieve the items here\nimport os\nimport sys\nfrom typing import List\n\nNAMES = ['a', 'b', 'c', 'd', 'e']\n\n\ndef total(items):\n    leftover = 5\n    result = 0\n    for i in items:\n        result += i\n    return result\n\n\n"
                    "def ok(x):\n    return x in NAMES\n\n\ndef shared(x=[]):\n    return x\n\n\ndef tot2(r):\n    return sum([i for i in r])\n\n\ndef low(xs: List[int]):\n    return sorted(xs)[0]\n\n\nprint(sys.argv)\n")
    w(d / "tests/test_lib.py", "import sys, os\nsys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))\nimport lib\n\n\ndef test_all():\n    assert lib.total([1, 2]) == 3 and lib.ok('a') and not lib.ok('z') and lib.tot2([1, 2]) == 3 and lib.low([3, 1]) == 1\n")
    w(d / "README.md", "# Lib\n\nThe libary does teh work. Their is a test.\n")
    return dict(web=False, expect_py="tests")


def r3(d):       # node-ish front end with CDN tags, heavy libs, leaks
    w(d / "package.json", json.dumps({"name": "x", "dependencies": {"jquery": "^3", "moment": "^2", "lodash": "*", "react": "^18", "react-dom": "^18"}, "scripts": {}}))
    w(d / "index.html", '<html><head><script src="http://code.jquery.com/jquery-3.6.0.min.js"></script><script src="https://cdn.jsdelivr.net/npm/lodash/lodash.min.js"></script></head><body><button id="b">go</button><script src="src/main.js"></script></body></html>')
    w(d / "src/main.js", "import _ from 'lodash';\nimport moment from 'moment';\nconst items = [1,2,3];\nwindow.addEventListener('resize', () => {});\nsetInterval(() => { fetch('/x') }, 500);\nfor (const a of items) { if (items.indexOf(a) > -1) {} }\nfunction unusedFn(){}\nconsole.log(_.map(items, x => x));\n")
    w(d / "src/main.js.bak", "junk")
    return dict(web=True)


def r4(d):       # many languages' manifests
    w(d / "pom.xml", "<project><dependencies><dependency><groupId>joda-time</groupId><artifactId>joda-time</artifactId><version>2.10</version></dependency></dependencies></project>")
    w(d / "src/App.java", "import org.joda.time.DateTime;\nclass App { public static void main(String[] a) { System.out.println(\"hi\"); } }\n")
    w(d / "go.mod", "module x\nrequire github.com/pkg/errors v0.9.1\n"); w(d / "main.go", "package main\nfunc main() {}\n")
    w(d / "Cargo.toml", "[dependencies]\ntokio = { version = \"1\", features = [\"full\"] }\nlazy_static = \"1.4\"\n"); w(d / "src/main.rs", "fn main() {}\n")
    w(d / "composer.json", '{"require":{"swiftmailer/swiftmailer":"*"}}'); w(d / "index.php", "<?php echo 'hi';\n")
    w(d / "Gemfile", "source 'http://rubygems.org'\ngem 'rails'\n"); w(d / "app.rb", "puts 'hi'\n")
    w(d / "App.csproj", '<Project><ItemGroup><PackageReference Include="Newtonsoft.Json" Version="13.*" /></ItemGroup></Project>'); w(d / "Program.cs", "class P { static void Main() {} }\n")
    w(d / "pubspec.yaml", "dependencies:\n  dio: any\n"); w(d / "lib/main.dart", "void main() {}\n")
    return dict(web=False)


def r5(d):       # empty folder
    return dict(web=False)


def r6(d):       # binary only
    rng = random.Random(6)
    for i in range(6):
        (d / ("b%d.png" % i)).write_bytes(bytes(rng.getrandbits(8) for _ in range(3000)))
    (d / "x.bin").write_bytes(b"\x00\xff" * 500); (d / "empty.js").write_bytes(b""); (d / "empty.html").write_bytes(b"")
    return dict(web=False)


def r7(d):       # unicode & spaces in names and content
    w(d / "my folder/ünïcode файл.md", "# Привет\n\nHello wörld — we recieve naïve café ☕ text. 日本語のテキスト. javascript\n")
    w(d / "my folder/страница.html", PAGE % ("Héllo", "<p>Ünï test</p>", "скрипт.js")); w(d / "my folder/скрипт.js", "// recieve ✓\nconsole.log('日本語');\n")
    w(d / "emoji 😀/notes.txt", "A note about the databse 😀 and the Github page.\n")
    return dict(web=True)


def r8(d):       # CRLF, BOM, latin-1
    w(d / "a.md", "# Title\r\n\r\nTheir is a note about javascript.\r\nSecond line recieve.\r\n", newline="")
    w(d / "b.py", "# recieve me\r\nimport os\r\nprint('x')\r\n", newline="")
    (d / "c.md").write_bytes(b"\xef\xbb\xbf# BOM file\n\nWe recieve things from github.\n")
    (d / "d.txt").write_bytes("Caf\xe9 au lait is naive. We recieve it.\n".encode("latin-1"))
    (d / "e.js").write_bytes(b"\xef\xbb\xbf// recieve\nconsole.log(1);\r\nconsole.log(2);\n")
    return dict(web=False, crlf=["a.md", "b.py"])


def r9(d):       # scale
    rng = random.Random(9)
    for i in range(1500):
        w(d / ("pkg%d/mod%d.py" % (i % 50, i)), "# note %d\nx = %d\n\n\ndef f():\n    return x\n" % (i, i))
    w(d / "big.js", "\n".join("var v%d = %d; // comment %d" % (i, i, i) for i in range(60000)))
    w(d / "big.md", ("Some text about the project and its parts.\n\n" * 90000))
    return dict(web=False)


def r10(d):      # deep nesting, nested clones, symlink loop, read-only
    p = d
    for i in range(25):
        p = p / ("lvl%d" % i)
    w(p / "deep.txt", "deep file\n")
    w(d / "extra/extra/n.txt", "same\n"); w(d / "extra/n.txt", "same\n")
    png(d / "a/x.png", (1, 2, 3)); png(d / "b/x.png", (1, 2, 3))
    try:
        os.symlink(str(d), str(d / "loop"), target_is_directory=True)
        os.symlink("nowhere-at-all", str(d / "dangling.txt"))
    except OSError:
        pass
    w(d / "ro.md", "Their is a read only note.\n"); os.chmod(d / "ro.md", 0o444)
    return dict(web=False)


def r11(d):      # markdown with protected spans
    w(d / "doc.md", "---\ntitle: javascript guide\n---\n# Guide\n\nUse `javascript` and see https://github.com/x/javascript-demo or [javascript](http://x.io/javascript).\n\n```js\nconst github = 'javascript';  // recieve\n```\n\n    indented javascript code block\n\nWe love the colour and the favourite colour. The website, an e-mail, and the Github page.\n\n| a | b |\n|---|---|\n| javascript | `e-mail` |\n")
    w(d / "page.html", PAGE % ("Doc", "<pre>javascript recieve</pre><code>github</code><p>the e-mail</p>", "x.js")); w(d / "x.js", "var s = 'javascript';\n")
    return dict(web=True, md=["doc.md"])


def r12(d):      # valid modern code must never be touched; broken code repaired
    w(d / "ok.js", "const a = obj?.x ?? 1;\nexport const f = async (x) => { const { y, ...rest } = x; return [...rest, y]; };\nclass A { #p = 1; static s = 2; get v() { return this.#p; } }\nconsole.log(a, f, A);\n")
    w(d / "ok.tsx", "import React from 'react';\ninterface P<T> { items: T[] }\nexport function List<T>({ items }: P<T>) { return <ul>{items.map((i, k) => <li key={k}>{String(i)}</li>)}</ul>; }\n")
    w(d / "ok.py", "from __future__ import annotations\nimport asyncio\n\n\nasync def main() -> None:\n    match 1:\n        case 1:\n            print(f'{asyncio!r}')\n\n\nasyncio.run(main())\n")
    w(d / "bad1.js", "function f( { return 1; }\n"); w(d / "bad2.js", "const a = {x: 1,, y: 2};\n"); w(d / "bad3.py", "def f(:\n    pass\n")
    return dict(web=False, valid=["ok.js", "ok.tsx", "ok.py"])


def r13(d):      # already clean
    w(d / "index.html", PAGE % ("Clean", "<p>Everything is fine here.</p>", "a.js")); w(d / "a.js", "console.log('clean');\n")
    w(d / "README.md", "# Clean\n\nNothing to fix in this project.\n")
    return dict(web=True, expect_unchanged=True)


def r14(d):      # odd command-line input
    w(d / "x.txt", "hello\n")
    return dict(web=False, cli=True)


def r15(d):      # two scans at once on the same project
    w(d / "index.html", PAGE % ("Two", "<p>text</p>", "a.js")); w(d / "a.js", "var x = 1;\n"); w(d / "m.py", "import os\nprint(1)\n")
    return dict(web=True, concurrent=True)


ROUNDS = [(1, "static website: duplicates, broken script, typos", r1), (2, "python library with tests", r2), (3, "front-end project: CDN tags, heavy libraries, leaks", r3),
          (4, "seven languages' dependency files", r4), (5, "empty folder", r5), (6, "binary files only", r6), (7, "unicode and spaces in names and text", r7),
          (8, "CRLF, BOM and latin-1 files", r8), (9, "scale: 1500 files + 3 MB text files", r9), (10, "deep folders, nested clones, symlink loop, read-only file", r10),
          (11, "markdown with code fences, links, tables, front matter", r11), (12, "valid modern code must be left alone", r12), (13, "already clean project", r13),
          (14, "odd command-line input", r14), (15, "two scans at the same time", r15)]


# ---------------------------------------------------------------------------------------------------------------
def product_same(a_root, b_root):
    """Independent second opinion: open every page of both versions in a real browser and compare."""
    import pixelcheck
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None, ["playwright not installed"]
    pa, pb = pixelcheck.pages_in(a_root), pixelcheck.pages_in(b_root)
    if pa != pb:
        return False, ["pages differ: %s" % (sorted(set(pa) ^ set(pb))[:3],)]
    if not pa:
        return None, ["no pages"]
    # A page may point at a script/style whose bytes changed only in a comment.  The page's fingerprint includes a hash of that
    # file, so it would look "different" even though the program is provably the same.  Those files are checked with the
    # same proof the gate uses; if every changed code file is proven identical, the hashes are ignored.
    import gate
    code_ext = (".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".css")
    items = []
    for p in Path(a_root).rglob("*"):
        if p.suffix.lower() in code_ext and "node_modules" not in p.parts and p.is_file():
            rel = p.relative_to(a_root)
            q = Path(b_root) / rel
            if q.is_file() and p.read_bytes() != q.read_bytes():
                items.append((str(rel), p.read_text("utf-8", "replace"), q.read_text("utf-8", "replace")))
    mask = False
    if items:
        proven = gate.proven_same_program(str(b_root), items)
        broken_before = set()
        try:      # a file that could not even be read before is a REPAIR, not a refactor - the browser check below judges it
            # Asked through syntaxcheck's own reader, the same one that found the error: esbuild when the project has it,
            # otherwise Node (GitHub's machines have Node but no esbuild).
            from syntaxcheck import Oracle
            oracle = Oracle(str(b_root))
            for r, o, _ in items:
                ext = r.rsplit(".", 1)[-1].lower()
                if r not in proven and oracle.can(ext) and oracle.first_errors([o], ext)[0] is not None:
                    broken_before.add(r)
        except Exception:
            pass
        unproven = [r for r, _, _ in items if r not in proven and r not in broken_before]
        if unproven:
            return False, ["code changed in a way that is not provably the same program: %s" % unproven[:3]]
        mask = not broken_before
    norm = (lambda v: re.sub(r"sha:[0-9a-f]+", "sha:*", str(v))) if mask else (lambda v: v)
    sa, porta = pixelcheck.serve(a_root)
    sb, portb = pixelcheck.serve(b_root)
    problems, skipped = [], []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            for page_path in pa:
                for vw in pixelcheck.VIEWPORTS:
                    x1 = pixelcheck.snapshot(browser, porta, page_path, vw)
                    x2 = pixelcheck.snapshot(browser, porta, page_path, vw)
                    y = pixelcheck.snapshot(browser, portb, page_path, vw)
                    tag = "%s[%s]" % (page_path, vw[0])
                    if x1["errors"] or any("FAILED" in n for n in x1["net"]):
                        skipped.append(tag + " was already broken before")
                        if y["errors"] or any("FAILED" in n for n in y["net"]):
                            problems.append(tag + ": still broken after the fixes")
                        continue
                    for sig in ("net", "html", "boxes", "errors"):
                        if norm(x1[sig]) == norm(x2[sig]) and norm(x1[sig]) != norm(y[sig]):
                            problems.append("%s: %s differs" % (tag, sig))
                            if os.environ.get("QA_DEBUG"):
                                print("DEBUG", sig, "\nA:", str(x1[sig])[:700], "\nB:", str(y[sig])[:700])
                    if pixelcheck.same_png(x1["png"], x2["png"]) and not pixelcheck.same_png(x1["png"], y["png"]):
                        problems.append(tag + ": pixels differ")
            browser.close()
    finally:
        sa.shutdown(); sb.shutdown()
    return (not problems), problems[:5] + skipped[:2]


def md_protected(text):
    spans = re.findall(r"```.*?```", text, re.S) + re.findall(r"`[^`\n]+`", text) + re.findall(r"https?://\S+", text) + re.findall(r"\]\([^)]*\)", text)
    return sorted(spans)


def pipeline(rnd, name, fn):
    print("\n=== ROUND %d: %s" % (rnd, name), flush=True)
    tmp = Path(tempfile.mkdtemp(prefix="arachnid-qa-%d-" % rnd))
    proj = tmp / "proj"
    proj.mkdir()
    try:
        meta = fn(proj)
        if rnd not in (5, 6, 14) and Path("/opt/npm-tools/node_modules/esbuild").exists():
            os.symlink("/opt/npm-tools/node_modules", str(proj / "node_modules"), target_is_directory=True)   # lets the JavaScript checks run for real
        orig = snap(proj)
        orig_copy = tmp / "orig"
        shutil.copytree(proj, orig_copy, symlinks=True)
        root = str(proj)
        t_round = time.time()
        if meta.get("cli"):
            for label, args in (("scan (missing folder)", ["scan", str(tmp / "does-not-exist")]), ("scan (a file, not a folder)", ["scan", str(proj / "x.txt")]),
                                ("scan (trailing slash)", ["scan", root + "/"]), ("fix (missing folder)", ["fix", str(tmp / "nope")]),
                                ("insights (missing)", ["insights", str(tmp / "nope")]), ("netcheck (file)", ["netcheck", str(proj / "x.txt")]),
                                ("size (missing)", ["size", str(tmp / "nope")]), ("leaks (missing)", ["leaks", str(tmp / "nope")]), ("advise (missing)", ["advise", str(tmp / "nope")])):
                out, code, dt = run(rnd, label, args, 120)
            for tool in ("terms.py", "textcheck.py", "logiccheck.py", "spellcheck.py", "syntaxcheck.py", "links.py", "dedupe.py", "clones.py", "bundlesize.py", "leakcheck.py", "netcheck.py", "advisor.py", "insights.py", "deps.py"):
                for target in (str(tmp / "missing"), str(proj / "x.txt")):
                    t0 = time.time()
                    r = subprocess.run([PY, str(HERE / tool), target], capture_output=True, text=True, timeout=120, errors="replace")
                    if "Traceback" in (r.stdout + r.stderr):
                        note(rnd, "BUG", "`%s %s` crashed instead of giving a friendly message:\n%s" % (tool, "missing-folder" if "missing" in target else "file-as-folder", (r.stdout + r.stderr)[-500:]))
            if diff(orig, snap(proj)):
                note(rnd, "BUG", "odd input changed files: %s" % diff(orig, snap(proj))[:3])
            return
        # 1 scan must be read-only
        out, code, dt = run(rnd, "scan", ["scan", root], 400)
        if diff(orig, snap(proj)):
            note(rnd, "BUG", "`scan` changed files: %s" % diff(orig, snap(proj))[:4])
        times = {"scan": dt}
        if meta.get("concurrent"):
            ps = [subprocess.Popen([PY, str(HERE / "arachnid.py"), "scan", root], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace") for _ in range(2)]
            for p in ps:
                o = p.communicate(timeout=400)[0]
                if "Traceback" in o:
                    note(rnd, "BUG", "two scans at once crashed:\n" + o[o.rindex("Traceback"):][:600])
            if diff(orig, snap(proj)):
                note(rnd, "BUG", "concurrent scans changed files")
        # 2 fix, then fix again until it settles
        out, code, dt = run(rnd, "fix", ["fix", root], 700)
        times["fix"] = dt
        after1 = snap(proj)
        changed = diff(orig, after1)
        if meta.get("expect_unchanged") and changed:
            note(rnd, "BUG", "a clean project was changed: %s" % changed[:4])
        settled = None
        prev = after1
        for k in range(3):
            run(rnd, "fix", ["fix", root], 700)
            cur = snap(proj)
            if not diff(prev, cur):
                settled = k + 2
                break
            prev = cur
        if settled is None:
            note(rnd, "BUG", "`fix` never settled: it keeps changing the project on every run (%s)" % diff(prev, snap(proj))[:3])
        elif settled > 2:
            note(rnd, "WARN", "`fix` needed %d runs to settle (a second run still changed things)" % settled)
        final = snap(proj)
        # meta checks
        for f in meta.get("valid", []):
            if orig.get(f) != final.get(f):
                note(rnd, "BUG", "valid code was modified: %s" % f)
        for f in meta.get("crlf", []):
            if f in final and final[f] != orig[f]:
                b = final[f]
                if b"\n" in b.replace(b"\r\n", b""):
                    note(rnd, "BUG", "%s: line endings became mixed after a fix (CRLF not preserved)" % f)
        for f in meta.get("md", []):
            if f in final:
                a_, b_ = orig[f].decode("utf-8", "replace"), final[f].decode("utf-8", "replace")
                if md_protected(a_) != md_protected(b_):
                    note(rnd, "BUG", "%s: code blocks / inline code / links were changed" % f)
        if meta.get("expect_py") and "tests" in meta.get("expect_py", ""):
            r = subprocess.run([PY, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"], cwd=root, capture_output=True, text=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
            if r.returncode != 0:
                note(rnd, "BUG", "the project's own tests fail after the fixes:\n" + (r.stdout + r.stderr)[-400:])
        # 3 product behaviour, original vs fixed
        if meta.get("web") and changed:
            ok, info = product_same(orig_copy, proj)
            if ok is False:
                note(rnd, "BUG", "the finished product behaves differently from the original: %s" % "; ".join(info))
            elif ok is True:
                print("   product check: identical in a real browser (%s)" % ("; ".join(info) if info else "all pages"), flush=True)
        # 4 extras on the fixed project
        for label, args in (("insights", ["insights", root]), ("advise", ["advise", root]), ("netcheck", ["netcheck", root]), ("size", ["size", root]),
                            ("leaks", ["leaks", root]) if meta.get("web") else ("proposals", ["proposals"]), ("triage", ["triage"])):
            o, c, dt = run(rnd, label, args, 300)
            times[label] = dt
        run(rnd, "proposals", ["proposals"], 60)
        # 5 undo everything
        n = 0
        for n in range(25):
            o, c, dt = run(rnd, "undo", ["undo"], 120)
            if "Nothing to undo" in o:
                break
        else:
            note(rnd, "BUG", "undo never reached 'Nothing to undo' (25 tries)")
        back = snap(proj)
        if diff(orig, back):
            bad = diff(orig, back)
            note(rnd, "BUG", "undo did not restore the original project (%d file(s) differ: %s)" % (len(bad), bad[:4]))
        print("   times: " + ", ".join("%s %.1fs" % kv for kv in times.items()) + " | round total %.0fs | undo steps %d | files %d" % (time.time() - t_round, n, len(orig)), flush=True)
    except Exception as e:
        import traceback
        note(rnd, "BUG", "the test itself failed: %s\n%s" % (e, traceback.format_exc()[-500:]))
    finally:
        for p in tmp.rglob("*"):
            try:
                os.chmod(p, 0o755) if not p.is_symlink() else None
            except OSError:
                pass
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", type=int, nargs="*")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    if a.list:
        for n, name, _ in ROUNDS:
            print(n, name)
        return 0
    reports, backups = HERE / "reports", HERE / "backups"
    saved = Path(tempfile.mkdtemp(prefix="arachnid-qa-reports-"))
    if reports.exists():
        shutil.copytree(reports, saved / "reports")
    before_b = {p.name for p in backups.iterdir()} if backups.exists() else set()
    try:
        for n, name, fn in ROUNDS:
            if a.round and n not in a.round:
                continue
            pipeline(n, name, fn)
    finally:
        shutil.rmtree(reports, ignore_errors=True)
        if (saved / "reports").exists():
            shutil.copytree(saved / "reports", reports)
        else:
            reports.mkdir(exist_ok=True)
        if backups.exists():
            for p in backups.iterdir():
                if p.name not in before_b:
                    shutil.rmtree(p, ignore_errors=True)
        shutil.rmtree(saved, ignore_errors=True)
    bugs = [r for r in RESULTS if r[1] == "BUG"]
    print("\n" + "=" * 78)
    print("QA SUMMARY: %d bug(s), %d slow/warning note(s)" % (len(bugs), len(RESULTS) - len(bugs)))
    for r_, lvl, t in RESULTS:
        print("  round %d [%s] %s" % (r_, lvl, t.split("\n")[0][:160]))
    if os.environ.get("GITHUB_ACTIONS") == "true":     # on GitHub: each bug also becomes a note on the run's page
        for r_, lvl, t in bugs:
            ls = t.strip().splitlines()
            body = "\n".join(ls[:1] + (["..."] if len(ls) > 12 else []) + ls[1:][-11:])[:1800]
            print("::error title=Stress round %d::%s" % (r_, body.replace("%", "%25").replace("\r", "").replace("\n", "%0A")))
    return 1 if bugs else 0


if __name__ == "__main__":
    sys.exit(main())
