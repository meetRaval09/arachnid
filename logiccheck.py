#!/usr/bin/env python3
"""
ARACHNID - Step 6: LOGIC CHECKER  (unused imports, unused variables, "ambiguity" bugs)
=====================================================================================
    python logiccheck.py [folder]                 look only: list everything it found
    python logiccheck.py [folder] --fix           remove what is provably useless (always through the safety gate)
    python logiccheck.py [folder] --fix --level medium     also the "probably useless" ones
    python logiccheck.py --undo                   put the last logic fix back

What it looks for, in plain words
  UNUSED IMPORT      you bring a tool into the file but never use it.
  UNUSED VARIABLE    you put a value in a box (a variable) and never look at it again.
  AMBIGUITY BUG      a place where a word means two different things, so the code does something other than
                     what it looks like it does (two functions with the same name, a variable named `list`
                     that hides Python's own `list`, a dictionary with the same key twice ...).
                     These are ONLY REPORTED - fixing them would change behaviour, so the owner decides.

Safety
  * Python is read with Python's own parser (ast), so nothing is guessed from text patterns.
  * An import is removed automatically only when loading that module cannot do anything (the standard library,
    or one of your own files that only defines things).  Anything else is "review" - listed, never touched.
  * A variable is removed only when the value put in it cannot do anything either (a number, a text, a list ...).
    A line like  result = save_to_database()  keeps working: it needs --level medium and then only the
    `result =` part is dropped, the call stays.
  * Files that use eval / exec / globals() / locals() are never changed (they can reach names in hidden ways).
  * Every change goes through the safety gate first.  If the gate has nothing to run (no web pages, no build, no
    tests) the changes are HELD BACK - unless the owner explicitly adds --accept-analysis, which allows only the
    provably-harmless kinds above.
  * Backup before every write; --undo puts everything back.
"""
import argparse
import ast
import builtins
import json
import os
import re
import shutil
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

SKIP_DIRS = {"node_modules", ".git", "dist", "build", "__pycache__", ".venv", "venv", "env", "site-packages",
             ".next", ".cache", "arachnid", "backups", "reports", "coverage", ".mypy_cache", ".pytest_cache",
             ".tox", ".wrangler", ".vite"}
PY_EXT = {".py"}
JS_EXT = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"}
MAX_BYTES = 1_500_000
STDLIB = set(getattr(sys, "stdlib_module_names", ()))
LOUD_STDLIB = {"antigravity", "this", "readline", "rlcompleter", "site", "sitecustomize", "usercustomize",
               "__hello__", "__phello__", "idlelib", "turtledemo"}
DYNAMIC_CALLS = {"eval", "exec", "globals", "locals", "vars", "dir", "__import__"}
SAFE_DECORATORS = {"staticmethod", "classmethod", "property"}
BUILTIN_NAMES = {n for n in dir(builtins) if n[0].islower() and not n.startswith("_")} - {"copyright", "credits", "license", "exit", "quit"}

KIND_TEXT = {
    "unused-import": "You bring a tool into the file but never use it. Like packing a tool you never take out of the bag.",
    "duplicate-import": "The same thing is brought in twice. The second one does nothing.",
    "unused-variable": "A value is put into a box (a variable) and the box is never opened again.",
    "unused-exception-name": "`except Problem as e` gives the error a name, but the name is never used.",
    "unused-with-name": "`with ... as x` gives something a name, but the name is never used.",
    "duplicate-definition": "Two functions/classes have the SAME NAME in the same place. The second silently replaces the first, so the first can never run.",
    "duplicate-dict-key": "A dictionary has the same key twice. Only the last one counts; the other is silently thrown away.",
    "shadowed-builtin": "A variable has the same name as one of Python's own tools (like `list` or `type`). From then on the name means YOUR value, not Python's tool.",
    "import-overwritten": "A name that was imported is later used for something else, so the import is hidden.",
    "mutable-default": "A function's default value is a list/dictionary. Python creates it ONCE and shares it between all calls, so values pile up between calls.",
    "is-literal": "`is` checks 'the very same object', not 'equal'. With text or numbers the answer can be wrong. Usually `==` was meant.",
    "nested-loop-same-var": "A loop inside a loop uses the same counter name, so the inner loop overwrites the outer one's counter.",
    "duplicate-condition": "Two branches of one if/elif chain test exactly the same thing, so the second branch can never run.",
    "useless-comparison": "A line that only compares (x == 1) and throws the answer away. Usually `=` (put a value in) was meant.",
    "assert-tuple": "assert (condition, 'message') is ALWAYS true, because a non-empty pair is true. The check never checks anything.",
    }
LEVEL_TEXT = {"high": "safe to fix", "medium": "probably safe", "review": "your decision", "ambiguity": "your decision"}


# ----------------------------------------------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------------------------------------------
class Src:
    """Turns (line, column-in-utf8-bytes) from the parser into character offsets, and handles line endings."""

    def __init__(self, text):
        self.text = text
        self.lines = re.findall(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+", text)
        self.starts, o = [], 0
        for ln in self.lines:
            self.starts.append(o)
            o += len(ln)

    def off(self, lineno, col):
        line = self.lines[lineno - 1]
        return self.starts[lineno - 1] + len(line.encode("utf-8")[:col].decode("utf-8", "ignore"))

    def line_end(self, lineno):
        return self.starts[lineno - 1] + len(self.lines[lineno - 1])

    def eol(self, lineno):
        m = re.search(r"(\r\n|\r|\n)$", self.lines[lineno - 1])
        return m.group(1) if m else "\n"


def own_nodes(scope, skip_comprehensions=False):
    """Nodes that belong to this scope itself (not to functions/classes defined inside it)."""
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        n = stack.pop()
        yield n
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        if skip_comprehensions and isinstance(n, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            continue
        stack.extend(ast.iter_child_nodes(n))


def pure_expr(n, allow_attr=False):
    """True when evaluating this expression cannot DO anything (no calls, no hidden method calls)."""
    if isinstance(n, ast.Constant):
        return True
    if isinstance(n, ast.Name):
        return True
    if allow_attr and isinstance(n, ast.Attribute):
        return pure_expr(n.value, True)
    if isinstance(n, (ast.List, ast.Tuple, ast.Set)):
        return all(pure_expr(e, allow_attr) for e in n.elts)
    if isinstance(n, ast.Dict):
        return all(k is not None and pure_expr(k, allow_attr) for k in n.keys) and all(pure_expr(v, allow_attr) for v in n.values)
    if isinstance(n, ast.UnaryOp) and isinstance(n.operand, ast.Constant):
        return True
    if isinstance(n, ast.BinOp) and isinstance(n.left, ast.Constant) and isinstance(n.right, ast.Constant):
        return True
    if isinstance(n, ast.Lambda):
        return all(pure_expr(d, allow_attr) for d in n.args.defaults)
    return False


def annotation_names(tree):
    """Names that appear inside string annotations like  x: "Foo"  /  List["Foo"]."""
    found = set()
    anns = []
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            anns += [a.annotation for a in n.args.args + n.args.kwonlyargs + n.args.posonlyargs if a.annotation]
            for a in (n.args.vararg, n.args.kwarg):
                if a is not None and a.annotation:
                    anns.append(a.annotation)
            if n.returns:
                anns.append(n.returns)
        elif isinstance(n, ast.AnnAssign):
            anns.append(n.annotation)
    for a in anns:
        for c in ast.walk(a):
            if isinstance(c, ast.Constant) and isinstance(c.value, str) and len(c.value) < 300:
                try:
                    for m in ast.walk(ast.parse(c.value.strip(), mode="eval")):
                        if isinstance(m, ast.Name):
                            found.add(m.id)
                except SyntaxError:
                    pass
    return found


def dunder_all(tree):
    out = set()
    for n in tree.body:
        if isinstance(n, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = n.targets if isinstance(n, ast.Assign) else [n.target]
            if any(isinstance(t, ast.Name) and t.id == "__all__" for t in targets) and n.value is not None:
                for c in ast.walk(n.value):
                    if isinstance(c, ast.Constant) and isinstance(c.value, str):
                        out.add(c.value)
    return out


# ----------------------------------------------------------------------------------------------------------------
# project context (all Python files, who re-exports what, which local modules are "pure")
# ----------------------------------------------------------------------------------------------------------------
class Project:
    def __init__(self, root):
        self.root = Path(root)
        self.py, self.js = {}, {}
        self.trees = {}
        self.skipped = []
        for p in self._walk():
            rel = p.relative_to(self.root).as_posix()
            ext = p.suffix.lower()
            if ext not in PY_EXT and ext not in JS_EXT:
                continue
            try:
                if p.stat().st_size > MAX_BYTES:
                    continue
                with open(p, encoding="utf-8", newline="") as fh:
                    text = fh.read()
            except (OSError, UnicodeDecodeError):
                continue
            (self.py if ext in PY_EXT else self.js)[rel] = text
        for rel, text in self.py.items():
            try:
                self.trees[rel] = ast.parse(text)
            except (SyntaxError, ValueError, RecursionError):
                self.skipped.append(rel)
        self.files = set(self.py)
        self.from_pairs, self.star_from, self.attr_pairs = set(), set(), set()
        for rel, tree in self.trees.items():
            for n in ast.walk(tree):
                if isinstance(n, ast.ImportFrom):
                    last = (n.module or "").split(".")[-1]
                    for a in n.names:
                        if a.name == "*":
                            self.star_from.add(last)
                        else:
                            self.from_pairs.add((last, a.name))
                            if not n.module:                       # from . import foo   (foo is a module)
                                self.from_pairs.add((a.name, "*module*"))
                elif isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name):
                    self.attr_pairs.add((n.value.id, n.attr))
        self._pure = {}

    def _walk(self):
        stack = [self.root]
        while stack:
            d = stack.pop()
            try:
                entries = list(d.iterdir())
            except OSError:
                continue
            for e in entries:
                if e.is_dir():
                    if e.name not in SKIP_DIRS and not e.is_symlink():
                        stack.append(e)
                else:
                    yield e

    # -- re-exports: is `name` of file `rel` used by some OTHER file?
    def reexported(self, rel, name):
        stem = Path(rel).stem if Path(rel).name != "__init__.py" else Path(rel).parent.name
        return stem in self.star_from or (stem, name) in self.from_pairs or (stem, name) in self.attr_pairs

    # -- local module lookup
    def resolve(self, rel, module, level):
        parts = module.split(".") if module else []
        here = Path(rel).parent
        bases = []
        if level:
            b = here
            for _ in range(level - 1):
                b = b.parent
            bases.append(b)
        else:
            bases += [Path("."), here]
        for b in bases:
            c = b.joinpath(*parts) if parts else b
            cands = ([c.with_name(c.name + ".py")] if parts else []) + [c / "__init__.py"]
            for cand in cands:
                key = cand.as_posix()
                if key.startswith("./"):
                    key = key[2:]
                if key in self.files:
                    return key
        return None

    # -- does importing this local file DO anything besides defining names?
    def pure_module(self, rel, depth=0):
        if rel in self._pure:
            return self._pure[rel]
        self._pure[rel] = True                                     # cycles: assume yes while checking
        ok = depth < 6 and rel in self.trees and self._pure_body(rel, self.trees[rel], depth)
        self._pure[rel] = ok
        return ok

    def _pure_body(self, rel, tree, depth):
        for n in tree.body:
            if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant):
                continue
            if isinstance(n, ast.Pass):
                continue
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not self._pure_def(n):
                    return False
                continue
            if isinstance(n, ast.ClassDef):
                if n.keywords or any(not isinstance(b, (ast.Name, ast.Attribute)) for b in n.bases) or not self._pure_decos(n):
                    return False
                for m in n.body:
                    if isinstance(m, ast.Pass) or (isinstance(m, ast.Expr) and isinstance(m.value, ast.Constant)):
                        continue
                    if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) and self._pure_def(m):
                        continue
                    if isinstance(m, (ast.Assign, ast.AnnAssign)) and (m.value is None or pure_expr(m.value, True)):
                        continue
                    return False
                continue
            if isinstance(n, (ast.Assign, ast.AnnAssign)):
                if n.value is None or pure_expr(n.value, True):
                    continue
                return False
            if isinstance(n, ast.If) and isinstance(n.test, ast.Compare) and isinstance(n.test.left, ast.Name) \
                    and n.test.left.id == "__name__":
                continue
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                if self.import_is_quiet(rel, n, depth + 1):
                    continue
                return False
            return False
        return True

    def _pure_decos(self, n):
        return all(isinstance(d, ast.Name) and d.id in SAFE_DECORATORS for d in n.decorator_list)

    def _pure_def(self, n):
        a = n.args
        defaults = list(a.defaults) + [d for d in a.kw_defaults if d is not None]
        return self._pure_decos(n) and all(pure_expr(d, True) for d in defaults)

    def import_is_quiet(self, rel, node, depth=0):
        """Loading the module(s) of this import statement cannot do anything."""
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            return True
        mods = []                                              # (dotted name, level, must it exist as a module?)
        if isinstance(node, ast.Import):
            for a in node.names:
                parts = a.name.split(".")
                mods += [(".".join(parts[:i]), 0, True) for i in range(1, len(parts) + 1)]
        else:
            mods.append((node.module or "", node.level, bool(node.module) and node.level == 0))
            for a in node.names:
                if a.name != "*":
                    mods.append((((node.module + ".") if node.module else "") + a.name, node.level, False))
        for m, level, must in mods:
            local = self.resolve(rel, m, level) if (m or level) else None
            if local:
                if not self.pure_module(local, depth):
                    return False
                continue
            if not must:
                continue                                       # it is a name inside the module, not a file of its own
            top = m.split(".")[0]
            if top in STDLIB and top not in LOUD_STDLIB:
                continue
            return False                                       # outside library, or one that prints/starts things
        return True


# ----------------------------------------------------------------------------------------------------------------
# Python analysis of one file
# ----------------------------------------------------------------------------------------------------------------
class PyFile:
    def __init__(self, proj, rel, level_ok, third_party):
        self.proj, self.rel = proj, rel
        self.text = proj.py[rel]
        self.tree = proj.trees[rel]
        self.src = Src(self.text)
        self.level_ok = level_ok
        self.third_party = third_party
        self.findings = []
        self.plans = []                                        # edit groups: {"edits": [(s, e, repl)], "ids": [finding ids]}
        self.parents = {}
        for n in ast.walk(self.tree):
            for c in ast.iter_child_nodes(n):
                self.parents[c] = n
        # things that can reach module-level names in hidden ways (a function's own locals() cannot)
        def reaches_module(n):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in ("eval", "exec", "globals", "__import__"):
                return True
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in ("vars", "dir") and n.args:
                return True
            return isinstance(n, ast.Attribute) and n.attr == "modules" and isinstance(n.value, ast.Name) and n.value.id == "sys"
        self.file_dynamic = any(reaches_module(n) for n in ast.walk(self.tree))

    # -- finding bookkeeping
    def add(self, node, kind, name, level, extra="", fix=None, provable=False):
        line = getattr(node, "lineno", 1)
        fid = len(self.findings)
        f = {"file": self.rel, "line": line, "col": getattr(node, "col_offset", 0), "kind": kind, "name": name,
             "level": level, "detail": extra, "code": self.src.lines[line - 1].strip()[:140] if line - 1 < len(self.src.lines) else "",
             "will_fix": False, "provable": provable, "lang": "python"}
        self.findings.append(f)
        return fid

    def comment_blocks(self, node):
        """True if the first line of node carries a noqa / type comment (the owner asked us not to touch it)."""
        line = self.src.lines[node.lineno - 1] if node.lineno - 1 < len(self.src.lines) else ""
        return bool(re.search(r"#.*\b(noqa|type:|pragma|pylint|isort)", line))

    # -- edits
    def remove_statement(self, node, removed_ids):
        s = self.src
        first, last = node.lineno, node.end_lineno
        before = s.text[s.starts[first - 1]:s.off(first, node.col_offset)]
        after = s.text[s.off(last, node.end_col_offset):s.line_end(last)]
        if before.strip() or (after.strip() and not after.strip().startswith("#")) or ";" in after:
            return None
        parent = self.parents.get(node)
        need_pass = False
        if parent is not None and not isinstance(parent, ast.Module):
            for field in ("body", "orelse", "finalbody"):
                lst = getattr(parent, field, None)
                if isinstance(lst, list) and node in lst:
                    if all(id(x) in removed_ids for x in lst) and lst[-1] is node:
                        need_pass = True
                    break
        a, b = s.starts[first - 1], s.line_end(last)
        if need_pass:
            return (a, b, before + "pass" + s.eol(last))
        return (a, b, "")

    # -- run everything
    def run(self):
        self.imports()
        self.variables()
        self.ambiguity()
        return self

    # ---------------- imports
    def imports(self):
        t = self.tree
        is_init = Path(self.rel).name == "__init__.py"
        all_names = dunder_all(t)
        ann_names = annotation_names(t)
        loaded_module = set()
        for n in ast.walk(t):
            if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Load, ast.Del)):
                loaded_module.add(n.id)
            elif isinstance(n, ast.AugAssign) and isinstance(n.target, ast.Name):
                loaded_module.add(n.target.id)
            elif isinstance(n, (ast.Global, ast.Nonlocal)):
                loaded_module.update(n.names)
        loaded_module |= ann_names | all_names

        # group import statements by the scope they live in
        stmts = []
        for n in ast.walk(t):
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                scope = n
                while scope in self.parents:
                    scope = self.parents[scope]
                    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
                        break
                stmts.append((n, scope))
        seen = defaultdict(list)                                # (scope, bound name) -> [(stmt, alias)]
        per_stmt = defaultdict(list)                            # stmt -> [(alias, flagged finding id)]
        for node, scope in stmts:
            in_try = any(isinstance(p, ast.Try) for p in self.ancestors(node))
            blocked = in_try or self.comment_blocks(node)
            quiet = None
            for al in node.names:
                if al.name == "*" or (isinstance(node, ast.ImportFrom) and node.module == "__future__"):
                    continue
                bound = al.asname or al.name.split(".")[0]
                key = (id(scope), bound)
                same = [x for x in seen[key] if self.same_target(x[0], x[1], node, al)]
                seen[key].append((node, al))
                if same and not blocked:
                    fid = self.add(node, "duplicate-import", bound, "high",
                                   "`%s` is brought in again here (first at line %d)" % (bound, same[0][0].lineno), provable=True)
                    per_stmt[node].append((al, fid))
                    continue
                if seen[key][:-1] and not same:
                    prev = seen[key][0][0]
                    self.add(node, "import-overwritten", bound, "ambiguity",
                             "`%s` was already brought in at line %d from somewhere else - this one hides it" % (bound, prev.lineno))
                # is it used?
                if isinstance(scope, ast.Module):
                    used = bound in loaded_module
                    if not used and self.proj.reexported(self.rel, bound):
                        used = True
                else:
                    used = any(isinstance(m, ast.Name) and m.id == bound and isinstance(m.ctx, (ast.Load, ast.Del))
                               for m in ast.walk(scope)) or bound in ann_names
                    if not used and any(isinstance(m, ast.Call) and isinstance(m.func, ast.Name) and m.func.id in DYNAMIC_CALLS
                                        for m in ast.walk(scope)):
                        used = True
                if used:
                    continue
                if is_init:
                    continue                                    # package __init__ files re-export on purpose
                if quiet is None:
                    quiet = self.proj.import_is_quiet(self.rel, node)
                if blocked:
                    self.add(node, "unused-import", bound, "review",
                             "looks unused, but it sits in a try/except or carries a comment (noqa/type) - left alone")
                    continue
                if self.file_dynamic:
                    self.add(node, "unused-import", bound, "review",
                             "looks unused, but this file uses eval/exec/globals() so names can be reached in hidden ways")
                    continue
                if quiet:
                    fid = self.add(node, "unused-import", bound, "high", provable=True)
                else:
                    fid = self.add(node, "unused-import", bound, "medium" if self.third_party else "review",
                                   "unused, but loading `%s` might do something (it is not the standard library or a plain file of yours)"
                                   % (al.name if isinstance(node, ast.Import) else ((node.module or ".") )))
                per_stmt[node].append((al, fid))
        # build edit plans per statement
        removed_ids = set()
        planned = []
        for node, items in per_stmt.items():
            items = [(al, fid) for al, fid in items if self.findings[fid]["level"] in self.level_ok]
            if items:
                planned.append((node, items))
                if len(items) == len([a for a in node.names]):
                    removed_ids.add(id(node))
        for node, items in planned:
            if id(node) in removed_ids:
                e = self.remove_statement(node, removed_ids)
                edits = [e] if e else []
            else:
                edits = self.alias_edits(node, [al for al, _ in items])
            if edits:
                for _, fid in items:
                    self.findings[fid]["will_fix"] = True
                self.plans.append({"edits": edits, "ids": [fid for _, fid in items]})

    def ancestors(self, node):
        while node in self.parents:
            node = self.parents[node]
            yield node

    @staticmethod
    def same_target(n1, a1, n2, a2):
        if type(n1) is not type(n2):
            return False
        if isinstance(n1, ast.ImportFrom) and (n1.module, n1.level) != (n2.module, n2.level):
            return False
        return a1.name == a2.name and a1.asname == a2.asname

    def alias_edits(self, node, drop):
        """Remove only some names from  import a, b, c  /  from m import (a, b, c)."""
        s = self.src
        names = node.names
        edits = []
        for al in drop:
            i = names.index(al)
            a = s.off(al.lineno, al.col_offset)
            b = s.off(al.end_lineno, al.end_col_offset)
            if i + 1 < len(names) and names[i + 1] not in drop:
                nxt = s.off(names[i + 1].lineno, names[i + 1].col_offset)
                edits.append((a, nxt, ""))
            elif i > 0 and names[i - 1] not in drop:
                prev_end = s.off(names[i - 1].end_lineno, names[i - 1].end_col_offset)
                edits.append((prev_end, b, ""))
            else:
                return []
        return edits

    # ---------------- unused variables
    def variables(self):
        t = self.tree
        funcs = [n for n in ast.walk(t) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        cands = []                                              # (kind, node, name, stmt, extra)
        for f in funcs:
            sub = list(ast.walk(f))
            if any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in DYNAMIC_CALLS for n in sub):
                continue
            used = set()
            for n in sub:
                if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Load, ast.Del)):
                    used.add(n.id)
                elif isinstance(n, ast.AugAssign) and isinstance(n.target, ast.Name):
                    used.add(n.target.id)
                elif isinstance(n, (ast.Global, ast.Nonlocal)):
                    used.update(n.names)
            params = {a.arg for a in f.args.args + f.args.kwonlyargs + f.args.posonlyargs}
            params |= {a.arg for a in (f.args.vararg, f.args.kwarg) if a is not None}
            for n in own_nodes(f):
                if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
                    nm = n.targets[0].id
                    if nm not in used and nm not in params and not nm.startswith("_") and not self.comment_blocks(n):
                        cands.append(("var", n, nm, n))
                elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.value is not None:
                    nm = n.target.id
                    if nm not in used and nm not in params and not nm.startswith("_") and not self.comment_blocks(n):
                        cands.append(("var", n, nm, n))
                elif isinstance(n, (ast.With, ast.AsyncWith)):
                    for it in n.items:
                        if isinstance(it.optional_vars, ast.Name):
                            nm = it.optional_vars.id
                            if nm not in used and nm not in params and not nm.startswith("_") and not self.comment_blocks(n):
                                cands.append(("with", it, nm, n))
                elif isinstance(n, ast.ExceptHandler) and n.name and not n.name.startswith("_"):
                    body_used = any(isinstance(m, ast.Name) and m.id == n.name and isinstance(m.ctx, (ast.Load, ast.Del))
                                    for b in n.body for m in ast.walk(b))
                    declared = n.name in used and any(isinstance(m, (ast.Global, ast.Nonlocal)) and n.name in m.names for m in sub)
                    if not body_used and not declared and not self.comment_blocks(n):
                        cands.append(("exc", n, n.name, n))
        # first pass: which statements will be removed entirely (needed to decide where a `pass` is required)
        removed_ids = set()
        for kind, node, nm, stmt in cands:
            if kind == "var" and pure_expr(node.value) and "high" in self.level_ok:
                removed_ids.add(id(node))
        for kind, node, nm, stmt in cands:
            s = self.src
            if kind == "var":
                pure = pure_expr(node.value)
                level = "high" if pure else "medium"
                fid = self.add(node, "unused-variable", nm, level,
                               "" if pure else "the value is needed for what the right side DOES; only the `%s =` part would be dropped" % nm,
                               provable=pure)
                if level not in self.level_ok:
                    continue
                if pure:
                    e = self.remove_statement(node, removed_ids)
                    edits = [e] if e else []
                else:
                    a = s.off(node.lineno, node.col_offset)
                    b = s.off(node.value.lineno, node.value.col_offset)
                    edits = [(a, b, "")]
                if edits:
                    self.findings[fid]["will_fix"] = True
                    self.plans.append({"edits": edits, "ids": [fid]})
            elif kind == "with":
                fid = self.add(stmt, "unused-with-name", nm, "high", provable=True)
                if "high" not in self.level_ok:
                    continue
                a = s.off(node.context_expr.end_lineno, node.context_expr.end_col_offset)
                b = s.off(node.optional_vars.end_lineno, node.optional_vars.end_col_offset)
                if re.fullmatch(r"\s+as\s+" + re.escape(nm), self.text[a:b]):
                    self.findings[fid]["will_fix"] = True
                    self.plans.append({"edits": [(a, b, "")], "ids": [fid]})
            else:
                fid = self.add(node, "unused-exception-name", nm, "high", provable=True)
                if "high" not in self.level_ok or node.type is None:
                    continue
                a = s.off(node.type.end_lineno, node.type.end_col_offset)
                b = s.off(node.body[0].lineno, node.body[0].col_offset)
                m2 = re.match(r"(\s+as\s+" + re.escape(nm) + r")\s*:", self.text[a:b])
                if m2:
                    self.findings[fid]["will_fix"] = True
                    self.plans.append({"edits": [(a, a + len(m2.group(1)), "")], "ids": [fid]})

    # ---------------- ambiguity bugs (report only)
    def ambiguity(self):
        t = self.tree
        # same name defined twice in the same block
        for node in ast.walk(t):
            for field in ("body", "orelse", "finalbody"):
                lst = getattr(node, field, None)
                if not isinstance(lst, list):
                    continue
                seen = {}
                for st in lst:
                    if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        decos_names = {d.id for dec in st.decorator_list for d in ast.walk(dec) if isinstance(d, ast.Name)} | \
                                      {d.attr for dec in st.decorator_list for d in ast.walk(dec) if isinstance(d, ast.Attribute)}
                        if st.name in seen and not (decos_names & {st.name, "overload", "register", "setter", "getter", "deleter", "singledispatch"}):
                            self.add(st, "duplicate-definition", st.name, "ambiguity",
                                     "`%s` is already defined at line %d - this one replaces it, so the first can never run" % (st.name, seen[st.name]))
                        seen.setdefault(st.name, st.lineno)
        for n in ast.walk(t):
            if isinstance(n, ast.Dict):
                keys = {}
                for k, v in zip(n.keys, n.values):
                    if isinstance(k, ast.Constant):
                        kk = (type(k.value).__name__ if not isinstance(k.value, (int, float, bool)) else "num", k.value)
                        if kk in keys:
                            self.add(k, "duplicate-dict-key", repr(k.value), "ambiguity",
                                     "key %r appears twice (first at line %d); the later value wins" % (k.value, keys[kk]))
                        else:
                            keys[kk] = k.lineno
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for d in list(n.args.defaults) + [x for x in n.args.kw_defaults if x is not None]:
                    if isinstance(d, (ast.List, ast.Dict, ast.Set)) or \
                            (isinstance(d, ast.Call) and isinstance(d.func, ast.Name) and d.func.id in ("list", "dict", "set")):
                        self.add(d, "mutable-default", n.name, "ambiguity", "function `%s` shares one list/dictionary between all calls" % n.name)
            elif isinstance(n, ast.Compare):
                for op, right in zip(n.ops, n.comparators):
                    if isinstance(op, (ast.Is, ast.IsNot)):
                        for side in (n.left, right):
                            if isinstance(side, ast.Constant) and isinstance(side.value, (str, bytes, int, float)) \
                                    and not isinstance(side.value, bool):
                                self.add(n, "is-literal", repr(side.value)[:30], "ambiguity", "use == to compare with a text/number")
                                break
            elif isinstance(n, ast.Expr) and isinstance(n.value, ast.Compare):
                self.add(n, "useless-comparison", "", "ambiguity", "the result of this comparison is thrown away")
            elif isinstance(n, ast.Assert) and isinstance(n.test, ast.Tuple) and n.test.elts:
                self.add(n, "assert-tuple", "", "ambiguity", "remove the parentheses/comma so the condition itself is tested")
            elif isinstance(n, ast.For) and isinstance(n.target, ast.Name):
                for m in own_nodes(n):
                    if isinstance(m, ast.For) and isinstance(m.target, ast.Name) and m.target.id == n.target.id:
                        self.add(m, "nested-loop-same-var", n.target.id, "ambiguity",
                                 "the loop at line %d already uses `%s`" % (n.lineno, n.target.id))
            elif isinstance(n, ast.If):
                tests = [ast.dump(n.test)]
                cur = n
                while len(cur.orelse) == 1 and isinstance(cur.orelse[0], ast.If):
                    cur = cur.orelse[0]
                    d = ast.dump(cur.test)
                    if d in tests:
                        self.add(cur, "duplicate-condition", "", "ambiguity", "same test as an earlier branch of this if/elif chain")
                    tests.append(d)
        # names that hide Python's own tools, and imports hidden by a later assignment
        imported = {}
        for st in self.tree.body:
            if isinstance(st, (ast.Import, ast.ImportFrom)):
                for al in st.names:
                    imported[al.asname or al.name.split(".")[0]] = st.lineno
        scopes = [t] + [n for n in ast.walk(t) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        reported = set()
        for sc in scopes:
            called = {c.func.id for c in ast.walk(sc) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
            stores = []
            for n in own_nodes(sc, skip_comprehensions=True):
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                    stores.append((n.id, n))
                elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    stores.append((n.name, n))
            if isinstance(sc, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for a in sc.args.args + sc.args.kwonlyargs + sc.args.posonlyargs:
                    if a.arg in BUILTIN_NAMES and a.arg in called:
                        stores.append((a.arg, a))
            for nm, node in stores:
                if nm in BUILTIN_NAMES and (nm, node.lineno) not in reported:
                    reported.add((nm, node.lineno))
                    if nm in called:
                        msg = ("`%s` now means your value here, yet this same scope also CALLS `%s(...)` - "
                               "that call will fail or do the wrong thing" % (nm, nm))
                    else:
                        msg = "`%s` hides Python's own `%s` in this scope" % (nm, nm)
                    self.add(node, "shadowed-builtin", nm, "ambiguity", msg)
                if isinstance(sc, ast.Module) and nm in imported and node.lineno > imported[nm] \
                        and isinstance(node, (ast.Name, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
                        and not any(isinstance(p, ast.Try) for p in self.ancestors(node)):
                    self.add(node, "import-overwritten", nm, "ambiguity",
                             "`%s` was imported at line %d and is replaced here" % (nm, imported[nm]))


# ----------------------------------------------------------------------------------------------------------------
# JavaScript / TypeScript (careful, text based: reports + the one provably-safe fix)
# ----------------------------------------------------------------------------------------------------------------
IMPORT_RE = re.compile(r"(?m)^[ \t]*import\s+(?P<type>type\s+)?(?P<clause>[^;'\"]*?)\s+from\s+(?P<q>['\"])(?P<src>[^'\"\n]+)(?P=q)[ \t]*;?")


def js_analyse(rel, text, level_ok):
    findings, plans = [], []
    lines = text.split("\n")

    def line_of(off):
        return text.count("\n", 0, off) + 1

    is_jsx_file = bool(re.search(r"</[A-Za-z]|/>", text)) or rel.lower().endswith((".jsx", ".tsx"))
    spans = [(m.start(), m.end()) for m in IMPORT_RE.finditer(text)]
    blanked = list(text)
    for a, b in spans:
        for i in range(a, b):
            if blanked[i] != "\n":
                blanked[i] = " "
    rest = "".join(blanked)
    for m in IMPORT_RE.finditer(text):
        clause = m.group("clause").strip()
        default, ns, named = None, None, []
        braces = re.search(r"\{(.*?)\}", clause, re.S)
        head = clause[:braces.start()] if braces else clause
        head = head.strip().rstrip(",").strip()
        if braces:
            for part in braces.group(1).split(","):
                part = part.strip()
                if not part:
                    continue
                mm = re.fullmatch(r"(?:type\s+)?([\w$]+)(?:\s+as\s+([\w$]+))?", part)
                if mm:
                    named.append((mm.group(2) or mm.group(1), part))
        if head.startswith("*"):
            mm = re.fullmatch(r"\*\s+as\s+([\w$]+)", head)
            ns = mm.group(1) if mm else None
        elif head:
            default = head.split(",")[0].strip()
            if not re.fullmatch(r"[\w$]+", default):
                default = None
        bound = ([default] if default else []) + ([ns] if ns else []) + [n for n, _ in named]
        if not bound:
            continue
        if is_jsx_file:
            bound = [b for b in bound if b != "React"]           # classic JSX quietly needs React
        unused = [b for b in bound if not re.search(r"(?:(?<![\w$.])|(?<=\.\.\.))" + re.escape(b) + r"(?![\w$])", rest)]
        if not unused:
            continue
        ln = line_of(m.start())
        multiline = "\n" in m.group(0)
        first_idx = len(findings)
        idx = {}
        for b in unused:
            idx[b] = len(findings)
            findings.append({"file": rel, "line": ln, "col": 0, "kind": "unused-import", "name": b, "level": "review",
                             "detail": "from '%s'" % m.group("src"), "code": lines[ln - 1].strip()[:140], "will_fix": False,
                             "provable": False, "lang": "js"})
        remaining_used = [b for b in bound if b not in unused]
        removable = [n for n, _ in named if n in unused]
        if remaining_used and removable and braces and not multiline and not m.group("type") and "high" in level_ok:
            new_named = [p for n, p in named if n not in unused]
            stmt = m.group(0)
            ci = stmt.index(clause)
            new_clause = (head + ", " if head else "") + ("{ " + ", ".join(new_named) + " }" if new_named else "")
            new_clause = new_clause.rstrip(", ") if not new_named else new_clause
            if new_clause.strip():
                plans.append({"edits": [(m.start(), m.end(), stmt[:ci] + new_clause + stmt[ci + len(clause):])],
                              "ids": [idx[n] for n in removable]})
                for n in removable:
                    findings[idx[n]]["level"] = "high"
                    findings[idx[n]]["will_fix"] = True
    # same function/class declared twice at the top of the file
    seen = {}
    for m in re.finditer(r"(?m)^(?:export\s+(?:default\s+)?)?(?:async\s+)?(?:function\s*\*?\s*|class\s+)([A-Za-z_$][\w$]*)[^\n;]*\{", text):
        nm = m.group(1)
        ln = line_of(m.start())
        if nm in seen:
            findings.append({"file": rel, "line": ln, "col": 0, "kind": "duplicate-definition", "name": nm, "level": "ambiguity",
                             "detail": "`%s` is already declared at line %d - this one replaces it" % (nm, seen[nm]),
                             "code": lines[ln - 1].strip()[:140], "will_fix": False, "provable": False, "lang": "js"})
        else:
            seen[nm] = ln
    return findings, plans


# ----------------------------------------------------------------------------------------------------------------
# putting edits into text, one by one, each checked
# ----------------------------------------------------------------------------------------------------------------
def apply_plans(text, plans, valid):
    """plans: [{"edits": [(s, e, repl)], "ids": [...]}].  Returns (new_text, applied_ids)."""
    order = sorted(range(len(plans)), key=lambda i: -min(e[0] for e in plans[i]["edits"]))
    cur, applied, floor = text, [], None
    for i in order:
        edits = sorted(plans[i]["edits"], key=lambda e: -e[0])
        top = max(e[1] for e in edits)
        if floor is not None and top > floor:
            continue                                            # overlaps something already changed
        new = cur
        for s, e, r in edits:
            new = new[:s] + r + new[e:]
        if valid(new):
            cur = new
            applied += plans[i]["ids"]
            floor = min(e[0] for e in edits)
    return cur, applied


def py_valid(t):
    try:
        ast.parse(t)
        return True
    except (SyntaxError, ValueError, RecursionError):
        return False


# ----------------------------------------------------------------------------------------------------------------
# the whole check
# ----------------------------------------------------------------------------------------------------------------
def analyse(root, level="high", third_party=False):
    root = Path(root).resolve()
    proj = Project(root)
    level_ok = {"high"} if level == "high" else {"high", "medium"}
    findings, plans_by_file = [], {}
    for rel in sorted(proj.trees):
        pf = PyFile(proj, rel, level_ok, third_party).run()
        base = len(findings)
        findings += pf.findings
        if pf.plans:
            plans_by_file[rel] = {"lang": "python", "plans": [{"edits": p["edits"], "ids": [base + i for i in p["ids"]]} for p in pf.plans]}
    for rel in sorted(proj.js):
        fs, plans = js_analyse(rel, proj.js[rel], level_ok)
        base = len(findings)
        findings += fs
        if plans:
            plans_by_file[rel] = {"lang": "js", "plans": [{"edits": p["edits"], "ids": [base + i for i in p["ids"]]} for p in plans]}
    return {"root": str(root), "findings": findings, "plans": plans_by_file, "project": proj, "level": level_ok}


def summarise(res):
    c = defaultdict(int)
    for f in res["findings"]:
        c[f["kind"]] += 1
    return dict(c)


def print_report(res, limit=400):
    fs = res["findings"]
    proj = res["project"]
    print("Arachnid logic checker - looking at: %s" % res["root"])
    print("   Python files read: %d   JavaScript/TypeScript files read: %d" % (len(proj.trees), len(proj.js)))
    if proj.skipped:
        print("   (left out because they could not be parsed - fix their syntax first: %s)" % ", ".join(proj.skipped[:5]))
    if not fs:
        print("\nNothing found.")
        return
    by_file = defaultdict(list)
    for f in fs:
        by_file[f["file"]].append(f)
    shown = 0
    order = {"high": 0, "medium": 1, "review": 2, "ambiguity": 3}
    for rel in sorted(by_file):
        print("\n" + rel)
        for f in sorted(by_file[rel], key=lambda f: (f["line"], order[f["level"]])):
            if shown >= limit:
                break
            shown += 1
            tag = "will be fixed" if f["will_fix"] else LEVEL_TEXT[f["level"]]
            nm = ("  " + f["name"]) if f["name"] else ""
            print("  line %-5d %-22s%s   [%s]" % (f["line"], f["kind"], nm, tag))
            if f["detail"]:
                print("             " + f["detail"])
            if f["code"]:
                print("             " + f["code"])
    kinds = sorted({f["kind"] for f in fs})
    print("\n---------------- What these words mean ----------------")
    for k in kinds:
        print("  %-22s %s" % (k, KIND_TEXT[k]))
    cnt = summarise(res)
    fixable = sum(1 for f in fs if f["will_fix"])
    amb = sum(1 for f in fs if f["level"] == "ambiguity")
    rev = sum(1 for f in fs if f["level"] == "review")
    print("\n---------------- Summary ----------------")
    print("Found: %d   (unused imports %d, unused variables %d, ambiguity bugs %d)" % (
        len(fs), cnt.get("unused-import", 0) + cnt.get("duplicate-import", 0),
        cnt.get("unused-variable", 0) + cnt.get("unused-exception-name", 0) + cnt.get("unused-with-name", 0), amb))
    print("Can be fixed automatically: %d     Your decision (never touched): %d" % (fixable, amb + rev))


def save_report(res, out):
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    data = {"root": res["root"], "summary": summarise(res), "findings": res["findings"]}
    out.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    print("Full report saved to: %s" % out)


# ----------------------------------------------------------------------------------------------------------------
# applying (backup -> gate -> write)
# ----------------------------------------------------------------------------------------------------------------
NO_PROOF = "This project has no web pages, no build and no tests"


def apply_fixes(res, level, accept_analysis=False):
    root = Path(res["root"])
    findings = res["findings"]
    proj = res["project"]
    new_texts, applied_by_file = {}, {}
    oracle = None
    for rel, pl in res["plans"].items():
        text = proj.py.get(rel) if pl["lang"] == "python" else proj.js.get(rel)
        if pl["lang"] == "python":
            valid = py_valid
        else:
            if oracle is None:
                try:
                    from syntaxcheck import Oracle
                    oracle = Oracle(root)
                except Exception:
                    oracle = False
            ext = Path(rel).suffix.lower().lstrip(".")
            if not oracle or not oracle.can(ext):
                continue
            valid = (lambda t, e=ext: oracle.first_errors([t], e)[0] is None)
        new, applied = apply_plans(text, pl["plans"], valid)
        if new != text and applied:
            new_texts[rel] = new
            applied_by_file[rel] = applied
    if not new_texts:
        print("\nNothing to fix automatically at this level.")
        return set(), {}
    print("\nSafety gate: proving the product stays exactly the same ...")
    from gate import guard, guard_units

    def apply_units(copy, units):
        for rel_ in units:
            with open(Path(copy) / rel_, "w", encoding="utf-8", newline="") as fh:
                fh.write(new_texts[rel_])

    units = sorted(new_texts)
    ok, lines = guard(root, lambda copy: apply_units(copy, units))
    passed, failed, notes = [], [], lines[:1]
    if ok:
        passed = units
    elif lines and lines[0].startswith(NO_PROOF) and accept_analysis:
        # nothing to run: accept only the kinds that are harmless by construction (checked by analysis)
        for rel in units:
            fs = [findings[i] for i in applied_by_file[rel]]
            if all(f["provable"] and f["level"] == "high" and f["lang"] == "python" for f in fs):
                passed.append(rel)
            else:
                failed.append((rel, "nothing can run to prove this change, and it is not one of the kinds that is harmless by analysis"))
        if passed:
            notes = ["Nothing can be run here to compare (no pages, build or tests) - proved by analysis instead: only unused standard-library/plain imports and "
                     "unused boxes holding plain values were removed."]
    elif lines and lines[0].startswith(NO_PROOF):
        failed = [(rel, "the gate has nothing to run here (no pages, build or tests), so it cannot prove the code is unchanged. "
                        "Add tests, or allow analysis-only proof with --accept-analysis") for rel in units]
    else:
        passed, failed, notes = guard_units(root, units, apply_units)
    for n in notes:
        print("   " + n)
    for u, why in failed:
        print("   HELD BACK for the owner: %s - %s" % (u, why))
    held_map = {u: why for u, why in failed}
    if not passed:
        return set(), held_map
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S") + "-logic"
    bdir = HERE / "backups" / stamp
    log, changed = [], []
    for rel in passed:
        if not os.access(root / rel, os.W_OK):     # read-only: left exactly as it is (permissions never changed)
            print("   SKIPPED %s - the file is read-only, so it is left as it is" % rel)
            continue
        dest = bdir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / rel, dest)
        with open(root / rel, "w", encoding="utf-8", newline="") as fh:
            fh.write(new_texts[rel])
        changed.append(rel)
        for i in applied_by_file[rel]:
            f = findings[i]
            log.append({"file": rel, "line": f["line"], "kind": f["kind"], "name": f["name"]})
    (bdir / "logic-manifest.json").write_text(json.dumps({"root": str(root), "files": changed, "fixes": log}, indent=2), encoding="utf-8")
    print("\n---------------- Auto-fix ----------------")
    for rel in changed:
        print("  %s   (%d change(s))" % (rel, len(applied_by_file[rel])))
    print("\nFixed %d thing(s) in %d file(s).  Backup: %s" % (len(log), len(changed), bdir))
    print("To put everything back:  python logiccheck.py --undo")
    return {i for rel in changed for i in applied_by_file[rel]}, held_map


def record_ledger(res, applied, held):
    try:
        import ledger
        ledger.reset("logic")
        for i, f in enumerate(res["findings"]):
            what = "%s %s%s" % (f["kind"], f["name"], (" - " + f["detail"]) if f["detail"] else "")
            if i in applied:
                ledger.record("logic", "fixed", "info", f["file"], f["line"], what, kind=f["kind"])
            elif f["will_fix"] and f["file"] in held:
                ledger.record("logic", "held", "warning", f["file"], f["line"], what + " (held back: %s)" % held[f["file"]][:120],
                              "removing it could not be proven harmless", "review and decide", f["kind"])
            elif f["will_fix"]:
                ledger.record("logic", "fixable", "info", f["file"], f["line"], what, kind=f["kind"])
            elif f["level"] == "ambiguity":
                ledger.record("logic", "decision", "warning", f["file"], f["line"], what,
                              "the code does something other than it looks like it does", "decide how it should behave", f["kind"])
            else:
                ledger.record("logic", "decision", "info", f["file"], f["line"], what, "", "optional clean-up; see logiccheck.py --level medium", f["kind"])
    except Exception:
        pass


def undo():
    base = HERE / "backups"
    sessions = sorted(p for p in base.glob("*") if (p / "logic-manifest.json").exists()) if base.exists() else []
    if not sessions:
        sys.exit("No logic backups found - nothing to undo.")
    last = sessions[-1]
    man = json.loads((last / "logic-manifest.json").read_text(encoding="utf-8"))
    root = Path(man["root"])
    for rel in man["files"]:
        shutil.copy2(last / rel, root / rel)
    (last / "logic-manifest.json").rename(last / "logic-manifest.undone.json")
    print("Restored %d file(s) from backup %s." % (len(man["files"]), last.name))


def main():
    ap = argparse.ArgumentParser(description="Arachnid logic checker - Step 6 (unused imports, unused variables, ambiguity bugs)")
    ap.add_argument("root", nargs="?", default=str(HERE.parent))
    ap.add_argument("--fix", action="store_true", help="remove what is provably useless (through the safety gate)")
    ap.add_argument("--level", choices=["high", "medium"], default="high")
    ap.add_argument("--third-party", action="store_true", help="also treat unused imports of outside libraries as 'probably safe'")
    ap.add_argument("--accept-analysis", action="store_true",
                    help="for projects with nothing the gate can run (no pages/build/tests): accept the provably-harmless kinds on analysis alone")
    ap.add_argument("--out", default=str(HERE / "reports" / "logic-report.json"))
    ap.add_argument("--undo", action="store_true")
    a = ap.parse_args()
    if a.undo:
        return undo()
    res = analyse(a.root, a.level, a.third_party)
    print_report(res)
    save_report(res, a.out)
    applied, held = set(), {}
    if a.fix:
        applied, held = apply_fixes(res, a.level, a.accept_analysis)
    record_ledger(res, applied, held)


if __name__ == "__main__":
    main()
