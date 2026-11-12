#!/usr/bin/env python3
"""
Arachnid SAFETY GATE - every fix, from every tool, must pass through here before a single file is changed.
=========================================================================================================
  guard(root, apply_fn)                 one yes/no for one change set
  guard_units(root, units, apply_units) many independent fixes: finds out WHICH ones are safe

How it decides (details in pixelcheck.py):
  * Web project (it has HTML pages): the project is built before and after the fix in temporary copies, every
    page is opened in a real headless browser and compared - files loaded, HTML, position of every element,
    browser errors and every pixel.  Any difference at all = the fix is NOT applied, the owner decides.
  * Project without pages: the build output must be byte-for-byte identical and the tests must give the same result.
  * If nothing can prove the fix is harmless (no browser installed, no build, no tests) the fix is NOT applied.
    "Cannot prove it" always means "ask the owner" - the gate fails closed.

When a batch fails, it is split in halves and each half is tried again, so one risky fix cannot hold back the
safe ones. A fix that is never proven safe is never applied.

There is deliberately NO switch to turn the gate off.
"""
import os
from pathlib import Path

MAX_RUNS = 12


def proven_same_program(root, items):
    """items: [(rel_path, old_text, new_text)].  Returns the set of rel_paths where the PROGRAM itself is provably
    identical - only comments or spacing differ, or the same program is written in a shorter, provably equivalent way.
    Python: the parsed program trees are equal, or equal after slim.py's equivalence rules.  JavaScript/TypeScript/CSS:
    esbuild's minified output is equal (names renamed, or names kept).  Anything else is never 'proven'."""
    import ast
    same, other = set(), []
    for rel, old, new in items:
        ext = Path(rel).suffix.lower()
        if ext == ".py":
            try:
                if ast.dump(ast.parse(old)) == ast.dump(ast.parse(new)):
                    same.add(rel)
                else:
                    # the same program written shorter: both files reduce to the same canonical program (slim.py RULES)
                    from slim import same_python_program
                    if same_python_program(old, new):
                        same.add(rel)
            except Exception:
                pass
        elif ext in (".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".css"):
            other.append((rel, old, new, ext))
    for rel, old, new, ext in other:              # no tools needed: only whole comment lines changed
        if comment_lines_only(old, new, ext):
            same.add(rel)
    other = [o for o in other if o[0] not in same]
    if other:
        try:
            from syntaxcheck import Esbuild, ESBUILD_LOADER
            es = Esbuild(root)
            if es.ok:
                loaders = [("css" if e == ".css" else ESBUILD_LOADER.get(e.lstrip("."), "jsx")) for _, _, _, e in other]
                a = es.minified([(r + ":old", o, l) for (r, o, n, e), l in zip(other, loaders)])
                b = es.minified([(r + ":new", n, l) for (r, o, n, e), l in zip(other, loaders)])
                for (r, o, n, e), x, y in zip(other, a, b):
                    if x is not None and x == y:
                        same.add(r)
                rest = [(i, o) for i, o in enumerate(other) if o[0] not in same]
                if rest:                        # names kept as written: catches the same program written shorter
                    a = es.minified([(o[0] + ":old", o[1], loaders[i]) for i, o in rest], keep_names=True)
                    b = es.minified([(o[0] + ":new", o[2], loaders[i]) for i, o in rest], keep_names=True)
                    for (i, o), x, y in zip(rest, a, b):
                        if x is not None and x == y:
                            same.add(o[0])
        except Exception:
            pass
    return same


def _line_states(text, css=False):
    """For every line: what the reader is inside of when the line STARTS - 'code', 'block' (a /* */ comment) or
    'unsure' (inside a string, a `template`, or anything we cannot be certain about)."""
    states, state, i, n, line_state = [], "code", 0, len(text), "code"
    tmpl = 0
    while i <= n:
        if i == n or text[i] == "\n":
            states.append(line_state)
            if state in ("line", "str"):
                state = "code"
            line_state = state if state in ("code", "block") and not tmpl else "unsure"
            i += 1
            continue
        c, nx = text[i], text[i + 1] if i + 1 < n else ""
        if state == "code":
            if c == "/" and nx == "*":
                state, i = "block", i + 2
                continue
            if c == "/" and nx == "/" and not css:
                state = "line"
            elif c in "'\"" :
                state, quote = "str", c
            elif c == "`" and not css:
                tmpl += 1
                state = "tmpl"
        elif state == "block":
            if c == "*" and nx == "/":
                state, i = "code", i + 2
                continue
        elif state == "str":
            if c == "\\":
                i += 2
                continue
            if c == quote:
                state = "code"
        elif state == "tmpl":
            if c == "\\":
                i += 2
                continue
            if c == "`":
                tmpl -= 1
                state = "code"
        i += 1
    return states


def comment_lines_only(old, new, ext):
    """True only when old and new differ in WHOLE comment lines and nowhere else - provable without any tool.
    Strict on purpose: every changed line must start (after spaces) with // or sit in a /* */ comment that itself
    began at the start of a line, and no backtick (a `template`) may appear before it.  Any doubt -> False."""
    css = ext == ".css"
    a, b = old.split("\n"), new.split("\n")
    if len(a) != len(b) or old == new:
        return False
    sa, sb = _line_states(old, css), _line_states(new, css)
    if len(sa) != len(a) or len(sb) != len(b):
        return False
    for k, (x, y) in enumerate(zip(a, b)):
        if x == y:
            continue
        if sa[k] != sb[k] or "`" in "\n".join(a[:k + 1]) or "`" in "\n".join(b[:k + 1]):
            return False
        for lines, st in ((a, sa[k]), (b, sb[k])):
            t = lines[k].strip()
            if st == "code":
                if t.startswith("//") and not css:
                    continue
                if t.startswith("/*") and (("*/" not in t) or t.endswith("*/")) and t.count("*/") <= 1:
                    continue
                return False
            if st == "block":
                if "*/" in t and not t.endswith("*/"):
                    return False                  # the comment ends and code follows on the same line
                j = k                             # the comment must have started at the beginning of a line
                while j >= 0 and "/*" not in lines[j]:
                    j -= 1
                if j < 0 or not lines[j].strip().startswith("/*"):
                    return False
                continue
            return False
    return True


def guard(root, apply_fn, allow_unstable=False, build_cmd="auto", log=print, repair=False):
    """Returns (ok, [explanation lines])."""
    from pixelcheck import compare, compare_without_pages, Unavailable, NoPages
    try:
        return compare(root, apply_fn, build_cmd=build_cmd, allow_unstable=allow_unstable, log=log, repair=repair)
    except NoPages:
        return compare_without_pages(root, apply_fn, build_cmd=build_cmd, repair=repair)
    except Unavailable as e:
        return False, ["The safety gate cannot run here, so nothing is changed: " + str(e)]


def guard_units(root, units, apply_units, allow_unstable=False, build_cmd="auto", log=print, repair=False):
    """units: list of independent fixes.  apply_units(copy_root, subset) applies a subset to a COPY.
    Returns (passed_units, [(unit, reason), ...], [notes])."""
    notes, runs = [], [0]
    passed, failed = [], []

    def attempt(sub):
        if not sub:
            return
        if runs[0] >= MAX_RUNS:
            failed.extend((u, "the gate ran out of its time budget before this fix was proven safe") for u in sub)
            return
        runs[0] += 1
        ok, lines = guard(root, lambda copy: apply_units(copy, sub), allow_unstable, build_cmd, log, repair)
        if ok:
            passed.extend(sub)
            notes.extend(lines[:1])
            return
        if len(sub) == 1:
            failed.append((sub[0], lines[0] if lines else "the gate said no"))
            return
        if runs[0] == 1:
            notes.append("The whole batch did not pass (%s) - trying smaller groups to find the safe fixes ..." % (lines[0] if lines else ""))
        mid = len(sub) // 2
        attempt(sub[:mid])
        attempt(sub[mid:])
    attempt(list(units))
    return passed, failed, notes


def selftest():
    """python gate.py --selftest  - proves the gate works on THIS computer, using a tiny throw-away site."""
    import shutil
    import struct
    import tempfile
    import zlib
    tmp = Path(tempfile.mkdtemp(prefix="arachnid-selftest-"))
    try:
        site = tmp / "site"
        site.mkdir()
        raw = b"".join(b"\x00" + bytes((200, 30, 30)) * 40 for _ in range(20))
        def ch(t, d):
            c = struct.pack(">I", len(d)) + t + d
            return c + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
        (site / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", struct.pack(">IIBBBBB", 40, 20, 8, 2, 0, 0, 0))
                                        + ch(b"IDAT", zlib.compress(raw)) + ch(b"IEND", b""))
        (site / "package.json").write_text("{}")
        (site / "index.html").write_text('<html><head><style>img{width:40px}</style></head><body><h1>Test</h1><img src="logo.png"></body></html>')
        print("Self-test 1 of 2: a harmless change (a comment added to the page) - the gate should say YES ...")
        ok1, l1 = guard(site, lambda c: (Path(c) / "index.html").write_text(
            (Path(c) / "index.html").read_text() + "<!-- harmless -->"))
        print("   ->", "YES (correct)" if ok1 else "NO (wrong)", "|", l1[0][:160])
        print("Self-test 2 of 2: a change that moves the picture by ONE pixel - the gate should say NO ...")
        ok2, l2 = guard(site, lambda c: (Path(c) / "index.html").write_text(
            (Path(c) / "index.html").read_text().replace("width:40px", "width:41px")))
        print("   ->", "NO (correct)" if not ok2 else "YES (wrong - the gate missed it!)", "|", l2[0][:160])
        good = ok1 and not ok2
        print("\nRESULT:", "the safety gate works on this computer." if good else "the gate is NOT working correctly here - do not apply fixes.")
        return 0 if good else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    print(__doc__)
