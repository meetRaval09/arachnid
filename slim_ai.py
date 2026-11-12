#!/usr/bin/env python3
"""
ARACHNID - SLIM with an AI (Ponytail rules) - every answer is PROVEN or TESTED, never trusted
=============================================================================================
    python arachnid.py slim [folder] --ai

The AI gets one function at a time, the Ponytail rules (ai/ponytail, MIT, by DietrichGebert) and a strict contract:
same name, same inputs, the same behaviour for every input.  Whatever it answers, Arachnid then checks it itself:

  1. PROVEN   the new file is the same program (Python: same canonical program as slim.py; JS/TS: esbuild, names kept)
  2. TESTED   the old and the new function are run side by side on hundreds of inputs (from your own code, from the
              parameter names and types, and edge cases).  Results, result types, errors, printed output and changes
              made to the inputs must be identical - AND at least 90% of the lines of BOTH versions must have run
              (otherwise the test did not really look).  Only for functions that do not touch files, the network,
              the clock or randomness.
  3. otherwise the answer is thrown away.

A tested answer is offered as a proposal marked "tested, not proven"; like everything, it changes nothing until you
approve it, and the side-by-side test runs again right before the file is changed.

Which AI:
  ARACHNID_AI_CMD="claude -p"        any command that reads the question on stdin and prints the answer (any AI tool)
  ANTHROPIC_API_KEY=...              or the Anthropic API directly (model: ARACHNID_AI_MODEL, default claude-sonnet-5-5)
The key is read from the environment only - it is never stored, printed or written to a report.  Your code is sent
to that AI, which is why this only runs when you add --ai.
"""
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import proposals  # noqa: E402
import slim  # noqa: E402

PONYTAIL = HERE / "ai" / "ponytail" / "SKILL.md"
MIN_LINES = 5
MIN_COVERAGE = 0.9
MIN_NORMAL_CALLS = 12
IMPURE_MODULES = {"os", "sys", "subprocess", "socket", "requests", "urllib", "http", "shutil", "time", "random", "datetime",
                  "uuid", "secrets", "pathlib", "sqlite3", "smtplib", "asyncio", "threading", "multiprocessing", "logging",
                  "httpx", "aiohttp", "boto3", "psycopg2", "pymongo", "redis", "tempfile", "glob", "io"}
IMPURE_CALLS = {"open", "input", "exec", "eval", "__import__", "exit", "quit", "breakpoint", "globals", "locals", "vars", "id", "hash"}
SAFE_TOP_CALLS = {"compile", "getLogger", "namedtuple", "TypeVar", "frozenset", "set", "dict", "list", "tuple", "object", "field",
                  "Enum", "dataclass", "NewType", "IntEnum", "auto", "Decimal", "Path"}

CONTRACT = """You are rewriting ONE function so it is SHORTER, following the Ponytail rules below, under a strict contract:
- Keep the function's name, parameters, defaults, decorators and docstring exactly as they are.
- The new function must behave EXACTLY the same for EVERY possible input: the same return value and type, the same
  exceptions, the same printed output, the same changes to its arguments, the same calls to other code.
  Watch for: truthiness (`return x` is not `return bool(x)`), `or` vs a None check, float summation order
  (sum() of floats is NOT a loop of +=), evaluating something twice, early exits, mutation and aliasing.
- Never cut validation, error handling or security checks.
- A one-liner that needs decoding is not short: keep it readable.
- If you cannot make it shorter with certainty, answer exactly: UNCHANGED
Answer with ONLY the complete new function in one code block, nothing else."""


# ============================================================================================== the AI
def ask(prompt, timeout=180):
    cmd = os.environ.get("ARACHNID_AI_CMD")
    if cmd:
        r = subprocess.run(cmd, shell=True, input=prompt, capture_output=True, text=True, encoding="utf-8", timeout=timeout)
        if r.returncode != 0:
            raise RuntimeError("the AI command failed: " + (r.stderr or "")[-200:])
        return r.stdout
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("no AI is set up: set ARACHNID_AI_CMD (for example \"claude -p\") or ANTHROPIC_API_KEY")
    body = json.dumps({"model": os.environ.get("ARACHNID_AI_MODEL", "claude-sonnet-5-5"), "max_tokens": 4000,
                       "messages": [{"role": "user", "content": prompt}]}).encode("utf-8")
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body, method="POST",
                                 headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return "".join(part.get("text", "") for part in data.get("content", []))


def code_from(answer):
    if not answer or answer.strip() == "UNCHANGED" or "UNCHANGED" in answer.strip()[:20]:
        return None
    m = re.search(r"```[a-zA-Z]*\n(.*?)```", answer, re.S)
    return (m.group(1) if m else answer).strip("\n")


def rules_text():
    try:
        return PONYTAIL.read_text(encoding="utf-8")
    except OSError:
        return ""


# ============================================================================================== Python: what can be tested
def _module_safe_to_load(tree):
    """Loading the module must not DO anything (no server, no database, no files) - only define things."""
    for st in tree.body:
        if isinstance(st, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Pass)):
            continue
        if isinstance(st, ast.Expr) and isinstance(st.value, ast.Constant):
            continue
        if isinstance(st, ast.If) and "__name__" in ast.unparse(st.test):
            continue
        if isinstance(st, ast.Try) and all(isinstance(x, (ast.Import, ast.ImportFrom)) for x in st.body):
            continue
        if isinstance(st, (ast.Assign, ast.AnnAssign)):
            v = st.value
            calls = [n for n in ast.walk(v)] if v is not None else []
            if all(not isinstance(n, ast.Call) or (isinstance(n.func, (ast.Name, ast.Attribute)) and
                                                   (n.func.id if isinstance(n.func, ast.Name) else n.func.attr) in SAFE_TOP_CALLS)
                   for n in calls):
                continue
        return False
    return True


def _imported_names(tree):
    out = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                out[a.asname or a.name.split(".")[0]] = a.name.split(".")[0]
        elif isinstance(n, ast.ImportFrom) and n.module:
            for a in n.names:
                out[a.asname or a.name] = n.module.split(".")[0]
    return out


def _pure(fn, imported):
    """No files, network, clock, randomness, globals, generators or async - the same input always gives the same output."""
    if isinstance(fn, ast.AsyncFunctionDef):
        return False
    for n in ast.walk(fn):
        if isinstance(n, (ast.Global, ast.Nonlocal, ast.Yield, ast.YieldFrom, ast.Await)):
            return False
        if isinstance(n, ast.Name) and (n.id in IMPURE_CALLS or imported.get(n.id) in IMPURE_MODULES):
            return False
    return True


def python_candidates(root):
    """(rel, text, function node) for module-level functions long enough to shorten, in modules safe to load."""
    out = []
    for rel, p in slim.walk_files(root):
        if p.suffix != ".py":
            continue
        try:
            text = p.read_text(encoding="utf-8")
            tree = ast.parse(text)
        except (OSError, UnicodeDecodeError, SyntaxError, ValueError):
            continue
        if not _module_safe_to_load(tree):
            continue
        imported = _imported_names(tree)
        for fn in tree.body:
            if isinstance(fn, ast.FunctionDef) and fn.end_lineno - fn.lineno + 1 >= MIN_LINES + 1 and _pure(fn, imported) \
                    and not fn.args.vararg and not fn.args.kwarg and not fn.args.kwonlyargs and not fn.args.posonlyargs:
                out.append((rel, text, fn))
    out.sort(key=lambda c: -(c[2].end_lineno - c[2].lineno))
    return out


HARNESS = r'''
import ast, copy, contextlib, io, itertools, json, random, re, signal, sys, types
root, rel, old_src, new_src, fname, harvested = json.loads(sys.stdin.read())
sys.path.insert(0, root)
pkg = ".".join(rel[:-3].split("/")[:-1])
def load(src, tag):
    m = types.ModuleType("arachnid_" + tag)
    m.__package__ = pkg or None
    m.__file__ = root + "/" + rel
    sys.modules[m.__name__] = m
    exec(compile(src, root + "/" + rel, "exec"), m.__dict__)
    return m
old_m, new_m = load(old_src, "old"), load(new_src, "new")
f_old, f_new = getattr(old_m, fname), getattr(new_m, fname)
tree = ast.parse(old_src)
fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == fname)
params = [a.arg for a in fn.args.args]
ann = {a.arg: (ast.unparse(a.annotation) if a.annotation else "") for a in fn.args.args}
nd = len(fn.args.defaults)
defaults = {}
for a, d in zip(fn.args.args[len(params) - nd:], fn.args.defaults):
    try: defaults[a.arg] = ast.literal_eval(d)
    except Exception: pass
INT = [0, 1, -1, 2, 3, 7, 10, 18, 99, 100, 101, -5, 1000]
FLT = [0.0, 0.1, 0.5, 1.5, -2.25, 2.5, 100.0, 1e-9, 1e12]
STR = ["", "a", "abc", "Hello world", " x ", "123", "IN", "a,b,c", "Ä", "AEIOU", "xyz", "The quick brown fox", "aaaa",
       "Mississippi", "naïve café", "12 apples", "\n", "hello, world!", "Zoë", "  ", "UPPER lower", "e"]
LST = [[], [1], [1, 2, 3], [3, 1, 2], [0, -1, 5], [1.5, 2.5], [0.1, 0.2, 0.3], [1e16, 1.0, -1e16], ["a", "b", "a"], [1, 1, 2],
       [[1, 2], [3]], [None, 0], [5, 3, 5, 1], ["b", "a"], [2, 2, 2], [-3, -1, -2], [10, 20, 30, 40], [0], [True, False]]
DCT = [{}, {"a": 1}, {"a": 1, "b": 2}, {"x": [1, 2]}]
GEN = [0, 1, -1, 2, 0.5, "", "a", "abc", None, True, False, [], [1, 2, 3], ["a", "b"], {}, {"a": 1}, (1, 2)]
def pool(p):
    a, n = ann.get(p, "").lower(), p.lower()
    vals = list(harvested.get(p, []))
    if p in defaults: vals.append(defaults[p])
    if "int" in a: vals += INT
    elif "float" in a: vals += FLT + INT[:5]
    elif "str" in a: vals += STR
    elif "bool" in a: vals += [True, False]
    elif "list" in a or "sequence" in a or "iterable" in a: vals += LST
    elif "dict" in a or "mapping" in a: vals += DCT
    elif re.search(r"(s|list|items|values|rows|data|arr|seq)$", n) and not n.endswith("ss"): vals += LST
    elif re.search(r"(price|amount|qty|quantity|count|total|age|size|num|^n$|^i$|^k$|limit|max|min|index|year|days|level|score)", n): vals += INT + FLT[:5]
    elif re.search(r"(name|text|word|msg|message|label|title|code|key|country|email|url|path|^s$|str)", n): vals += STR
    elif n.endswith("s") or re.search(r"(list|items|values|rows|data|arr|seq)", n): vals += LST
    elif re.search(r"(dict|map|config|opts|options|params|kw)", n): vals += DCT
    else: vals += GEN
    seen, out = set(), []
    for v in vals:
        k = repr(v)
        if k not in seen: seen.add(k); out.append(v)
    return out
pools = [pool(p) for p in params]
combos = list(itertools.product(*pools)) if params else [()]
random.seed(1234)
if len(combos) > 600: combos = random.sample(combos, 600)
for k in range(len(params) - nd, len(params) + 1):
    if k < len(params): combos.append(tuple(c[0] for c in pools[:k]))     # calls that rely on the defaults
lines = {"old": set(), "new": set()}
codes = {f_old.__code__: "old", f_new.__code__: "new"}
def tracer(frame, event, arg):
    tag = codes.get(frame.f_code)
    if tag is None:
        c = frame.f_code
        for code, t in list(codes.items()):
            if c in code.co_consts: codes[c] = t; tag = t; break
    if tag is None: return None
    def local(fr, ev, a):
        if ev == "line": lines[tag].add(fr.f_lineno)
        return local
    lines[tag].add(frame.f_lineno)
    return local
A = lambda s: re.sub(r" at 0x[0-9a-fA-F]+", "", s)
class Timeout(Exception): pass
def alarm(*a): raise Timeout()
HAS_ALARM = hasattr(signal, "SIGALRM")          # not on Windows: there the whole test has one overall time limit
if HAS_ALARM: signal.signal(signal.SIGALRM, alarm)
def call(f, args):
    a = copy.deepcopy(args); buf = io.StringIO()
    sys.settrace(tracer)
    if HAS_ALARM: signal.setitimer(signal.ITIMER_REAL, 2)
    try:
        with contextlib.redirect_stdout(buf):
            try: r = f(*a); res = ("ok", A(repr(r)), type(r).__name__)
            except Timeout: res = ("timeout",)
            except RecursionError: res = ("raise", "RecursionError")
            except Exception as e: res = ("raise", type(e).__name__)
    finally:
        if HAS_ALARM: signal.setitimer(signal.ITIMER_REAL, 0)
        sys.settrace(None)
    return res, buf.getvalue(), A(repr(a))
diffs, normal, total = [], 0, 0
for c in combos:
    x = call(f_old, c)
    if x[0][0] == "timeout": continue
    y = call(f_new, c)
    total += 1
    if x[0][0] == "ok": normal += 1
    if x != y:
        diffs.append({"input": A(repr(c))[:200], "old": str(x)[:200], "new": str(y)[:200]})
        if len(diffs) >= 3: break
def body_lines(src):
    t = ast.parse(src)
    f = next(n for n in t.body if isinstance(n, ast.FunctionDef) and n.name == fname)
    out = set()
    for n in ast.walk(f):
        if isinstance(n, ast.stmt) and n is not f and not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)):
            out.add(n.lineno)
    return out
cov = {}
for tag, src in (("old", old_src), ("new", new_src)):
    want = body_lines(src)
    cov[tag] = (len(want & lines[tag]) / len(want)) if want else 1.0
print(json.dumps({"calls": total, "normal": normal, "diffs": diffs, "coverage": cov}))
'''


def harvest_calls(root, name):
    """Literal arguments the project itself passes to this function: the most realistic test inputs."""
    params = {}
    for rel, p in slim.walk_files(root):
        if p.suffix != ".py":
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and ((isinstance(n.func, ast.Name) and n.func.id == name) or
                                            (isinstance(n.func, ast.Attribute) and n.func.attr == name)):
                params.setdefault("__pos__", []).append(n.args)
    return params


def _harvest_for(fn, root):
    found = {}
    pos = harvest_calls(root, fn.name).get("__pos__", [])
    names = [a.arg for a in fn.args.args]
    for args in pos:
        for nm, a in zip(names, args):
            try:
                found.setdefault(nm, []).append(ast.literal_eval(a))
            except Exception:
                pass
    return found


def side_by_side(root, rel, old_src, new_src, fname, harvested):
    py = sys.executable
    r = subprocess.run([py, "-I", "-c", HARNESS], input=json.dumps([str(root), rel, old_src, new_src, fname, harvested]),
                       capture_output=True, text=True, encoding="utf-8", timeout=120, cwd=str(root))
    if r.returncode != 0:
        return None, "the side-by-side test could not run: " + (r.stderr or "")[-160:].strip()
    try:
        return json.loads(r.stdout.strip().splitlines()[-1]), ""
    except Exception:
        return None, "the side-by-side test gave no result"


def version_trap(old_fn, new_fn):
    """Code whose result depends on the Python version: a test on THIS Python cannot speak for the others."""
    def maybe_float_sums(f):
        n_ = 0
        for n in ast.walk(f):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "sum":
                a = n.args[0] if n.args else None
                whole = isinstance(a, (ast.GeneratorExp, ast.ListComp)) and isinstance(a.elt, ast.Constant) and type(a.elt.value) is int
                n_ += not whole                                  # sum(1 for ...) counts: the same on every version
        return n_
    if sys.version_info < (3, 12) and maybe_float_sums(new_fn) > maybe_float_sums(old_fn):
        return ("it adds sum(): from Python 3.12 on, sum() adds decimals more precisely than a loop, so a test on this "
                "Python %d.%d cannot show the difference - run Arachnid with Python 3.12 or newer to test it" % sys.version_info[:2])
    return ""


def judge(res):
    """(ok, evidence text)"""
    if res is None:
        return False, "not testable"
    if res["diffs"]:
        d = res["diffs"][0]
        return False, "DIFFERENT for input %s: before %s, after %s" % (d["input"], d["old"], d["new"])
    cov_o, cov_n = res["coverage"]["old"], res["coverage"]["new"]
    if res["normal"] < MIN_NORMAL_CALLS:
        return False, "only %d test calls returned normally - not enough evidence" % res["normal"]
    if cov_o < MIN_COVERAGE or cov_n < MIN_COVERAGE:
        return False, "the test only reached %d%% of the old and %d%% of the new lines - not enough evidence" % (cov_o * 100, cov_n * 100)
    return True, ("tested side by side on %d inputs with Python %d.%d: identical results, result types, errors, printed output "
                  "and argument changes; %d%% of the old and %d%% of the new lines ran" % (res["calls"], sys.version_info[0],
                                                                                      sys.version_info[1], cov_o * 100, cov_n * 100))


def _replace_function(text, fn, new_code):
    lines = text.splitlines(keepends=True)
    start = fn.lineno - 1 - len(fn.decorator_list) if fn.decorator_list else fn.lineno - 1
    if fn.decorator_list:
        start = min(d.lineno for d in fn.decorator_list) - 1
    end = fn.end_lineno
    indent = re.match(r"\s*", lines[start]).group(0)
    body = textwrap.indent(textwrap.dedent(new_code).rstrip("\n") + "\n", indent)
    at = sum(len(l) for l in lines[:start])
    old_t = "".join(lines[start:end])
    return at, old_t, body


def python_ai(root, limit, log):
    out, notes, stats = [], [], {"asked": 0, "proven": 0, "tested": 0, "rejected": 0, "unchanged": 0}
    rules = rules_text()
    for rel, text, fn in python_candidates(root)[:limit]:
        old_fn = ast.get_source_segment(text, fn)
        prompt = "%s\n\n=== PONYTAIL RULES (MIT, github.com/DietrichGebert/ponytail) ===\n%s\n\n=== THE FUNCTION (Python, file %s) ===\n```python\n%s\n```\n" % (
            CONTRACT, rules, rel, old_fn)
        stats["asked"] += 1
        try:
            new_code = code_from(ask(prompt))
        except Exception as e:
            notes.append("AI: %s" % str(e)[:200])
            break
        if not new_code:
            stats["unchanged"] += 1
            continue
        try:
            nt = ast.parse(textwrap.dedent(new_code))
        except SyntaxError:
            stats["rejected"] += 1
            continue
        nf = [n for n in nt.body if isinstance(n, ast.FunctionDef)]
        if len(nf) != 1 or nf[0].name != fn.name or ast.dump(nf[0].args) != ast.dump(fn.args) or len(nt.body) != 1 \
                or [ast.dump(d) for d in nf[0].decorator_list] != [ast.dump(d) for d in fn.decorator_list] \
                or ast.get_docstring(nf[0]) != ast.get_docstring(fn):
            stats["rejected"] += 1
            continue
        at, old_t, new_t = _replace_function(text, fn, new_code)
        if new_t.count("\n") >= old_t.count("\n"):
            stats["unchanged"] += 1
            continue
        new_text = text[:at] + new_t + text[at + len(old_t):]
        saved = old_t.count("\n") - new_t.count("\n")
        if slim.same_python_program(text, new_text):
            stats["proven"] += 1
            out.append(_ai_proposal(rel, fn, at, old_t, new_t, saved, "proven",
                                    "PROVEN: the new file is the same program (canonical program check)", "slim"))
            continue
        if not _pure(nf[0], _imported_names(ast.parse(text))):
            stats["rejected"] += 1
            continue
        risky = version_trap(fn, nf[0])
        if risky:
            stats["rejected"] += 1
            log("  AI answer for %s() in %s thrown away: %s" % (fn.name, rel, risky))
            continue
        res, err = side_by_side(root, rel, text, new_text, fn.name, _harvest_for(fn, root))
        ok, evidence = judge(res)
        if ok:
            stats["tested"] += 1
            out.append(_ai_proposal(rel, fn, at, old_t, new_t, saved, "tested", "TESTED, not proven: " + evidence, "slim-ai"))
        else:
            stats["rejected"] += 1
            log("  AI answer for %s() in %s thrown away: %s" % (fn.name, rel, err or evidence))
    return out, notes, stats


def _ai_proposal(rel, fn, at, old_t, new_t, saved, proof, evidence, verify):
    pid = proposals.make_id("slim", "ai-" + proof, rel, "%s|%s" % (old_t, new_t))
    return {"id": pid, "kind": "ai-shorter-" + proof, "file": rel, "line": fn.lineno,
            "title": "Shorter %s() suggested by the AI (Ponytail rules)" % fn.name,
            "benefit": "%d line%s shorter" % (saved, "" if saved == 1 else "s"),
            "risk": evidence if proof == "tested" else "none: " + evidence,
            "patches": [{"at": at, "old": old_t, "new": new_t}], "verify": verify, "saved": saved, "proof": proof,
            "function": fn.name}


# ============================================================================================== JavaScript / TypeScript
JS_FUNC = re.compile(r"(?m)^(export\s+)?(async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(")


def js_ai(root, limit, log):
    """JS / TS: only answers esbuild PROVES to be the same program are kept (no side-by-side test for browser code)."""
    out, notes, stats = [], [], {"asked": 0, "proven": 0, "rejected": 0, "unchanged": 0}
    try:
        from syntaxcheck import Esbuild
        es = Esbuild(root)
    except Exception:
        es = None
    if es is None or not es.ok:
        return out, ["AI for JavaScript/TypeScript: skipped - the proof needs esbuild (npm install esbuild in the Arachnid folder)"], stats
    rules = rules_text()
    cands = []
    for rel, p in slim.walk_files(root):
        if p.suffix not in slim.JS_EXT:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        code = slim._js_blank(text)
        if code is None:
            continue
        for m in JS_FUNC.finditer(code):
            brace = code.find("{", code.find(")", m.end()))
            close = slim._match_paren(code, brace, "{", "}") if brace > 0 else -1
            if close > 0 and text[m.start():close].count("\n") >= MIN_LINES:
                cands.append((rel, text, m.start(), close + 1, m.group(3)))
    cands.sort(key=lambda c: -(c[3] - c[2]))
    for rel, text, a, b, name in cands[:limit]:
        loader = slim.JS_EXT[Path(rel).suffix.lower()]
        prompt = "%s\n\n=== PONYTAIL RULES (MIT, github.com/DietrichGebert/ponytail) ===\n%s\n\n=== THE FUNCTION (%s, file %s) ===\n```\n%s\n```\n" % (
            CONTRACT, rules, "TypeScript" if loader.startswith("ts") else "JavaScript", rel, text[a:b])
        stats["asked"] += 1
        try:
            new_code = code_from(ask(prompt))
        except Exception as e:
            notes.append("AI: %s" % str(e)[:200])
            break
        if not new_code or new_code.count("\n") >= text[a:b].count("\n"):
            stats["unchanged"] += 1
            continue
        new_text = text[:a] + new_code.strip() + text[b:]
        try:
            x, y = es.minified([("old", text, loader), ("new", new_text, loader)], keep_names=True)
        except Exception:
            x = y = None
        if x is not None and x == y:
            stats["proven"] += 1
            saved = text[a:b].count("\n") - new_code.strip().count("\n")
            out.append({"id": proposals.make_id("slim", "ai-proven", rel, "%s|%s" % (text[a:b], new_code)), "kind": "ai-shorter-proven",
                        "file": rel, "line": text.count("\n", 0, a) + 1, "title": "Shorter %s() suggested by the AI (Ponytail rules)" % name,
                        "benefit": "%d line%s shorter" % (saved, "" if saved == 1 else "s"),
                        "risk": "none: PROVEN - esbuild reads the old and the new file as the same program",
                        "patches": [{"at": a, "old": text[a:b], "new": new_code.strip()}], "verify": "slim", "saved": saved, "proof": "proven",
                        "function": name})
        else:
            stats["rejected"] += 1
            log("  AI answer for %s() in %s thrown away: esbuild does not read it as the same program" % (name, rel))
    return out, notes, stats


def run(root, ai_limit=20, log=print):
    root = Path(root).resolve()
    print("  AI shortening is ON: functions are sent to %s." % ("the command in ARACHNID_AI_CMD" if os.environ.get("ARACHNID_AI_CMD")
                                                              else "the Anthropic API"))
    a, na, sa = python_ai(root, ai_limit, log)
    b, nb, sb = js_ai(root, max(0, ai_limit - sa["asked"]), log)
    notes = na + nb
    notes.append("AI: asked about %d function(s): %d proven, %d tested, %d thrown away, %d it could not shorten" % (
        sa["asked"] + sb["asked"], sa["proven"] + sb["proven"], sa.get("tested", 0), sa["rejected"] + sb["rejected"],
        sa["unchanged"] + sb["unchanged"]))
    return a + b, notes


def retest(root, p):
    """Right before an approved TESTED proposal is applied: run the side-by-side test again on the file as it is now."""
    root = Path(root)
    f = root / p["file"]
    text = f.read_text(encoding="utf-8")
    new_text = proposals.apply_patches(text, p["patches"])
    res, err = side_by_side(root, p["file"], text, new_text, p["function"], _harvest_for(
        next(n for n in ast.parse(text).body if isinstance(n, ast.FunctionDef) and n.name == p["function"]), root))
    return judge(res)


def other_proof_exists(root):
    """Does the project itself have pages, a build or tests the safety gate can use as well?"""
    import pixelcheck
    root = Path(root)
    return bool(pixelcheck.detect_build(root) or pixelcheck.detect_tests(root) or pixelcheck.pages_in(root, limit=1))
