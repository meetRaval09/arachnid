#!/usr/bin/env python3
"""
ARACHNID - SLIM: shorter code, the SAME product
===============================================
    python slim.py [folder]          find code that can be written shorter -> PROPOSALS (nothing changes)
    python slim.py [folder] --ai     also ask an AI (with the Ponytail rules) for shorter versions; every one is TESTED

AI assistants tend to write long code where short code does the same.  Slim finds it and offers the shorter
version - but only when it can PROVE the program stays exactly the same:

  Python      every rewrite is one of a small set of rules whose sameness is known (see RULES), and the new file
              must reduce to EXACTLY the same "canonical" program tree as the old file.  Anything else: not offered.
  JS / TS     the new file and the old file must compile (esbuild, names kept) to exactly the same program.

Nothing is ever changed by this tool.  You approve a proposal (python arachnid.py approve <id>), then
`python arachnid.py apply-approved` re-checks the proof and runs the safety gate before a file is touched.

What is never touched, on purpose:  code with a comment in it (your note stays), lines that would become hard to
read (a one-liner that needs decoding is not short - from Ponytail), test files, minified / vendor / built code.
"""
import argparse
import ast
import builtins
import io
import json
import os
import re
import sys
import tokenize
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ledger  # noqa: E402
import proposals  # noqa: E402

TOOL = "slim"
MAX_LINE = 99
SKIP_DIRS = {"node_modules", ".git", "backups", "reports", "dist", "build", "out", ".next", ".nuxt", "vendor", "venv", ".venv",
             "__pycache__", "coverage", ".cache", "site-packages", "arachnid", ".mypy_cache", ".pytest_cache", "env"}
TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|spec|e2e|fixtures?)(/|$)|(^|/)(test_[^/]*|[^/]*_test)\.py$|\.(test|spec)\.[jt]sx?$")
JS_EXT = {".js": "js", ".mjs": "js", ".cjs": "js", ".jsx": "jsx", ".ts": "ts", ".tsx": "tsx", ".mts": "ts", ".cts": "ts"}

RULES = {
    "bool-return": ("`if X: return True / else: return False` is just `return X`",
                    "the same True/False comes back for every value of X"),
    "ternary-assign": ("an if/else that only picks one of two values for the same name",
                       "`x = a if c else b` does exactly the same in one line"),
    "list-comprehension": ("an empty list filled by a loop that only appends",
                           "a list comprehension builds the same list, in the same order"),
    "any-all": ("a loop that only searches for a match and returns True/False",
                "any(...) / all(...) stop at the same item and return the same answer"),
    "return-variable": ("a value stored in a name only to return it on the next line",
                        "returning the value directly is the same"),
    "trailing-return": ("`return` / `return None` as the last line of a function",
                        "a function already returns None when it reaches its end"),
    "else-after-return": ("`else:` after a branch that always returns",
                          "the code after it only runs when the branch did not return, with or without the else"),
    "js-bool-return": ("an if/else that returns true or false", "returning the test itself is the same program"),
    "js-return-variable": ("a value stored in a name only to return it", "returning the value directly is the same program"),
    "js-else-after-return": ("`else` after a branch that always returns", "the same program without the extra block"),
    "js-arrow-body": ("an arrow function `=> { return x; }`", "`=> x` is the same program"),
    "js-null-check": ("`x === null || x === undefined`", "`x == null` is the same program"),
    "js-ternary-bool": ("`c ? true : false`", "`!!c` (or the comparison itself) is the same program"),
    "js-empty-else": ("an empty `else { }`", "the same program without it"),
}


def walk_files(root):
    root = Path(root)
    for dp, dn, fn in os.walk(root):
        dn[:] = sorted(d for d in dn if d not in SKIP_DIRS and not d.startswith("."))
        for f in sorted(fn):
            p = Path(dp) / f
            rel = p.relative_to(root).as_posix()
            if TEST_PATH.search(rel) or re.search(r"\.min\.|\.bundle\.|-bundle\.", f):
                continue
            if p.suffix == ".py" or p.suffix in JS_EXT:
                yield rel, p


# ============================================================================================== Python: the rules
class Scope:
    """What a rewrite needs to know about the place it happens in."""

    def __init__(self, node, module, in_class=False):
        self.node = node
        self.is_func = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        self.in_class = in_class
        self.names = Counter()
        for n in ast.walk(node):
            if isinstance(n, ast.Name):
                self.names[n.id] += 1
            elif isinstance(n, ast.arg):
                self.names[n.arg] += 1
            elif isinstance(n, (ast.Global, ast.Nonlocal)):
                for x in n.names:
                    self.names[x] += 100                  # never touch a name declared global / nonlocal
        self.unsafe = any(isinstance(n, ast.Name) and n.id in ("locals", "vars", "eval", "exec", "globals", "__import__")
                          for n in ast.walk(node))
        self.module = module
        self.local_stores = _stores(node) if self.is_func else set()


def _stores(node, top_only=False):
    """Names a piece of code defines itself (assignments, defs, imports, parameters, loop targets)."""
    out = set()
    nodes = []
    if top_only:                                    # the module's own top level - not inside functions or classes
        todo = list(node.body)
        while todo:
            n = todo.pop()
            nodes.append(n)
            if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                todo.extend(ast.iter_child_nodes(n))
            elif not isinstance(n, ast.Lambda):
                nodes.append(n)
    else:
        nodes = list(ast.walk(node))
    for n in nodes:
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            out.add(n.id)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(n.name)
        elif isinstance(n, ast.arg):
            out.add(n.arg)
        elif isinstance(n, ast.alias):
            out.add((n.asname or n.name).split(".")[0])
        elif isinstance(n, ast.ImportFrom) and any(a.name == "*" for a in n.names):
            out.add("*")
        elif isinstance(n, ast.Global):
            out.update(n.names)
    return out


def _shadowed(module):
    """Built-in names (bool, any, all) the MODULE redefines at its top level or declares global anywhere - a rewrite
    never relies on a built-in the code redefines.  Names a function defines for itself are checked per function."""
    out = _stores(module, top_only=True)
    for n in ast.walk(module):
        if isinstance(n, ast.Global):
            out.update(n.names)
    return out


def _count_in(node, name):
    return sum(1 for n in ast.walk(node) if isinstance(n, ast.Name) and n.id == name)


def _target_names(t):
    if isinstance(t, ast.Name):
        return [t.id]
    if isinstance(t, (ast.Tuple, ast.List)) and all(isinstance(e, ast.Name) for e in t.elts) and t.elts:
        return [e.id for e in t.elts]
    return None


def _plain(node):
    """No yield / await / := / lambda / nested function anywhere inside: their meaning depends on WHERE they run."""
    return not any(isinstance(n, (ast.Yield, ast.YieldFrom, ast.Await, ast.NamedExpr, ast.Lambda, ast.FunctionDef,
                                  ast.AsyncFunctionDef, ast.ClassDef)) for n in ast.walk(node))


def _const(node, value):
    return isinstance(node, ast.Constant) and node.value is value


def _is_bool_valued(node, shadow):
    """An expression that is ALWAYS exactly True or False (so it can be returned as it is)."""
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return True
    if isinstance(node, ast.Compare) and all(isinstance(o, (ast.Is, ast.IsNot, ast.In, ast.NotIn)) for o in node.ops):
        return True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("isinstance", "issubclass", "callable",
                                                                                          "hasattr", "bool") \
            and node.func.id not in shadow and not node.keywords:
        return True
    return isinstance(node, ast.Constant) and isinstance(node.value, bool)


def _ret(value):
    return ast.Return(value=value)


def _call(name, args):
    return ast.Call(func=ast.Name(id=name, ctx=ast.Load()), args=args, keywords=[])


def _terminal(stmts):
    return bool(stmts) and isinstance(stmts[-1], (ast.Return, ast.Raise, ast.Continue, ast.Break))


def match(stmts, i, scope, shadow, in_try):
    """Try every rule at stmts[i].  Returns (rule, count of statements replaced, [new statements], info) or None.
    The SAME function is used to find proposals and to build the canonical program that proves them."""
    s = stmts[i]
    nxt = stmts[i + 1] if i + 1 < len(stmts) else None
    shadow = shadow | scope.local_stores
    # ---- bool-return:  if C: return True  else: return False      (or: ... followed by return False)
    if isinstance(s, ast.If) and len(s.body) == 1 and isinstance(s.body[0], ast.Return) and scope.is_func:
        a = s.body[0].value
        b, used = None, 1
        if len(s.orelse) == 1 and isinstance(s.orelse[0], ast.Return):
            b = s.orelse[0].value
        elif not s.orelse and isinstance(nxt, ast.Return):
            b, used = nxt.value, 2
        if a is not None and b is not None and ((_const(a, True) and _const(b, False)) or (_const(a, False) and _const(b, True))):
            c = s.test
            if _const(a, True):
                if _is_bool_valued(c, shadow):
                    v = c
                elif "bool" not in shadow and "*" not in shadow:
                    v = _call("bool", [c])
                else:
                    v = None
            else:
                v = ast.UnaryOp(op=ast.Not(), operand=c)
            if v is not None:
                return "bool-return", used, [_ret(v)], {"value": v, "test": c, "negate": not _const(a, True)}
    # ---- ternary-assign:  if C: x = A  else: x = B
    if isinstance(s, ast.If) and len(s.body) == 1 and len(s.orelse) == 1 and all(
            isinstance(t, ast.Assign) and len(t.targets) == 1 and isinstance(t.targets[0], ast.Name) and t.type_comment is None
            for t in (s.body[0], s.orelse[0])) and s.body[0].targets[0].id == s.orelse[0].targets[0].id:
        A, B = s.body[0].value, s.orelse[0].value
        if not any(isinstance(n, (ast.Yield, ast.YieldFrom, ast.Starred, ast.Lambda)) for e in (A, B, s.test) for n in ast.walk(e)):
            x = s.body[0].targets[0].id
            new = ast.Assign(targets=[ast.Name(id=x, ctx=ast.Store())], value=ast.IfExp(test=s.test, body=A, orelse=B))
            return "ternary-assign", 1, [new], {"name": x, "test": s.test, "a": A, "b": B}
    # ---- list-comprehension:  x = []  /  for v in it: [if F:] x.append(E)
    if isinstance(s, ast.Assign) and len(s.targets) == 1 and isinstance(s.targets[0], ast.Name) and isinstance(s.value, ast.List) \
            and not s.value.elts and isinstance(nxt, ast.For) and not nxt.orelse and len(nxt.body) == 1 \
            and not scope.in_class and not in_try and not scope.unsafe:
        x = s.targets[0].id
        body, cond = nxt.body[0], None
        if isinstance(body, ast.If) and len(body.body) == 1 and not body.orelse:
            cond, body = body.test, body.body[0]
        names = _target_names(nxt.target)
        if names and isinstance(body, ast.Expr) and isinstance(body.value, ast.Call) and isinstance(body.value.func, ast.Attribute) \
                and isinstance(body.value.func.value, ast.Name) and body.value.func.value.id == x and body.value.func.attr == "append" \
                and len(body.value.args) == 1 and not body.value.keywords and not isinstance(body.value.args[0], ast.Starred):
            E = body.value.args[0]
            parts = [E, nxt.iter] + ([cond] if cond is not None else [])
            if all(_plain(p) for p in parts) and all(_count_in(p, x) == 0 for p in parts) and x not in names \
                    and all(scope.names[n] == _count_in(nxt, n) for n in names) and scope.names[x] < 100:
                comp = ast.comprehension(target=nxt.target, iter=nxt.iter, ifs=[cond] if cond is not None else [], is_async=0)
                new = ast.Assign(targets=[ast.Name(id=x, ctx=ast.Store())], value=ast.ListComp(elt=E, generators=[comp]))
                return "list-comprehension", 2, [new], {"name": x, "elt": E, "target": nxt.target, "iter": nxt.iter, "cond": cond}
    # ---- any-all:  for v in it: if C: return True  /  return False
    if isinstance(s, ast.For) and scope.is_func and not nxt is None and isinstance(nxt, ast.Return) and not s.orelse \
            and len(s.body) == 1 and isinstance(s.body[0], ast.If) and not s.body[0].orelse and len(s.body[0].body) == 1 \
            and isinstance(s.body[0].body[0], ast.Return) and not scope.unsafe:
        inner, final, C = s.body[0].body[0].value, nxt.value, s.body[0].test
        names = _target_names(s.target)
        if names and inner is not None and final is not None and _plain(C) and _plain(s.iter) \
                and all(scope.names[n] == _count_in(s, n) for n in names) \
                and not ({"any", "all", "*"} & shadow):
            gen = lambda e: ast.GeneratorExp(elt=e, generators=[ast.comprehension(target=s.target, iter=s.iter, ifs=[], is_async=0)])
            v = None
            if _const(inner, True) and _const(final, False):
                v, how = _call("any", [gen(C)]), "any"
            elif _const(inner, False) and _const(final, True):
                if isinstance(C, ast.UnaryOp) and isinstance(C.op, ast.Not):
                    v, how = _call("all", [gen(C.operand)]), "all"
                else:
                    v, how = ast.UnaryOp(op=ast.Not(), operand=_call("any", [gen(C)])), "not any"
            if v is not None:
                return "any-all", 2, [_ret(v)], {"how": how, "test": C, "target": s.target, "iter": s.iter}
    # ---- return-variable:  x = E  /  return x
    if isinstance(s, ast.Assign) and len(s.targets) == 1 and isinstance(s.targets[0], ast.Name) and s.type_comment is None \
            and isinstance(nxt, ast.Return) and isinstance(nxt.value, ast.Name) and nxt.value.id == s.targets[0].id \
            and scope.is_func and not scope.unsafe and scope.names[s.targets[0].id] == 2 \
            and not any(isinstance(n, (ast.Yield, ast.YieldFrom)) for n in ast.walk(s.value)):
        return "return-variable", 2, [_ret(s.value)], {"name": s.targets[0].id, "value": s.value}
    # ---- else-after-return
    if isinstance(s, ast.If) and s.orelse and _terminal(s.body):
        new_if = ast.If(test=s.test, body=s.body, orelse=[])
        return "else-after-return", 1, [new_if] + list(s.orelse), {"node": s}
    return None


def _visit_lists(node, scope, shadow, in_try, out):
    """Every statement list inside this scope (not inside nested functions/classes), with whether it sits in a try."""
    for field in ("body", "orelse", "finalbody"):
        lst = getattr(node, field, None)
        if isinstance(lst, list) and lst and isinstance(lst[0], ast.stmt):
            # inside try, or inside with (a context manager can SWALLOW an error - contextlib.suppress): code after an
            # error that was swallowed sees whatever was half-done, so rewrites that change half-done state are off there
            t = in_try or (isinstance(node, (ast.Try, getattr(ast, "TryStar", ast.Try))) and field in ("body", "orelse", "finalbody")) \
                or isinstance(node, (ast.With, ast.AsyncWith))
            out.append((node, field, scope, t))
            for st in lst:
                if not isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    _visit_lists(st, scope, shadow, t, out)
    for h in getattr(node, "handlers", []) or []:
        out.append((h, "body", scope, True))
        for st in h.body:
            if not isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                _visit_lists(st, scope, shadow, True, out)
    for c in getattr(node, "cases", []) or []:
        out.append((c, "body", scope, in_try))
        for st in c.body:
            _visit_lists(st, scope, shadow, in_try, out)


def scopes_of(module):
    """(scope node, Scope) for the module, every function and every class body."""
    res = [(module, Scope(module, module))]
    for n in ast.walk(module):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            res.append((n, Scope(n, module)))
        elif isinstance(n, ast.ClassDef):
            res.append((n, Scope(n, module, in_class=True)))
    return res


def statement_lists(module):
    shadow = _shadowed(module)
    out = []
    for node, scope in scopes_of(module):
        _visit_lists(node, scope, shadow, False, out)
    return out, shadow


# ---- trailing return (needs the function, not a statement list)
def trailing_return(fn):
    if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and len(fn.body) >= 2 and isinstance(fn.body[-1], ast.Return) \
            and (fn.body[-1].value is None or _const(fn.body[-1].value, None)):
        return fn.body[-1]
    return None


def _canonical_tree(tree, shadow_fixed=None):
    """Apply every rule until nothing changes.  shadow_fixed: use these redefined names (from the OLD file) instead of
    recomputing them - fewer rewrites, never a wrong one."""
    for _ in range(8):
        changed = False
        for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            if trailing_return(fn) is not None:
                fn.body.pop()
                changed = True
        lists, shadow = statement_lists(tree)
        if shadow_fixed is not None:
            shadow = shadow_fixed
        for node, field, scope, in_try in lists:
            stmts = getattr(node, field)
            i = 0
            while i < len(stmts):
                m = match(stmts, i, scope, shadow, in_try)
                if m:
                    rule, used, new, _ = m
                    stmts[i:i + used] = new
                    changed = True
                    scope.__init__(scope.node, tree, scope.in_class)       # counts changed: look again
                    if rule == "else-after-return":
                        i += 1
                    continue
                i += 1
        if not changed:
            break
        tree = ast.parse(ast.unparse(ast.fix_missing_locations(tree)))   # fresh tree: no shared nodes between rewrites
    return ast.dump(tree)


def canonical(text):
    """The program tree with every rule applied until nothing changes.  Two files with the same canonical tree are
    the same program (each rule only ever replaces code by code that does exactly the same)."""
    return _canonical_tree(ast.parse(text))


_CANON = {}


def _canon_def(node_dump_key, node, shadow):
    key = (node_dump_key, tuple(sorted(shadow)))
    if key not in _CANON:
        if len(_CANON) > 2000:
            _CANON.clear()
        _CANON[key] = _canonical_tree(ast.Module(body=[node], type_ignores=[]), shadow)
    return _CANON[key]


def _shell(n):
    """A compound statement without its blocks (to see that only the blocks differ)."""
    if isinstance(n, ast.If):
        return ast.If(test=n.test, body=[], orelse=[])
    if isinstance(n, ast.With):
        return ast.With(items=n.items, body=[])
    return ast.Try(body=[], handlers=[], orelse=[], finalbody=[])


def _class_head(c):
    """A class without its body (works on every Python 3 version: type_params only exists from 3.12)."""
    return "|".join([c.name] + [ast.dump(b) for b in c.bases] + [ast.dump(k) for k in c.keywords]
                                                     + [ast.dump(d) for d in c.decorator_list]
                                                     + [ast.dump(t) for t in getattr(c, "type_params", [])])


def _same_stmt(x, y, shadow):
    """One top-level statement (or class member) of the old file against the new file."""
    dx, dy = ast.dump(x), ast.dump(y)
    if dx == dy:
        return True
    if type(x) is type(y) and isinstance(x, (ast.If, ast.Try, ast.With)) and ast.dump(_shell(x)) == ast.dump(_shell(y)):
        res = []
        for field in ("body", "orelse", "finalbody"):
            xa, ya = getattr(x, field, []), getattr(y, field, [])
            if len(xa) != len(ya):
                return None
            res += [_same_stmt(a, b, shadow) for a, b in zip(xa, ya)]
        if [ast.dump(h.type) if h.type else "" for h in getattr(x, "handlers", [])] != [ast.dump(h.type) if h.type else "" for h in getattr(y, "handlers", [])]:
            return None
        for hx, hy in zip(getattr(x, "handlers", []), getattr(y, "handlers", [])):
            if len(hx.body) != len(hy.body):
                return None
            res += [_same_stmt(a, b, shadow) for a, b in zip(hx.body, hy.body)]
        return False if False in res else (True if all(r is True for r in res) else None)
    if type(x) is not type(y) or not isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) or x.name != y.name:
        return None                                       # not comparable piece by piece
    if isinstance(x, ast.ClassDef) and len(x.body) == len(y.body) and _class_head(x) == _class_head(y):
        res = [_same_stmt(a, b, shadow) for a, b in zip(x.body, y.body)]
        if all(r is True for r in res):
            return True
        if False in res:
            return False
    return _canon_def(dx, x, shadow) == _canon_def(dy, y, shadow)


def same_python_program(old, new):
    """True when the new file is provably the same program as the old one.  Fast path: every top-level statement is
    identical except some functions / classes, and each of those reduces to the same canonical program (a function's
    rules only look inside the function, plus the names the module redefines - taken from the OLD file for both)."""
    try:
        a, b = ast.parse(old), ast.parse(new)
        if len(a.body) == len(b.body):
            shadow = _shadowed(a)
            res = [_same_stmt(x, y, shadow) for x, y in zip(a.body, b.body)]
            if all(r is True for r in res):
                return True
            if False in res:
                return False
        return _canonical_tree(a) == _canonical_tree(b)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return False


# ============================================================================================== Python: writing it
class Src:
    def __init__(self, text):
        self.text = text
        self.lines = text.splitlines(keepends=True)
        self.starts = [0]
        for ln in self.lines:
            self.starts.append(self.starts[-1] + len(ln))
        self.comment_lines = set()
        self.multiline_strings = []
        try:
            for tok in tokenize.generate_tokens(io.StringIO(text).readline):
                if tok.type == tokenize.COMMENT:
                    self.comment_lines.add(tok.start[0])
                elif tok.type == tokenize.STRING and tok.end[0] > tok.start[0]:
                    self.multiline_strings.append((tok.start[0], tok.end[0]))
        except (tokenize.TokenError, IndentationError, SyntaxError):
            pass

    def pos(self, line, col_bytes):
        ln = self.lines[line - 1] if line - 1 < len(self.lines) else ""
        return self.starts[line - 1] + len(ln.encode("utf-8")[:col_bytes].decode("utf-8", "replace"))

    def seg(self, node):
        return self.text[self.pos(node.lineno, node.col_offset):self.pos(node.end_lineno, node.end_col_offset)]

    def has_comment(self, a, b):
        return any(a <= ln <= b for ln in self.comment_lines)

    def has_multiline_string(self, a, b):
        return any(not (e < a or s > b) for s, e in self.multiline_strings)


def _wrap(src, node, ctx):
    """Source of a sub-expression, in brackets when needed to keep its meaning in its new place."""
    t = src.seg(node)
    already = t.startswith("(") and t.endswith(")")
    need = "\n" in t and not already
    if ctx == "operand-not":
        need = need or isinstance(node, (ast.BoolOp, ast.IfExp, ast.Lambda, ast.NamedExpr, ast.Tuple, ast.Compare, ast.BinOp))
    elif ctx in ("ifexp-part", "comp-cond", "comp-iter"):
        need = need or isinstance(node, (ast.IfExp, ast.Lambda, ast.NamedExpr)) or (isinstance(node, ast.Tuple) and not already)
    elif ctx == "ifexp-else":
        need = need or isinstance(node, (ast.Lambda, ast.NamedExpr)) or (isinstance(node, ast.Tuple) and not already)
    elif ctx == "elt":
        need = need or isinstance(node, (ast.Lambda, ast.NamedExpr)) or (isinstance(node, ast.Tuple) and not already)
    elif ctx == "return":
        need = need
    return "(%s)" % t if need and not already else t


def _python_text(src, rule, info):
    if rule == "bool-return":
        c = info["test"]
        if info["negate"]:
            return "return not " + _wrap(src, c, "operand-not")
        if isinstance(info["value"], ast.Call) and info["value"] is not c:
            return "return bool(%s)" % src.seg(c)
        return "return " + _wrap(src, c, "return")
    if rule == "ternary-assign":
        return "%s = %s if %s else %s" % (info["name"], _wrap(src, info["a"], "ifexp-part"), _wrap(src, info["test"], "ifexp-part"),
                                          _wrap(src, info["b"], "ifexp-else"))
    if rule == "list-comprehension":
        cond = (" if " + _wrap(src, info["cond"], "comp-cond")) if info["cond"] is not None else ""
        return "%s = [%s for %s in %s%s]" % (info["name"], _wrap(src, info["elt"], "elt"), src.seg(info["target"]),
                                             _wrap(src, info["iter"], "comp-iter"), cond)
    if rule == "any-all":
        c = info["test"]
        inner = c.operand if info["how"] == "all" else c
        g = "%s for %s in %s" % (_wrap(src, inner, "elt"), src.seg(info["target"]), _wrap(src, info["iter"], "comp-iter"))
        return {"any": "return any(%s)", "all": "return all(%s)", "not any": "return not any(%s)"}[info["how"]] % g
    if rule == "return-variable":
        return "return " + _wrap(src, info["value"], "return")
    return None


def python_proposals(rel, text):
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return []
    src = Src(text)
    if "\t" in text:
        return []                                   # mixed indentation: leave it alone
    out, used_lines = [], set()

    def free(a, b):
        return not any(ln in used_lines for ln in range(a, b + 1))

    lists, shadow = statement_lists(tree)
    module_shadow = _shadowed(tree)
    tops = []
    for t in tree.body:
        first = min([t.lineno] + [d.lineno for d in getattr(t, "decorator_list", [])])
        tops.append((first, t.end_lineno, t))

    def proven(a_line, b_line, at, old_t, new_t):
        """Is the file with this one edit the same program?  Only the top-level function / class holding the edit
        can differ, so only that piece is compared (read on its own, it is the same code as inside the file)."""
        for first, last, t in tops:
            if first <= a_line and b_line <= last and isinstance(t, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.If, ast.Try, ast.With)):
                s0, s1 = src.starts[first - 1], src.starts[last] if last < len(src.starts) else len(text)
                if not (s0 <= at and at + len(old_t) <= s1):
                    break
                piece_old = text[s0:s1]
                piece_new = piece_old[:at - s0] + new_t + piece_old[at - s0 + len(old_t):]
                try:
                    x, y = ast.parse(piece_old).body, ast.parse(piece_new).body
                except SyntaxError:
                    return False
                r = _same_stmt(x[0], y[0], module_shadow) if len(x) == len(y) == 1 else False
                if r is not None:
                    return r is True
                break
        return same_python_program(text, text[:at] + new_t + text[at + len(old_t):])

    for node, field, scope, in_try in lists:
        stmts = getattr(node, field)
        for i in range(len(stmts)):
            m = match(stmts, i, scope, shadow, in_try)
            if not m:
                continue
            rule, used, new, info = m
            first, last = stmts[i], stmts[i + used - 1]
            a, b = first.lineno, last.end_lineno
            if not free(a, b) or src.has_comment(a, b):
                continue
            indent = src.lines[a - 1][:len(src.lines[a - 1]) - len(src.lines[a - 1].lstrip())]
            start = src.pos(first.lineno, first.col_offset)
            if src.text[src.starts[a - 1]:start].strip():
                continue                                # something else on the same line before it (a; b)
            end = src.pos(last.end_lineno, last.end_col_offset)
            rest = src.text[end:src.starts[b] if b < len(src.starts) else len(src.text)]
            if rest.strip() and not rest.strip().startswith("#"):
                continue
            if rule == "else-after-return":
                patch = _else_after_return_patch(src, info["node"])
                if not patch:
                    continue
                at, old_t, new_t = patch
                saved = 1
            else:
                line = _python_text(src, rule, info)
                if line is None or "\n" in line or len(indent) + len(line) > MAX_LINE:
                    continue
                at, old_t, new_t = start, text[start:end], line
                saved = (b - a + 1) - 1
            if not proven(a, b, at, old_t, new_t):
                continue                                 # the proof did not hold: never offered
            used_lines.update(range(a, b + 1))
            out.append(_proposal(rel, a, rule, at, old_t, new_t, saved))
    for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        r = trailing_return(fn)
        if r is None or not free(r.lineno, r.end_lineno) or src.has_comment(r.lineno, r.end_lineno):
            continue
        a = src.starts[r.lineno - 1]
        b = src.starts[r.end_lineno] if r.end_lineno < len(src.starts) else len(text)
        if text[a:src.pos(r.lineno, r.col_offset)].strip() or text[src.pos(r.end_lineno, r.end_col_offset):b].strip():
            continue
        if proven(r.lineno, r.end_lineno, a, text[a:b], ""):
            used_lines.update(range(r.lineno, r.end_lineno + 1))
            out.append(_proposal(rel, r.lineno, "trailing-return", a, text[a:b], "", 1))
    return out


def _else_after_return_patch(src, node):
    """Remove the `else:` line and move its block one level left - only when that is a pure re-indent."""
    first = node.orelse[0]
    a, b = first.lineno, node.orelse[-1].end_lineno
    if_line = src.lines[node.lineno - 1]
    if_indent = len(if_line) - len(if_line.lstrip())
    else_ln = None
    for ln in range(a - 1, node.body[-1].end_lineno, -1):
        t = src.lines[ln - 1]
        if t.strip() == "":
            continue
        if t.strip() == "else:" and len(t) - len(t.lstrip()) == if_indent:
            else_ln = ln
        break
    if else_ln is None or src.has_multiline_string(a, b) or src.has_comment(else_ln, else_ln) or b - a + 1 > 8:
        return None                                      # moving a long block to save one line is not worth a review
    body_indent = len(src.lines[a - 1]) - len(src.lines[a - 1].lstrip())
    delta = body_indent - if_indent
    if delta <= 0:
        return None
    new_lines = []
    for ln in range(a, b + 1):
        t = src.lines[ln - 1]
        if t.strip() == "":
            new_lines.append(t)
            continue
        if not t.startswith(" " * delta):
            return None
        new_lines.append(t[delta:])
    at = src.starts[else_ln - 1]
    old_t = src.text[at:src.starts[b] if b < len(src.starts) else len(src.text)]
    new_t = "".join(src.lines[else_ln:a - 1]) + "".join(new_lines)
    return at, old_t, new_t


def group(found, text_of, at_least=4):
    """Many proposals of one kind in one file become ONE proposal (one approval) - if the whole set is still proven."""
    out, by = [], {}
    for f in found:
        by.setdefault((f["file"], f["kind"]), []).append(f)
    for (rel, kind), items in by.items():
        if len(items) < at_least or items[0].get("verify") != "slim" or kind.startswith("ai-"):
            out += items
            continue
        text = text_of(rel)
        patches = [pt for f in items for pt in f["patches"]]
        try:
            new_text = proposals.apply_patches(text, patches)
        except ValueError:
            out += items
            continue
        if not verify(rel, text, new_text):
            out += items
            continue
        saved = sum(f.get("saved", 0) for f in items)
        title, why = RULES[kind]
        out.append({"id": proposals.make_id(TOOL, kind + "-group", rel, "|".join("%d:%s" % (p["at"], p["new"]) for p in patches)),
                    "kind": kind, "file": rel, "line": min(f["line"] for f in items),
                    "title": "Shorter, same program (%d places in this file): %s" % (len(items), title),
                    "benefit": "%d lines shorter - %s" % (saved, why), "risk": items[0]["risk"], "patches": patches,
                    "verify": "slim", "saved": saved, "proof": "proven", "lines": sorted(f["line"] for f in items)})
    return out


def _proposal(rel, line, rule, at, old_t, new_t, saved):
    title, why = RULES[rule]
    pid = proposals.make_id(TOOL, rule, rel, "%s|%s" % (old_t, new_t))
    return {"id": pid, "kind": rule, "file": rel, "line": line,
            "title": "Shorter, same program: %s" % title,
            "benefit": "%d line%s shorter - %s" % (saved, "" if saved == 1 else "s", why) if saved else "shorter - " + why,
            "risk": "none: Arachnid proved the new code is the same program (and the safety gate checks it again)",
            "patches": [{"at": at, "old": old_t, "new": new_t}], "verify": "slim", "saved": saved, "proof": "proven"}


# ============================================================================================== JavaScript / TypeScript
def _js_blank(text):
    try:
        import bugcheck
        return bugcheck.js_lex(text)[0]
    except Exception:
        return None


def _match_paren(code, i, open_ch="(", close_ch=")"):
    depth = 0
    for j in range(i, len(code)):
        if code[j] == open_ch:
            depth += 1
        elif code[j] == close_ch:
            depth -= 1
            if depth == 0:
                return j
    return -1


def _top_level_ops(code):
    """Operators at the top level of an expression (outside brackets)."""
    depth, out = 0, []
    i = 0
    while i < len(code):
        ch = code[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif depth == 0:
            for op in ("===", "!==", "==", "!=", "<=", ">=", "&&", "||", "??", "instanceof", " in ", "<", ">", "?", ","):
                if code.startswith(op, i):
                    out.append(op.strip())
                    i += len(op) - 1
                    break
        i += 1
    return out


def _js_is_bool(cond_blank):
    c = cond_blank.strip()
    ops = _top_level_ops(c)
    if any(o in ("&&", "||", "??", "?", ",") for o in ops):
        return False
    if any(o in ("===", "!==", "==", "!=", "<=", ">=", "<", ">", "instanceof", "in") for o in ops):
        return True
    return c.startswith("!") and not ops


def _simple_chain(c):
    return re.fullmatch(r"[A-Za-z_$][\w$]*(\.[A-Za-z_$][\w$]*|\[\d+\])*", c) is not None


def _bang2(c):
    return "!!" + c if _simple_chain(c) else "!!(%s)" % c


def _negate(c, cb):
    """The opposite test, written the way a person would (esbuild still has to prove it is the same program)."""
    ops = _top_level_ops(cb)
    flip = {"===": "!==", "!==": "===", "==": "!=", "!=": "=="}
    if ops and all(o in flip for o in ops) and len(ops) == 1:
        i = next(k for k in range(len(cb)) if any(cb.startswith(o, k) for o in flip) and _top_level_ops(cb[:k]) == [])
        op = next(o for o in ("===", "!==", "==", "!=") if cb.startswith(o, i))
        return c[:i].rstrip() + " " + flip[op] + " " + c[i + len(op):].lstrip()
    if ops and set(ops) <= set(flip) | {"||"} and "||" in ops:
        parts, depth, last = [], 0, 0
        for k, ch in enumerate(cb):
            depth += ch in "([{"
            depth -= ch in ")]}"
            if depth == 0 and cb.startswith("||", k):
                parts.append((last, k))
                last = k + 2
        parts.append((last, len(cb)))
        out = []
        for x, y in parts:
            sub_c, sub_b = c[x:y].strip(), cb[x:y].strip()
            if len(_top_level_ops(sub_b)) != 1 or _top_level_ops(sub_b)[0] not in flip:
                return "!(%s)" % c
            out.append(_negate(sub_c, sub_b))
        return " && ".join(out)
    if c.startswith("!") and _simple_chain(c[1:]):
        return "!!" + c[1:]
    return "!" + c if _simple_chain(c) else "!(%s)" % c


def _has_comment(text, code, a, b):
    """A comment inside text[a:b] (js_lex blanks comments but keeps quotes, so a // or /* the code view lost is one)."""
    for m in re.finditer(r"//|/\*", text[a:b]):
        k = a + m.start()
        if code[k] == " ":
            return True
    return False


def js_candidates(text):
    """(rule, start, end, new text) - candidates only; esbuild decides which ones are really the same program."""
    code = _js_blank(text)
    if code is None or len(code) != len(text):
        return []
    out = []
    W = r"\s*"
    # if (C) { return true; } else { return false; }   |   if (C) return true; return false;
    for m in re.finditer(r"\bif\s*\(", code):
        open_p = m.end() - 1
        close_p = _match_paren(code, open_p)
        if close_p < 0:
            continue
        cond_b, cond = code[open_p + 1:close_p], text[open_p + 1:close_p]
        rest = code[close_p + 1:]
        r = re.match(W + r"\{?" + W + r"return\s+(true|false)\s*;?" + W + r"\}?" + W + r"(?:else\s*\{?" + W + r"return\s+(true|false)\s*;?" + W + r"\}?|return\s+(true|false)\s*;)", rest)
        if r and not re.search(r"\belse\s*$", code[:m.start()]):
            a, b = r.group(1), r.group(2) or r.group(3)
            if {a, b} == {"true", "false"} and "\n" not in cond.strip():
                end = close_p + 1 + r.end()
                if a == "true":
                    new = "return %s;" % (cond.strip() if _js_is_bool(cond_b) else _bang2(cond.strip()))
                else:
                    new = "return %s;" % _negate(cond.strip(), cond_b.strip())
                out.append(("js-bool-return", m.start(), end, new))
    # const x = E; return x;
    for m in re.finditer(r"\b(const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*", code):
        name = m.group(2)
        semi = m.end()
        depth = 0
        while semi < len(code):
            ch = code[semi]
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
                if depth < 0:
                    break
            elif ch == ";" and depth == 0:
                break
            semi += 1
        if semi >= len(code) or code[semi] != ";":
            continue
        r = re.match(r"\s*return\s+%s\s*;" % re.escape(name), code[semi + 1:])
        if r and len(re.findall(r"(?<![\w$.])%s(?![\w$])" % re.escape(name), code)) == 2:
            expr = text[m.end():semi].strip()
            if "\n" not in expr:
                out.append(("js-return-variable", m.start(), semi + 1 + r.end(), "return %s;" % expr))
    # => { return E; }
    for m in re.finditer(r"=>\s*\{\s*return\s+", code):
        close = _match_paren(code, code.index("{", m.start()), "{", "}")
        if close < 0:
            continue
        inner_b = code[m.end():close].rstrip()
        if not inner_b.endswith(";") or ";" in inner_b[:-1] or "\n" in text[m.end():close].strip():
            continue
        expr = text[m.end():m.end() + len(inner_b) - 1].strip()
        new = "=> " + ("(%s)" % expr if expr.startswith("{") else expr)
        out.append(("js-arrow-body", m.start(), close + 1, new))
    # x === null || x === undefined   (either order)  /  !== ... &&
    for m in re.finditer(r"([A-Za-z_$][\w$.]*)\s*(===|!==)\s*(null|undefined)\s*(\|\||&&)\s*\1\s*(===|!==)\s*(null|undefined)", code):
        if {m.group(3), m.group(6)} == {"null", "undefined"} and m.group(2) == m.group(5) and \
                ((m.group(2) == "===" and m.group(4) == "||") or (m.group(2) == "!==" and m.group(4) == "&&")):
            out.append(("js-null-check", m.start(), m.end(), "%s %s null" % (m.group(1), "==" if m.group(2) == "===" else "!=")))
    # c ? true : false
    for m in re.finditer(r"\?\s*(true|false)\s*:\s*(true|false)\b", code):
        if {m.group(1), m.group(2)} != {"true", "false"}:
            continue
        st = m.start()
        j, depth = st - 1, 0
        while j >= 0:
            ch = code[j]
            if ch in ")]":
                depth += 1
            elif ch in "([":
                if depth == 0:
                    break
                depth -= 1
            elif depth == 0 and ch in ";,\n{}:?":
                break
            elif depth == 0 and ch == "=" and code[j - 1:j] not in ("=", "!", "<", ">") and code[j + 1:j + 2] != "=":
                break
            j -= 1
        cs = j + 1
        r = re.match(r"\s*return\b", code[cs:st])
        if r:
            cs += r.end()
        cs += len(code[cs:st]) - len(code[cs:st].lstrip())
        cond, cond_b = text[cs:st].strip(), code[cs:st]
        if not cond or "\n" in cond:
            continue
        if m.group(1) == "true":
            new = cond if _js_is_bool(cond_b) else "!!(%s)" % cond
        else:
            new = "!(%s)" % cond
        out.append(("js-ternary-bool", cs, m.end(), new))
    # else { }
    for m in re.finditer(r"\s*else\s*\{\s*\}", code):
        out.append(("js-empty-else", m.start(), m.end(), ""))
    # } else { BODY }  after a block that ends with return - remove the else and its braces, BODY one level left
    for m in re.finditer(r"\}\s*else\s*\{", code):
        blk_open = code.rfind("{", 0, m.start())
        close = _match_paren(code, m.end() - 1, "{", "}")
        if close < 0:
            continue
        prev_block = code[:m.start()].rstrip()
        if not re.search(r"\breturn\b[^;{}]*;?\s*$", prev_block):
            continue
        body = text[m.end():close]
        lines = body.split("\n")
        if len(lines) < 3:
            continue
        ind = [len(l) - len(l.lstrip(" ")) for l in lines[1:-1] if l.strip()]
        outer = len(lines[-1]) - len(lines[-1].lstrip(" "))
        if not ind or min(ind) <= outer:
            continue
        delta = min(ind) - outer
        if lines[0].strip():
            continue
        moved = "\n".join([l[delta:] if l.strip() else l for l in lines[1:-1]])
        out.append(("js-else-after-return", m.start(), close + 1, "}\n" + moved))
    return out


def js_proposals(root, files):
    """files: [(rel, text)].  Every candidate is applied alone and kept only if esbuild proves it is the same program."""
    try:
        from syntaxcheck import Esbuild
        es = Esbuild(root)
    except Exception:
        es = None
    if es is None or not es.ok:
        return [], "JavaScript/TypeScript: skipped - the proof needs esbuild (run  npm install esbuild  in the Arachnid folder, or use the Docker image); without a proof nothing is offered"
    jobs = []
    for rel, text in files:
        loader = JS_EXT[Path(rel).suffix.lower()]
        cands = js_candidates(text)
        code = _js_blank(text) or text
        seen = set()
        for rule, a, b, new in cands:
            if _has_comment(text, code, a, b):
                continue                                  # your note stays
            if any(not (b <= x or a >= y) for x, y in seen):
                continue
            new_text = text[:a] + new + text[b:]
            jobs.append((rel, text, loader, rule, a, b, new, new_text))
            seen.add((a, b))
    if not jobs:
        return [], ""
    base = {}
    for rel, text, loader, *_ in jobs:
        base.setdefault(rel, (text, loader))
    try:
        ref = dict(zip(base, es.minified([(r + ":old", t, l) for r, (t, l) in base.items()], keep_names=True)))
        got = es.minified([(j[0] + ":%d" % k, j[7], j[2]) for k, j in enumerate(jobs)], keep_names=True)
    except Exception as e:
        return [], "JavaScript/TypeScript: esbuild could not run (%s) - nothing offered" % str(e)[:80]
    out = []
    for j, g in zip(jobs, got):
        rel, text, loader, rule, a, b, new, new_text = j
        if g is None or ref.get(rel) is None or g != ref[rel]:
            continue
        saved = text[a:b].count("\n") - new.count("\n")
        out.append(_proposal(rel, text.count("\n", 0, a) + 1, rule, a, text[a:b], new, max(saved, 0)))
    return out, ""


def verify(rel, old, new, root=None):
    """The proof, checked again right before a file is changed (apply-approved)."""
    ext = Path(rel).suffix.lower()
    if ext == ".py":
        return same_python_program(old, new)
    if ext in JS_EXT:
        try:
            from syntaxcheck import Esbuild
            es = Esbuild(root or HERE)
            if not es.ok:
                return False
            a, b = es.minified([("old", old, JS_EXT[ext]), ("new", new, JS_EXT[ext])], keep_names=True)
            return a is not None and a == b
        except Exception:
            return False
    return False


# ============================================================================================== bloat that needs a human
IDEAS = {
    "slim-wrapper": ("a function that only passes its inputs to another function",
                     "it adds a name and a jump, not behaviour; callers could call the inner function directly",
                     "call the inner function directly (check nothing else relies on the wrapper's name)"),
    "slim-dataclass": ("a class whose __init__ only stores its inputs",
                       "@dataclass writes this __init__ for you (and adds a readable print) - not identical, so a human decides",
                       "consider @dataclass"),
    "slim-json-clone": ("JSON.parse(JSON.stringify(x)) used to copy an object",
                        "structuredClone(x) is shorter and keeps dates, maps and sets - NOT identical, so a human decides",
                        "consider structuredClone(x)"),
    "slim-lookup-chain": ("a long if/elif chain that only maps one value to another",
                          "a dictionary lookup says the same in a few lines", "consider a dict"),
}


def python_ideas(rel, text):
    out = []
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return out
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and not n.decorator_list and len(n.body) == 1 \
                and isinstance(n.body[0], ast.Return) and isinstance(n.body[0].value, ast.Call):
            c = n.body[0].value
            params = [a.arg for a in n.args.args]
            if params and not n.args.defaults and not n.args.vararg and not n.args.kwarg and not c.keywords and \
                    [a.id if isinstance(a, ast.Name) else None for a in c.args] == params and isinstance(c.func, (ast.Name, ast.Attribute)):
                out.append((n.lineno, "slim-wrapper", "%s() only calls %s()" % (n.name, ast.unparse(c.func))))
        if isinstance(n, ast.ClassDef) and not n.bases and not n.decorator_list and len(n.body) == 1 \
                and isinstance(n.body[0], ast.FunctionDef) and n.body[0].name == "__init__":
            init = n.body[0]
            params = [a.arg for a in init.args.args[1:]]
            if len(params) >= 2 and len(init.body) == len(params) and all(
                    isinstance(s, ast.Assign) and len(s.targets) == 1 and isinstance(s.targets[0], ast.Attribute)
                    and isinstance(s.targets[0].value, ast.Name) and s.targets[0].value.id == "self"
                    and isinstance(s.value, ast.Name) and s.targets[0].attr == s.value.id for s in init.body):
                out.append((n.lineno, "slim-dataclass", "class %s" % n.name))
        if isinstance(n, ast.If):
            chain, cur, var = 0, n, None
            while isinstance(cur, ast.If) and isinstance(cur.test, ast.Compare) and len(cur.test.ops) == 1 \
                    and isinstance(cur.test.ops[0], ast.Eq) and isinstance(cur.test.comparators[0], ast.Constant) \
                    and len(cur.body) == 1 and isinstance(cur.body[0], ast.Return) and isinstance(cur.body[0].value, ast.Constant):
                v = ast.unparse(cur.test.left)
                if var is None:
                    var = v
                elif v != var:
                    break
                chain += 1
                cur = cur.orelse[0] if len(cur.orelse) == 1 and isinstance(cur.orelse[0], ast.If) else None
            if chain >= 4:
                out.append((n.lineno, "slim-lookup-chain", "%d branches on %s" % (chain, var)))
    seen, res = set(), []
    for ln, kind, d in out:
        if (ln, kind) not in seen:
            seen.add((ln, kind))
            res.append((ln, kind, d))
    return res


def js_ideas(rel, text):
    out = []
    for m in re.finditer(r"JSON\.parse\(\s*JSON\.stringify\(", text):
        out.append((text.count("\n", 0, m.start()) + 1, "slim-json-clone", "JSON.parse(JSON.stringify(...))"))
    return out


# ============================================================================================== run
def scan(root, use_ai=False, ai_limit=20, log=print):
    root = Path(root).resolve()
    found, ideas, notes, js_files = [], [], [], []
    for rel, p in walk_files(root):
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if len(text) > 400_000 or (text.count("\n") < 3 and len(text) > 2000):
            continue                                   # generated / minified
        if not os.access(p, os.W_OK):
            continue
        if p.suffix == ".py":
            found += python_proposals(rel, text)
            ideas += [(rel,) + i for i in python_ideas(rel, text)]
        else:
            js_files.append((rel, text))
            ideas += [(rel,) + i for i in js_ideas(rel, text)]
    js_found, note = js_proposals(root, js_files)
    found += js_found
    found = group(found, lambda rel: (root / rel).read_text(encoding="utf-8"))
    if note:
        notes.append(note)
    if use_ai:
        try:
            import slim_ai
            ai_found, ai_notes = slim_ai.run(root, ai_limit=ai_limit, log=log)
            taken = [(f["file"], pt["at"], pt["at"] + len(pt["old"])) for f in found for pt in f["patches"]]
            for f in ai_found:                 # a rule-based proposal for the same lines wins
                pt = f["patches"][0]
                if not any(r == f["file"] and not (pt["at"] + len(pt["old"]) <= a or pt["at"] >= b) for r, a, b in taken):
                    found.append(f)
            notes += [n for n in ai_notes if not (n.startswith("AI for JavaScript") and note)]
        except Exception as e:
            notes.append("AI shortening stopped: %s" % str(e)[:200])
    return found, ideas, notes


def main():
    ap = argparse.ArgumentParser(description="Arachnid slim: shorter code, the same product (proposals only)")
    ap.add_argument("root", nargs="?", default=str(HERE.parent))
    ap.add_argument("--ai", action="store_true", help="also ask an AI (Ponytail rules) - every answer is proven or tested")
    ap.add_argument("--ai-limit", type=int, default=20)
    ap.add_argument("--out", default=str(HERE / "reports" / "slim-report.json"))
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    print("Arachnid slim - looking for code that can be shorter, in:", Path(a.root).resolve())
    found, ideas, notes = scan(a.root, a.ai, a.ai_limit)
    proposals.sync(TOOL, found, Path(a.root).resolve())
    ledger.reset(TOOL)
    for f in found:
        print("\n  %s  line %d   [PROPOSAL %s, %s]  %s" % (f["file"], f["line"], f["id"], f.get("proof", "proven").upper(), f["kind"]))
        print("     %s\n     %s" % (f["title"], f["benefit"]))
        if f.get("proof") == "tested":
            print("     " + f["risk"])
        ledger.record(TOOL, "idea", "info", f["file"], f["line"], f["title"] + " (" + f["benefit"] + ")", f["risk"],
                      "approve %s, then apply-approved" % f["id"], f["kind"])
    for rel, ln, kind, d in ideas:
        title, why, todo = IDEAS[kind]
        print("\n  %s  line %d   [IDEA]  %s: %s" % (rel, ln, title, d))
        ledger.record(TOOL, "idea", "info", rel, ln, "%s: %s" % (title, d), why, todo, kind)
    for n in notes:
        print("\n  " + n)
    saved = sum(f.get("saved", 0) for f in found)
    print("\n---------------- Summary ----------------")
    tested = sum(1 for f in found if f.get("proof") == "tested")
    print("Shorter versions: %d (%d PROVEN the same program, %d TESTED side by side), about %d line%s shorter    Ideas for a human: %d" % (
        len(found), len(found) - tested, tested, saved, "" if saved == 1 else "s", len(ideas)))
    if found:
        print("Nothing has changed.  Look:  python arachnid.py proposals    Say yes:  python arachnid.py approve <id>")
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps({"root": str(Path(a.root).resolve()), "proposals": found, "ideas": ideas, "notes": notes}, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
