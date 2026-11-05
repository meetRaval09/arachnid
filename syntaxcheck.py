#!/usr/bin/env python3
"""
Arachnid - Step 3: SYNTAX CHECKER
=================================
"Syntax" = the grammar rules of a programming language. A missing bracket, a missing comma or a
quote that never closes is like a sentence without a full stop: the computer cannot read the file at all.

This tool reads every code file and tells you, in plain words, WHERE the grammar is broken.
By default it only reports. With --fix it also REPAIRS the mistakes it is sure about (see "Auto-fix" below).

Run it:
    python syntaxcheck.py                 check the project (the folder above arachnid/)
    python syntaxcheck.py "C:\\some\\folder"
    python syntaxcheck.py --out report.json
    python syntaxcheck.py --fix --preview  show what it WOULD repair, change nothing
    python syntaxcheck.py --fix            repair (backup first), then re-check every repaired file
    python syntaxcheck.py --undo           put the last repair back, byte for byte

Auto-fix - how it stays safe: for each broken file it tries small repairs near the error (add a missing
bracket / quote / comma / colon, or remove a stray comma or bracket) and asks the real parser "is the file
valid now?". A repair is applied only if the answer is YES and exactly ONE repair works. If two different
repairs both work, the tool cannot know which one you meant, so it shows you both and changes nothing.
If a file cannot be fully repaired, it is left untouched. Originals are always backed up.

What it understands (no installs needed, Python 3.10+ only):
    Python      -> Python's own parser (exact line and column)
    JSON        -> Python's JSON parser (also tolerates // comments in .jsonc)
    JS/JSX/TS   -> esbuild if the project has it (what Vite uses, so answers match your real build),
                   otherwise `node --check`, otherwise a bracket/quote balance check
    HTML        -> unclosed / stray tags, plus the JavaScript and CSS written inside the page
    CSS / GLSL  -> brackets, parentheses, quotes, comments that never end
    SQL         -> brackets and quotes
    Markdown    -> code fences (```) that are never closed
"""
import argparse
import ast
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from spellcheck import SKIP_DIRS, SKIP_FILES, MAX_BYTES  # same ignore rules as the spell checker

KINDS = {
    "py": "py", "json": "json", "jsonc": "jsonc",
    "js": "js", "mjs": "js", "cjs": "js", "jsx": "js", "ts": "js", "tsx": "js",
    "css": "css", "scss": "css", "less": "css",
    "frag": "glsl", "vert": "glsl", "glsl": "glsl",
    "sql": "sql", "html": "html", "htm": "html", "md": "md",
}
ESBUILD_LOADER = {"js": "jsx", "mjs": "jsx", "cjs": "jsx", "jsx": "jsx", "ts": "ts", "tsx": "tsx"}


# ----------------------------------------------------------------------------
# Plain-English explanations
# ----------------------------------------------------------------------------
HINTS = [
    (r"unterminated|string starting|unclosed string|string literal|eol while|missing terminating",
     "A quote was opened but never closed - like opening a bracket in a sentence and never closing it. "
     "Look for a missing closing quote at the end of this line."),
    (r"tag opened here|code block|comment that starts",
     "Something was opened (a tag, code block or comment) and never closed. Add the matching closing part where it should end."),
    (r"was never closed|never closed|expected '?[\)\]\}]|expected closing|unexpected end|unexpected eof|unexpected end of file|unmatched '\(",
     "A bracket was opened but the matching closing bracket is missing (or the file ended too early). "
     "Check the opening bracket named here and make sure it has a partner."),
    (r"unexpected '?[\)\]\}]|unmatched|closing .* does not match|stray closing|without matching|unbalanced",
     "A closing bracket or tag appears where nothing is open, or it does not match what was opened. "
     "There is probably one closing bracket too many, or a missing opening one earlier."),
    (r"expecting ',' delimiter|expecting value|expected ',|expecting property name|trailing comma|unexpected token ?[\],}]|expected \"?,\"?|expected \"?:\"?",
     "A comma or colon is missing or extra. In JSON the last item must NOT have a comma after it, "
     "and every item except the last needs one."),
    (r"invalid syntax|unexpected token|expected an? |unexpected character|syntax error|expected expression",
     "Something is written in a place where the language does not allow it. The real cause is often just BEFORE "
     "this spot - a missing comma, bracket, quote or operator on the previous line."),
    (r"indent",
     "The spacing at the start of the line does not line up with the lines around it. "
     "Python uses indentation like paragraphs: lines in the same block must start at the same column."),
    (r"import|export",
     "An import/export line is not valid here. It must be at the top level of the file, not inside a function or block."),
]


def explain(msg):
    low = msg.lower()
    for pat, text in HINTS:
        if re.search(pat, low):
            return text
    return "The language's grammar rules are broken at this spot. Look at this line and the one just before it."


def finding(rel, kind, line, col, msg, lines):
    line = max(1, int(line or 1))
    snippet = lines[line - 1].rstrip("\r\n") if 0 < line <= len(lines) else ""
    if len(snippet) > 160:
        c = max(0, (col or 1) - 60)
        snippet = snippet[c:c + 140]
    return {"file": rel, "line": line, "col": int(col or 1), "kind": kind, "message": msg.strip(),
            "explanation": explain(msg), "context": snippet.strip()}


# ----------------------------------------------------------------------------
# Bracket / quote balance (for CSS, GLSL, SQL, Markdown-free fallbacks)
# ----------------------------------------------------------------------------
PAIRS = {")": "(", "]": "[", "}": "{"}
NAMES = {"(": "parenthesis", "[": "square bracket", "{": "curly brace"}


def check_balance(text, multiline_strings=False, hash_comments=False, dash_comments=False, slash_comments=True):
    """Returns a list of (line, col, message)."""
    errs = []
    stack = []
    i, n = 0, len(text)
    line, ls = 1, 0  # current line number and index where it started
    while i < n:
        c = text[i]
        if c == "\n":
            line += 1
            ls = i + 1
            i += 1
            continue
        two = text[i:i + 2]
        if slash_comments and two == "//":
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if dash_comments and two == "--":
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if hash_comments and c == "#":
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if two == "/*":
            j = text.find("*/", i + 2)
            if j < 0:
                errs.append((line, i - ls + 1, "Comment that starts with /* is never closed with */"))
                return errs
            seg = text[i:j + 2]
            line += seg.count("\n")
            if "\n" in seg:
                ls = i + seg.rfind("\n") + 1
            i = j + 2
            continue
        if c in "\"'":
            q, sl, sc = c, line, i - ls + 1
            j = i + 1
            closed = False
            while j < n:
                d = text[j]
                if d == "\\" and not dash_comments:
                    j += 2
                    continue
                if d == q:
                    if dash_comments and text[j + 1:j + 2] == q:  # SQL '' escape
                        j += 2
                        continue
                    closed = True
                    break
                if d == "\n" and not multiline_strings:
                    break
                j += 1
            if not closed:
                errs.append((sl, sc, "String starting with %s is never closed" % q))
                if multiline_strings:
                    return errs
                i = j  # newline reached: keep going with the next line
                continue
            seg = text[i:j + 1]
            if "\n" in seg:
                line += seg.count("\n")
                ls = i + seg.rfind("\n") + 1
            i = j + 1
            continue
        if c in "([{":
            stack.append((c, line, i - ls + 1))
        elif c in ")]}":
            if not stack:
                errs.append((line, i - ls + 1, "Unexpected closing %s - nothing is open" % NAMES[PAIRS[c]]))
            elif stack[-1][0] != PAIRS[c]:
                o, ol, oc = stack[-1]
                errs.append((line, i - ls + 1, "Closing %s does not match the %s opened on line %d" % (
                    NAMES[PAIRS[c]], NAMES[o], ol)))
                # try to recover: drop the opener if it is further down the stack
                for k in range(len(stack) - 1, -1, -1):
                    if stack[k][0] == PAIRS[c]:
                        del stack[k:]
                        break
            else:
                stack.pop()
        i += 1
    for o, ol, oc in stack[:3]:
        errs.append((ol, oc, "The %s opened here was never closed" % NAMES[o]))
    return errs[:6]


# ----------------------------------------------------------------------------
# Per-language checks. Each returns a list of (line, col, message)
# ----------------------------------------------------------------------------
def check_py(text, path):
    try:
        ast.parse(text, filename=str(path))
    except SyntaxError as e:
        return [(e.lineno or 1, e.offset or 1, e.msg or "invalid syntax")]
    except (ValueError, RecursionError) as e:
        return [(1, 1, str(e))]
    return []


def strip_jsonc(text):
    out, i, n, in_s = [], 0, len(text), False
    while i < n:
        c = text[i]
        if in_s:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 1
            elif c == '"':
                in_s = False
        elif c == '"':
            in_s = True
            out.append(c)
        elif text[i:i + 2] == "//":
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif text[i:i + 2] == "/*":
            j = text.find("*/", i + 2)
            seg = text[i:(j + 2 if j >= 0 else n)]
            out.append(re.sub(r"[^\n]", " ", seg))
            i += len(seg)
            continue
        else:
            out.append(c)
        i += 1
    return "".join(out)


def check_json(text, jsonc=False):
    src = strip_jsonc(text) if jsonc else text
    if jsonc:
        src = re.sub(r",(\s*[\]}])", r"\1", src)  # trailing commas are allowed in jsonc
    if not src.strip():
        return [(1, 1, "The file is empty - JSON needs at least {} or []")]
    try:
        json.loads(src)
    except json.JSONDecodeError as e:
        return [(e.lineno, e.colno, e.msg)]
    return []


class Esbuild:
    """Asks esbuild (the fast parser used by Vite) to read many files in ONE call."""

    def __init__(self, root):
        self.dir = self._find(Path(root))
        self.node = shutil_which("node")
        self.ok = bool(self.dir and self.node)

    @staticmethod
    def _find(root):
        todo = [(root, 0)]
        while todo:
            d, depth = todo.pop(0)
            if (d / "node_modules" / "esbuild" / "package.json").exists():
                return d
            if depth < 3:
                try:
                    for e in sorted(os.scandir(d), key=lambda e: e.name):
                        if e.is_dir(follow_symlinks=False) and e.name not in ("node_modules", ".git") \
                                and not e.name.startswith(".cms"):
                            todo.append((Path(e.path), depth + 1))
                except OSError:
                    pass
        return None

    JS = r"""
const path = require('path');
const es = require(require.resolve('esbuild', { paths: [process.argv[1]] }));
try { es.transformSync('1', { loader: 'js', logLevel: 'silent' }); }
catch (e) { process.stderr.write('esbuild unusable here: ' + String(e.message || e).split('\n')[0]); process.exit(3); }
const items = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const out = [];
for (const it of items) {
  try { es.transformSync(it.code, { loader: it.loader, logLevel: 'silent', sourcefile: it.id }); out.push([]); }
  catch (e) {
    out.push((e.errors || [{ text: String(e.message || e) }]).slice(0, 3).map(x => [
      x.location ? x.location.line : 1, x.location ? x.location.column + 1 : 1, x.text]));
  }
}
process.stdout.write(JSON.stringify(out));
"""

    MIN_JS = r"""
const es = require(require.resolve('esbuild', { paths: [process.argv[1]] }));
const items = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const out = items.map(it => { try { return es.transformSync(it.code, { loader: it.loader, minify: true, legalComments: 'none', logLevel: 'silent' }).code; } catch (e) { return null; } });
process.stdout.write(JSON.stringify(out));
"""

    def minified(self, items):
        """items: list of (id, code, loader) -> list of minified code (None = could not be read).
        Minifying removes comments and spacing, so two texts with the same result are the SAME PROGRAM."""
        payload = json.dumps([{"id": i, "code": c, "loader": l} for i, c, l in items])
        r = subprocess.run([self.node, "-e", self.MIN_JS, str(self.dir)], input=payload, capture_output=True,
                           text=True, encoding="utf-8", timeout=300)
        if r.returncode != 0:
            raise RuntimeError(r.stderr[-300:])
        return json.loads(r.stdout)

    def run(self, items):
        """items: list of (id, code, loader) -> list of error lists"""
        payload = json.dumps([{"id": i, "code": c, "loader": l} for i, c, l in items])
        r = subprocess.run([self.node, "-e", self.JS, str(self.dir)], input=payload, capture_output=True,
                           text=True, encoding="utf-8", timeout=300)
        if r.returncode != 0:
            raise RuntimeError(r.stderr[-300:])
        return json.loads(r.stdout)


def shutil_which(name):
    from shutil import which
    return which(name)


def node_check(code, node):
    """Fallback for plain JavaScript: `node --check` on a temporary .mjs copy."""
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "x.mjs"
        f.write_text(code, encoding="utf-8")
        r = subprocess.run([node, "--check", str(f)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    if r.returncode == 0:
        return []
    err = r.stderr
    m = re.search(r"x\.mjs:(\d+)", err)
    line = int(m.group(1)) if m else 1
    msg = ""
    for ln in err.splitlines():
        if re.match(r"\w*Error", ln):
            msg = ln
            break
    return [(line, 1, msg or "syntax error")]


class HtmlCheck(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source",
            "track", "wbr", "command", "keygen"}
    OPTIONAL_END = {"p", "li", "dt", "dd", "tr", "td", "th", "option", "optgroup", "thead", "tbody", "tfoot",
                    "colgroup", "caption", "head", "body", "html", "rt", "rp"}

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.stack, self.errs, self.scripts, self.styles = [], [], [], []
        self._cur = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "style"):
            self._cur = (tag, a, self.getpos(), [])
        if tag not in self.VOID:
            self.stack.append((tag, self.getpos()))

    def handle_startendtag(self, tag, attrs):
        pass  # <x /> is complete by itself

    def handle_data(self, data):
        if self._cur:
            self._cur[3].append(data)

    def handle_endtag(self, tag):
        if self._cur and self._cur[0] == tag:
            t, a, pos, parts = self._cur
            body = "".join(parts)
            # the body starts on the line of the opening tag (first line may be empty)
            (self.scripts if t == "script" else self.styles).append((pos[0], a, body))
            self._cur = None
        if tag in self.VOID:
            return
        for k in range(len(self.stack) - 1, -1, -1):
            if self.stack[k][0] == tag:
                for t, p in self.stack[k + 1:]:
                    if t not in self.OPTIONAL_END:
                        self.errs.append((p[0], p[1] + 1, "The <%s> tag opened here is never closed" % t))
                del self.stack[k:]
                return
        self.errs.append((self.getpos()[0], self.getpos()[1] + 1,
                          "Stray closing tag </%s> - no <%s> is open" % (tag, tag)))

    def finish(self):
        for t, p in self.stack:
            if t not in self.OPTIONAL_END:
                self.errs.append((p[0], p[1] + 1, "The <%s> tag opened here is never closed" % t))
        return self.errs


def check_html(text):
    """Returns (errors, embedded) where embedded = [(kind, first_line, code)]"""
    p = HtmlCheck()
    try:
        p.feed(text)
        p.close()
    except Exception as e:  # the parser is lenient; this is only a safety net
        return [(1, 1, "Could not read this HTML: %s" % e)], []
    errs = p.finish()
    emb = []
    for line, a, body in p.scripts:
        if "src" in a and not body.strip():
            continue
        t = (a.get("type") or "").lower()
        if t in ("", "module", "text/javascript", "application/javascript"):
            emb.append(("js", line, body))
        elif t in ("application/json", "application/ld+json", "importmap"):
            emb.append(("json", line, body))
    for line, a, body in p.styles:
        emb.append(("css", line, body))
    return errs, emb


def check_md(text):
    errs, fence = [], None
    for i, ln in enumerate(text.splitlines(), 1):
        m = re.match(r"^\s{0,3}(`{3,}|~{3,})", ln)
        if m:
            if fence is None:
                fence = (m.group(1)[0], len(m.group(1)), i)
            elif m.group(1)[0] == fence[0] and len(m.group(1)) >= fence[1]:
                fence = None
    if fence:
        errs.append((fence[2], 1, "This code block (%s) is opened here but never closed" % (fence[0] * 3)))
    return errs


# ----------------------------------------------------------------------------
# Auto-fix: try small repairs, let the real parser judge
# ----------------------------------------------------------------------------
FIXABLE = {"py", "json", "jsonc", "js", "css", "glsl", "sql", "md"}
MAX_FIX_BYTES = 300_000
MAX_STEPS = 6


class Oracle:
    """Answers: 'what is the first syntax error in this text?' (None = the text is valid)."""

    def __init__(self, root):
        self.es = Esbuild(root)
        self.es_ok = False
        if self.es.ok:
            try:
                self.es.run([("probe", "1", "js")])
                self.es_ok = True
            except Exception:
                pass
        self.node = shutil_which("node")

    def can(self, ext):
        kind = KINDS[ext]
        if kind != "js":
            return True
        return self.es_ok or (bool(self.node) and ext in ("js", "mjs", "cjs"))

    def first_errors(self, texts, ext):
        kind = KINDS[ext]
        if kind == "js":
            if self.es_ok:
                out = self.es.run([("c%d" % i, t, ESBUILD_LOADER.get(ext, "jsx")) for i, t in enumerate(texts)])
                return [(e[0][0], e[0][1], e[0][2]) if e else None for e in out]
            with ThreadPoolExecutor(max_workers=8) as ex:
                res = list(ex.map(lambda t: node_check(t, self.node), texts))
            return [r[0] if r else None for r in res]
        return [self._one(t, ext, kind) for t in texts]

    @staticmethod
    def _one(t, ext, kind):
        if kind == "py":
            e = check_py(t, "x.py")
        elif kind in ("json", "jsonc"):
            e = check_json(t, kind == "jsonc")
        elif kind == "css" or kind == "glsl":
            e = check_balance(t)
        elif kind == "sql":
            e = check_balance(t, multiline_strings=True, dash_comments=True, slash_comments=False)
        else:
            e = check_md(t)
        return min(e) if e else None


def _starts(text):
    st = [0]
    for m in re.finditer("\n", text):
        st.append(m.end())
    return st


def _abs(text, starts, line, col):
    line = min(max(line, 1), len(starts))
    return min(starts[line - 1] + max(col, 1) - 1, len(text))


def candidates(text, ext, err):
    """Small edits near the error. Returns list of (description, new_text, edit_position, size_change)."""
    kind = KINDS[ext]
    line, col, _msg = err
    starts = _starts(text)
    lines = text.split("\n")
    out, seen = [], set()

    def add(desc, new, pos):
        if new != text and new not in seen:
            seen.add(new)
            out.append((desc, new, pos, len(new) - len(text)))

    if kind == "md":
        nl = "" if text.endswith("\n") or not text else "\n"
        add("closed the code block at the end of the file", text + nl + "```\n", len(text))
        return out
    if kind == "py":
        ins_end = [")", "]", "}", ":", ",", '"', "'"]
    elif kind == "json" or kind == "jsonc":
        ins_end = [",", "]", "}", '"']
    elif kind == "js":
        ins_end = [")", "]", "}", ";", ",", '"', "'", "`", ":"]
    elif kind == "sql":
        ins_end = [")", "'", ";"]
    else:
        ins_end = [")", "]", "}", ";", '"', "'"]
    ins_here = [c for c in ins_end if c not in ('"', "'", "`")] + ([] if kind in ("json", "jsonc") else [])
    closer = None
    m = re.search(r"(curly brace|square bracket|parenthesis|'\(' |'\[' |'\{' ).*never closed|never closed", _msg or "")
    if m and re.search(r"never closed", _msg or ""):
        low = (_msg or "").lower()
        closer = ("}" if ("curly" in low or "'{'" in low) else "]" if ("square" in low or "'['" in low)
                  else ")" if ("parenthesis" in low or "'('" in low) else None)
    scan = set(range(line - 1, line + 2))
    if closer:
        scan |= set(range(line, min(len(lines), line + 60) + 1))
    for ln in sorted(scan):
        if not 1 <= ln <= len(lines):
            continue
        raw = lines[ln - 1]
        body = raw[:-1] if raw.endswith("\r") else raw
        base = len(body.rstrip())
        poss = {base}
        if body[:base].endswith((";", ",")):
            poss.add(base - 1)
        for pos in poss:
            at = starts[ln - 1] + pos
            for ch in ins_end:
                add("added %r at the end of line %d" % (ch, ln), text[:at] + ch + text[at:], at)
    off = _abs(text, starts, line, col)
    for o in {off, off - 1, off - 2, off - 3}:
        if 0 <= o < len(text):
            ch = text[o]
            if ch in ",)]}" or (ch == ";" and kind in ("py",)) or (ch == ":" and kind in ("js", "py")):
                add("removed the extra %r on line %d" % (ch, text.count("\n", 0, o) + 1), text[:o] + text[o + 1:], o)
            if ch in ")]}":
                for rep in ")]}":
                    if rep != ch:
                        add("replaced %r with %r on line %d" % (ch, rep, text.count("\n", 0, o) + 1),
                            text[:o] + rep + text[o + 1:], o)
    for ch in ins_here:
        add("added %r at line %d, column %d" % (ch, line, col), text[:off] + ch + text[off:], off)
    nl = "" if text.endswith("\n") or not text else "\n"
    for ch in ("}", ")", "]"):
        add("added %r at the very end of the file" % ch, text + nl + ch + "\n", len(text))
    return out


def _tiebreak(ok):
    """Several repairs work. Merge the ones that mean the same thing:
       1) identical apart from spaces/new-lines (a comma at the end of a line or the start of the next);
       2) the same closer placed on the same line before or after the ending ';' / ',' - a closing quote or
          bracket belongs BEFORE the ';' (foo(1, 2) ;), a closing curly brace AFTER it."""
    groups = {}
    for c in ok:
        groups.setdefault(re.sub(r"\s+", "", c[1]), []).append(c)
    if len(groups) == 1:
        pick = [c for c in ok if "end of line" in c[0]] or ok
        return pick[:1]
    ends = [c for c in ok if "at the end of line" in c[0]]
    if len(ends) == len(ok) and len({c[0].split(" at the end")[0] + c[0].split("line")[-1] for c in ok}) == 1:
        ch = ok[0][0].split()[1].strip("'\"") or ""
        want_after = ch in ("}", ";")
        ranked = sorted(ok, key=lambda c: c[2], reverse=want_after)
        return ranked[:1]
    return ok


class Resolver:
    """When SEVERAL repairs make a file valid, gather evidence from the rest of the project to find the one
    that was really meant. Evidence, strongest first:
      1. TWIN / HISTORY - an intact copy of the same file elsewhere in the project (main/main/..., new_version-site/...)
         or its last saved version in git. The repair that rebuilds that copy wins.
      2. SHAPE - the way the file is indented shows where a block really ends.
      3. BUILD - run the project's own build command with each repair; a repair that breaks the build is out.
    A repair is chosen only if the evidence leaves exactly ONE winner. Otherwise nothing is changed."""

    def __init__(self, root, index, oracle, verify_cmd=None, allow_build=True):
        self.root, self.index, self.oracle = root, index, oracle
        self.verify_cmd, self.allow_build = verify_cmd, allow_build
        self.git = shutil_which("git")

    # ---- evidence 1: twins and git history
    def _references(self, rel, ext):
        refs = []
        base = rel.rsplit("/", 1)[-1]
        for other in self.index.get(base.lower(), []):
            if other != rel:
                try:
                    refs.append((other, (self.root / other).read_text(encoding="utf-8")))
                except (OSError, UnicodeDecodeError):
                    pass
        if self.git:
            try:
                f = self.root / rel
                r = subprocess.run([self.git, "show", "HEAD:./" + f.name], cwd=str(f.parent), capture_output=True,
                                   text=True, encoding="utf-8", timeout=20)
                if r.returncode == 0 and r.stdout:
                    refs.append(("git history (last saved version)", r.stdout))
            except Exception:
                pass
        good = []
        for name, txt in refs:
            if self.oracle.can(ext) and self.oracle.first_errors([txt], ext)[0] is None:
                good.append((name, txt))
        return good

    @staticmethod
    def _sim(a, b):
        norm = lambda t: [re.sub(r"\s+", "", l) for l in t.split("\n") if l.strip()]
        return difflib.SequenceMatcher(None, norm(a), norm(b), autojunk=False).ratio()

    def _by_twin(self, rel, ext, ok):
        scores, names = [0.0] * len(ok), [None] * len(ok)
        for name, txt in self._references(rel, ext):
            for i, c in enumerate(ok):
                v = self._sim(c[1], txt)
                if v > scores[i]:
                    scores[i], names[i] = v, name
        order = sorted(range(len(ok)), key=lambda i: -scores[i])
        if scores[order[0]] >= 0.85 and (len(ok) == 1 or scores[order[0]] - scores[order[1]] >= 0.01):
            return [ok[order[0]]], "rebuilt to match the intact copy: %s" % names[order[0]]
        return ok, None

    # ---- evidence 2: indentation shows where the block ends
    @staticmethod
    def _by_shape(cur, ok, err):
        if not re.search(r"never closed", err[2]):
            return ok, None
        lines = cur.split("\n")
        if not 1 <= err[0] <= len(lines):
            return ok, None

        def ind(t):
            return len(t.expandtabs(4)) - len(t.expandtabs(4).lstrip())
        base = ind(lines[err[0] - 1])
        passing = []
        for c in ok:
            m = re.search(r"end of line (\d+)", c[0])
            if m:
                last = int(m.group(1))
            elif "very end of the file" in c[0]:
                last = len(lines)
            else:
                continue
            inner = [l for l in lines[err[0]:last] if l.strip()]
            after = next((l for l in lines[last:] if l.strip()), None)
            if all(ind(l) > base for l in inner) and (after is None or ind(after) <= base):
                passing.append(c)
        if 0 < len(passing) < len(ok):
            if len(passing) == 1 or len({re.sub(r"\s+", "", c[1]) for c in passing}) == 1:
                return [passing[0]], "the indentation shows the block ends there"
            return passing, None
        return ok, None

    # ---- evidence 3: the project's own build
    def _build_dir(self, rel):
        d = (self.root / rel).parent
        while True:
            pj = d / "package.json"
            if pj.exists():
                try:
                    if "build" in (json.loads(pj.read_text(encoding="utf-8")).get("scripts") or {}):
                        return d
                except Exception:
                    pass
            if d == self.root or d.parent == d:
                return None
            d = d.parent

    def _by_build(self, rel, ok):
        if not self.allow_build:
            return ok, None
        pkg = self._build_dir(rel)
        cmd = self.verify_cmd or ("npm run build" if pkg else None)
        if cmd is None:
            return ok, None
        pkg = pkg or self.root
        passed = []
        for c in ok:
            tmp = Path(tempfile.mkdtemp(prefix="arachnid-build-"))
            try:
                work = tmp / "p"
                shutil.copytree(pkg, work, symlinks=True, ignore=shutil.ignore_patterns(
                    "node_modules", ".git", "dist", "build", "backups", ".cache", ".wrangler", "arachnid"))
                nm = pkg / "node_modules"
                if nm.exists():
                    os.symlink(nm, work / "node_modules", target_is_directory=True)
                (work / (self.root / rel).relative_to(pkg)).write_text(c[1], encoding="utf-8", newline="")
                r = subprocess.run(cmd, cwd=str(work), shell=True, capture_output=True, timeout=300)
                if r.returncode == 0:
                    passed.append(c)
            except Exception:
                pass
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        if 0 < len(passed) < len(ok):
            if len(passed) == 1:
                return passed, "only this repair keeps '%s' working" % cmd
            return passed, None
        return ok, None

    # ---- last resort for badly broken files: borrow ONLY the damaged lines from a healthy copy
    def heal(self, rel, ext, text, err):
        """If an intact twin / git version exists, copy back just the lines that differ near the damage,
        one patch at a time (closest to the error first), until the file is valid. Then drop every patch
        that turned out not to be needed, so your own newer edits elsewhere in the file are kept."""
        best = None
        a = text.split("\n")
        for name, ref in self._references(rel, ext):
            b = ref.split("\n")
            ops = []
            for o in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
                if o[0] == "equal":
                    continue
                if o[0] == "replace" and o[2] - o[1] == o[4] - o[3]:     # same length: patch line by line
                    ops += [("replace", o[1] + k, o[1] + k + 1, o[3] + k, o[3] + k + 1) for k in range(o[2] - o[1])]
                else:
                    ops.append(o)
            if not ops or len(ops) > 40:
                continue
            dist = lambda o: 0 if o[1] <= err[0] - 1 < max(o[2], o[1] + 1) else min(abs(o[1] - (err[0] - 1)), abs(o[2] - (err[0] - 1)))
            order = sorted(ops, key=dist)

            def build(chosen):
                out, pos = [], 0
                for o in ops:
                    out += a[pos:o[1]]
                    out += b[o[3]:o[4]] if o in chosen else a[o[1]:o[2]]
                    pos = o[2]
                return "\n".join(out + a[pos:])
            chosen, done = [], None
            for o in order:
                chosen.append(o)
                t = build(chosen)
                if self.oracle.first_errors([t], ext)[0] is None:
                    done = t
                    break
            if done is None:
                continue
            for o in list(chosen):                      # keep only the patches that are really needed
                trial = [x for x in chosen if x is not o]
                t = build(trial)
                if trial and self.oracle.first_errors([t], ext)[0] is None:
                    chosen, done = trial, t
            lines = sorted((o[1] + 1, max(o[2], o[1] + 1)) for o in chosen)
            desc = "restored line%s %s from %s (the rest of the file was kept)" % (
                "s" if len(lines) > 1 or lines[0][0] != lines[0][1] else "",
                ", ".join(("%d" % x if x == y else "%d-%d" % (x, y)) for x, y in lines), name)
            size = sum(o[2] - o[1] + o[4] - o[3] for o in chosen)
            if best is None or size < best[0]:
                best = (size, done, desc)
        return (best[1], best[2]) if best else (None, None)

    def pick(self, rel, ext, cur, ok, err):
        stages = (("matches an intact copy", lambda o: self._by_twin(rel, ext, o)),
                  ("the indentation shows the block ends there", lambda o: self._by_shape(cur, o, err)),
                  ("only this repair keeps the project building", lambda o: self._by_build(rel, o)))
        for label, stage in stages:
            before = len(ok)
            ok, note = stage(ok)
            if len(ok) > 1:
                ok = _tiebreak(ok)
            if len(ok) == 1 and (note or len(ok) < before):
                return ok[0], note or label
        return None, None


def fix_text(text, ext, oracle, resolver=None, rel=""):
    """Returns (new_text or None, steps[list of descriptions], proposals[list]). Never guesses between equals."""
    steps, cur = [], text
    for _ in range(MAX_STEPS):
        err = oracle.first_errors([cur], ext)[0]
        if err is None:
            return (cur if cur != text else None), steps, []
        cands = candidates(cur, ext, err)
        if not cands:
            return None, [], []
        starts = _starts(cur)
        orig_abs = _abs(cur, starts, err[0], err[1])
        results = oracle.first_errors([c[1] for c in cands], ext)
        ok = [c for c, r in zip(cands, results) if r is None]
        if len(ok) > 1:
            ok = _tiebreak(ok)
        if len(ok) > 1 and resolver:
            chosen, note = resolver.pick(rel, ext, cur, ok, err)
            if chosen:
                steps.append("%s   [evidence: %s]" % (chosen[0], note))
                cur = chosen[1]
                continue
        if len(ok) == 1:
            steps.append(ok[0][0])
            cur = ok[0][1]
            continue
        if len(ok) > 1:
            return None, [], [c[0] for c in ok[:4]]
        best, best_gain, tie = None, 0, False
        for c, r in zip(cands, results):
            ns = _starts(c[1])
            shift = c[3] if c[2] <= orig_abs else 0
            gain = _abs(c[1], ns, r[0], r[1]) - (orig_abs + shift)
            if gain > best_gain:
                best, best_gain, tie = c, gain, False
            elif gain == best_gain and gain > 0 and c[1] != best[1]:
                tie = True
        if best is None or tie:
            return None, [], []
        steps.append(best[0])
        cur = best[1]
    return None, [], []


def apply_syntax_fixes(root, result, preview, verify_cmd=None, allow_build=True):
    by_file = defaultdict(list)
    for f in result["findings"]:
        by_file[f["file"]].append(f)
    oracle = Oracle(root)
    index = defaultdict(list)
    for fp, _e in walk(root):
        index[fp.name.lower()].append(fp.relative_to(root).as_posix())
    resolver = Resolver(root, index, oracle, verify_cmd, allow_build)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    bdir = HERE / "backups" / stamp
    fixed, stuck, ambiguous = [], [], []
    for rel, items in sorted(by_file.items()):
        ext = rel.rsplit(".", 1)[-1].lower()
        if KINDS.get(ext) not in FIXABLE or not oracle.can(ext):
            stuck.append((rel, "this file type cannot be repaired safely on this computer"))
            continue
        path = root / rel
        try:
            if path.stat().st_size > MAX_FIX_BYTES:
                stuck.append((rel, "file is too big for safe auto-repair"))
                continue
            with open(path, encoding="utf-8", newline="") as fh:
                text = fh.read()
        except OSError:
            continue
        new, steps, props = fix_text(text, ext, oracle, resolver, rel)
        if new is not None:
            fixed.append((rel, steps, text, new))
        elif props:
            ambiguous.append((rel, props))
        else:
            healed, why = None, None
            try:
                e0 = oracle.first_errors([text], ext)[0]
                if e0:
                    healed, why = resolver.heal(rel, ext, text, e0)
            except Exception:
                pass
            if healed is not None and healed != text:
                fixed.append((rel, [why + "   [evidence: healthy copy]"], text, healed))
            else:
                stuck.append((rel, "badly broken and no healthy copy to learn from - needs a human"))
    held, held_why = [], {}
    if fixed and not preview:
        from gate import guard_units
        print("\nSafety gate: proving the product stays exactly the same (before / after on temporary copies) ...")
        def _apply_units(copy, units):
            for rel_, _st, _old, new_ in units:
                with open(Path(copy) / rel_, "w", encoding="utf-8", newline="") as fh:
                    fh.write(new_)
        passed, failed, notes = guard_units(root, fixed, _apply_units, build_cmd=verify_cmd or "auto", repair=True)
        for n in notes:
            print("   " + n)
        for u, why in failed:
            print(f"   HELD BACK for the owner: {u[0]} - {why}")
        held = [u for u, _w in failed]
        held_why = {u[0]: w for u, w in failed}
        fixed = passed
    if not preview and fixed:
        log = []
        for rel, steps, old, new in fixed:
            dest = bdir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / rel, dest)
            with open(root / rel, "w", encoding="utf-8", newline="") as fh:
                fh.write(new)
            log.append({"file": rel, "fixes": steps})
        (bdir / "manifest.json").write_text(json.dumps({"root": str(root), "files": [f[0] for f in fixed], "fixes": log}, indent=2), encoding="utf-8")
    try:
        import ledger
        first_line = {}
        for x in result["findings"]:
            first_line.setdefault(x["file"], x["line"])
        for rel, steps, old, new in fixed:
            ledger.record("syntax", "fixable" if preview else "fixed", "info", rel, first_line.get(rel, 0),
                          ("syntax error can be repaired: " if preview else "syntax error repaired: ") + (steps[0] if steps else ""), kind="syntax")
        for u in held:
            ledger.record("syntax", "held", "warning", u[0], first_line.get(u[0], 0),
                          "a repair exists, but the safety gate held it back: " + str(held_why.get(u[0], ""))[:160],
                          "applying it could change how the product behaves", "look at the proposed repair and decide", "syntax")
        for rel, props in ambiguous:
            ledger.record("syntax", "decision", "warning", rel, first_line.get(rel, 0),
                          "syntax error with several possible repairs: " + "; ".join(props[:3])[:200],
                          "the crawler will not guess", "pick the right one", "syntax")
        for rel, why in stuck:
            soft = rel.lower().endswith((".md", ".txt", ".mdx"))
            ledger.record("syntax", "found", "warning" if soft else "critical", rel, first_line.get(rel, 0),
                          "syntax error the crawler cannot repair: " + why,
                          "the file cannot be read by the program, so part of the product is probably broken" if not soft else "",
                          "repair the file by hand (exact line: syntax-report.json)", "syntax")
    except Exception:
        pass
    print("\n---------------- Auto-fix %s----------------" % ("(PREVIEW - nothing was changed) " if preview else ""))
    for rel, steps, old, new in fixed:
        print(f"  {'would fix' if preview else 'FIXED'}  {rel}")
        for st in steps:
            print(f"       - {st}")
    for rel, props in ambiguous:
        print(f"  NOT SURE   {rel}  - several repairs would work, so I changed nothing. Choices:")
        for pr in props:
            print(f"       ? {pr}")
    for rel, why in stuck:
        print(f"  needs you  {rel}  ({why})")
    print(f"\nRepaired {len(fixed)} file(s); {len(ambiguous)} unsure; {len(stuck)} need a human.")
    if fixed and not preview:
        print(f"Backup of the originals: {bdir}\nTo put everything back:  python syntaxcheck.py --undo")
    return fixed


# ----------------------------------------------------------------------------
# Orchestration
# ----------------------------------------------------------------------------
def walk(root):
    stack = [str(root)]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError:
            continue
        for e in entries:
            try:
                if e.is_dir(follow_symlinks=False):
                    if e.name not in SKIP_DIRS:
                        stack.append(e.path)
                    continue
                low = e.name.lower()
                if low in SKIP_FILES or low.startswith(".env") or low.endswith((".min.js", ".min.css", ".map")):
                    continue
                ext = low.rsplit(".", 1)[-1] if "." in low else ""
                if ext not in KINDS or e.stat().st_size > MAX_BYTES:
                    continue
            except OSError:
                continue
            yield Path(e.path), ext


def run(root):
    root = Path(root)
    files = list(walk(root))
    jobs = []  # (rel, ext, kind, text, line_offset)
    results = defaultdict(list)  # rel -> [(line, col, msg, kind)]
    texts = {}

    def load(t):
        p, ext = t
        try:
            with open(p, encoding="utf-8", newline="") as fh:
                return p, ext, fh.read()
        except (UnicodeDecodeError, OSError):
            return p, ext, None

    with ThreadPoolExecutor(max_workers=16) as ex:
        loaded = list(ex.map(load, files))

    js_jobs = []  # (rel, ext, text, offset)
    skipped = 0
    for p, ext, text in loaded:
        if text is None:
            skipped += 1
            continue
        rel = p.relative_to(root).as_posix()
        kind = KINDS[ext]
        texts[rel] = text
        if kind == "py":
            results[rel] += [(a, b, m, "python") for a, b, m in check_py(text, p)]
        elif kind in ("json", "jsonc"):
            results[rel] += [(a, b, m, "json") for a, b, m in check_json(text, kind == "jsonc")]
        elif kind == "js":
            js_jobs.append((rel, ext, text, 0))
        elif kind == "css":
            results[rel] += [(a, b, m, "css") for a, b, m in check_balance(text)]
        elif kind == "glsl":
            results[rel] += [(a, b, m, "shader") for a, b, m in check_balance(text)]
        elif kind == "sql":
            results[rel] += [(a, b, m, "sql") for a, b, m in
                             check_balance(text, multiline_strings=True, dash_comments=True, slash_comments=False)]
        elif kind == "md":
            results[rel] += [(a, b, m, "markdown") for a, b, m in check_md(text)]
        elif kind == "html":
            errs, emb = check_html(text)
            results[rel] += [(a, b, m, "html") for a, b, m in errs]
            for ek, first_line, code in emb:
                off = first_line - 1
                if ek == "js":
                    js_jobs.append((rel, "mjs", code, off))
                elif ek == "json":
                    results[rel] += [(a + off, b, m, "json in html") for a, b, m in check_json(code)]
                elif ek == "css":
                    results[rel] += [(a + off, b, m, "css in html") for a, b, m in check_balance(code)]

    engine = "none"
    if js_jobs:
        es = Esbuild(root)
        done = False
        if es.ok:
            try:
                out = es.run([(f"{r}#{i}", c, ESBUILD_LOADER.get(e, "jsx")) for i, (r, e, c, o) in enumerate(js_jobs)])
                for (r, e, c, o), errs in zip(js_jobs, out):
                    for ln, col, msg in errs:
                        results[r].append((ln + o, col, msg, "javascript" if e not in ("ts", "tsx") else "typescript"))
                engine = "esbuild"
                done = True
            except Exception as ex_:
                print("  (esbuild could not run: %s - using fallback)" % str(ex_).strip()[:120])
        if not done:
            node = shutil_which("node")

            def fb(j):
                r, e, c, o = j
                if node and e in ("js", "mjs", "cjs"):
                    try:
                        return j, node_check(c, node), "node"
                    except Exception:
                        pass
                return j, [], "skipped"   # a bracket count is not reliable for TS/JSX (regex literals, types): say nothing
            with ThreadPoolExecutor(max_workers=8) as ex:
                for (r, e, c, o), errs, how in ex.map(fb, js_jobs):
                    for ln, col, msg in errs:
                        results[r].append((ln + o, col, msg, "javascript"))
            engine = "node --check / bracket balance"

    findings = []
    for rel, errs in results.items():
        lines = texts[rel].splitlines()
        seen = set()
        for ln, col, msg, kind in sorted(errs):
            if (ln, msg) in seen:
                continue
            seen.add((ln, msg))
            findings.append(finding(rel, kind, ln, col, msg, lines))
    findings.sort(key=lambda f: (f["file"], f["line"]))
    return {"files": len(loaded) - skipped, "engine": engine, "findings": findings,
            "lines": sum(t.count("\n") + 1 for t in texts.values())}


def main():
    ap = argparse.ArgumentParser(description="Arachnid syntax checker: finds broken grammar in your code files.")
    ap.add_argument("root", nargs="?", default=str(HERE.parent), help="folder to check (default: the folder above arachnid/)")
    ap.add_argument("--out", default=str(HERE / "reports" / "syntax-report.json"))
    ap.add_argument("--fix", action="store_true", help="repair the mistakes it is sure about (backup first)")
    ap.add_argument("--preview", action="store_true", help="with --fix: only show what would change")
    ap.add_argument("--verify-cmd", default=None, help="command that proves the project still works, e.g. \"npm run build\"")
    ap.add_argument("--no-build", action="store_true", help="do not run the project's build while choosing between repairs")
    ap.add_argument("--undo", action="store_true", help="restore the files changed by the last repair")
    a = ap.parse_args()
    if a.undo:
        from spellcheck import undo
        undo()
        return 0
    root = Path(a.root).resolve()
    t0 = datetime.now()
    print("Arachnid syntax checker - looking at:", root)
    r = run(root)
    f = r["findings"]
    try:
        import ledger
        ledger.reset("syntax")
        if not a.fix:
            for x in f:
                ledger.record("syntax", "found", "warning", x["file"], x["line"], x["message"], "", "run: python syntaxcheck.py --fix --preview", "syntax")
    except Exception:
        pass
    for x in f[:60]:
        print(f"\n  {x['file']}  line {x['line']}, column {x['col']}   [{x['kind']}]")
        print(f"     problem : {x['message']}")
        if x["context"]:
            print(f"     the line: {x['context']}")
        print(f"     meaning : {x['explanation']}")
    if len(f) > 60:
        print(f"\n  ... and {len(f) - 60} more (all of them are in the report file)")
    print("\n---------------- Summary ----------------")
    print(f"Files checked: {r['files']}    Lines read: {r['lines']:,}    JS reader: {r['engine']}")
    per = Counter(x["kind"] for x in f)
    print(f"Syntax errors found: {len(f)}" + ("   (" + ", ".join(f"{k}: {v}" for k, v in per.most_common()) + ")" if f else ""))
    try:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps({
            "checked_at": datetime.now().isoformat(timespec="seconds"), "root": str(root), "files": r["files"],
            "engine": r["engine"], "errors": len(f), "findings": f}, indent=1), encoding="utf-8")
        print("Full report saved to:", a.out)
    except OSError as e:
        print("Could not save report:", e)
    if a.fix and f:
        fixed = apply_syntax_fixes(root, r, a.preview, a.verify_cmd, not a.no_build)
        if fixed and not a.preview:
            r2 = run(root)
            print(f"\nRe-check after repair: {len(r2['findings'])} syntax error(s) left (was {len(f)}).")
            f = r2["findings"]
    print(f"Took {(datetime.now() - t0).total_seconds():.1f}s")
    return 1 if f else 0


if __name__ == "__main__":
    sys.exit(main())
