#!/usr/bin/env python3
"""
ARACHNID - BUG HUNTER  (looks only, never changes anything)
===========================================================
    python bugcheck.py [folder]
    python bugcheck.py [folder] --rules        list every rule with an example

The other checks find code that is BROKEN (it does not even run) or USELESS (it does nothing).  This one hunts the
worst kind: code that runs perfectly, shows no error, and quietly does the WRONG thing.

    if answer == "yes" or "y":          always true - "y" on its own counts as true
    items = items.sort()                items is now None - sort() changes the list and returns nothing
    name.strip()                        does nothing - the cleaned text is thrown away
    return                              (in JavaScript, a line break after return returns NOTHING)
        total
    document.getElementById("#menu")    finds nothing - the # belongs to CSS selectors, not ids
    width: 100;                         ignored by the browser - 100 what? px? %?

Languages: Python, JavaScript, TypeScript, HTML, CSS, JSON, Markdown.

Nothing is ever changed.  Every finding is a DECISION for a person (or an IDEA for small polish), because fixing a
logic mistake changes what the program does - only the owner knows what was meant.  Each rule was chosen so that
it is almost never wrong: if a case could reasonably be intended, it is left alone.
"""
import argparse
import ast
import json
import posixpath
import re
import sys
import urllib.parse
from html.parser import HTMLParser
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import dedupe  # noqa: E402
import ledger  # noqa: E402

MAX_SIZE = 1_500_000
TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|spec|specs|e2e|cypress|fixtures?|mocks?)(/|$)|(^|/)test_[^/]*$|(^|/)tests?\.[jt]sx?$|"
                       r"_test\.py$|\.(test|spec)\.[jt]sx?$", re.I)

# kind: (pile, title, why it matters, what to do)        pile: "decision" (warning) or "idea" (info)
RULES = {
    # ---------------------------------------------------------------- Python
    "py-or-literal": ("decision", "a condition that is always true",
                      "`x == \"a\" or \"b\"` means `(x == \"a\") or \"b\"`, and \"b\" on its own always counts as true",
                      "write `x == \"a\" or x == \"b\"`, or `x in (\"a\", \"b\")`"),
    "py-none-result": ("decision", "a value that is always None",
                       "sort(), append(), extend(), reverse(), insert() and random.shuffle() change the list in place and give back None",
                       "call it on its own line, or use sorted(...) / a copy if you need the result"),
    "py-no-effect": ("decision", "a line that does nothing",
                     "text is never changed in place: strip(), lower(), replace() ... give back a NEW text, and here it is thrown away",
                     "keep the result, e.g. `name = name.strip()`"),
    "py-missing-self": ("decision", "a method that crashes when it is called",
                        "a function inside a class gets the object as its first value; with no parameters at all, calling it fails",
                        "add `self` as the first parameter, or mark it @staticmethod"),
    "py-method-not-called": ("decision", "a test that is always true",
                             "the method is named but not called (no brackets), and a method itself always counts as true",
                             "add the brackets: `if self.is_valid():`"),
    "py-return-in-init": ("decision", "a crash when the object is created",
                          "__init__ must not return a value - Python raises TypeError",
                          "remove the value from the return, or move the logic out of __init__"),
    "py-return-in-finally": ("decision", "an error that silently disappears",
                             "return / break / continue inside `finally` throws away any error that was on its way out",
                             "move it out of the finally block"),
    "py-unreachable": ("decision", "code that can never run",
                       "it comes right after a return / raise / break / continue in the same block",
                       "remove it, or move it before that line if it was meant to run"),
    "py-missing-f": ("decision", "text that shows {name} literally",
                     "the text has {placeholders} with names from this function, but no f in front, so the braces are printed as they are",
                     "put an f before the quote: f\"...\""),
    "py-len-impossible": ("decision", "a length check that never changes anything",
                          "a length is never below 0, so `len(x) < 0` is always false and `len(x) >= 0` always true",
                          "check what was meant - usually `== 0` or `> 0`"),
    "py-off-by-one": ("decision", "a loop that goes one step too far",
                      "range(len(x) + 1) reaches the position len(x), which does not exist - IndexError on the last step",
                      "use range(len(x))"),
    "py-float-equal": ("decision", "a decimal comparison that can fail",
                       "decimals like 0.1 or 19.99 are stored approximately, so `==` can be false although the numbers look equal",
                       "compare with a small tolerance (math.isclose), or use whole numbers (paise/cents) or Decimal"),
    "py-loop-never-ends": ("decision", "a loop that never ends",
                           "the loop checks a variable that is never changed inside the loop, and nothing inside stops it",
                           "change the variable inside the loop (e.g. `i += 1`), or add a break"),
    "py-input-unchecked": ("decision", "a crash on unexpected input",
                           "int(input()) / float(input()) stops the program with ValueError as soon as someone types a letter",
                           "wrap it in try/except ValueError and ask again"),
    "py-bare-except": ("idea", "errors (and Ctrl+C) silently swallowed",
                       "`except:` with only `pass` hides every error - even the ones you need to know about",
                       "catch the specific error you expect, or at least log it"),
    "py-self-assign": ("idea", "an assignment that does nothing",
                       "a variable is set to itself", "check which value was meant"),
    "py-stale-default": ("decision", "a default value that never updates",
                         "defaults are calculated ONCE, when the function is created - every later call gets that same old time / id / random value",
                         "use `=None` and calculate the value inside the function"),
    "py-exception-not-raised": ("decision", "an error that is created but never raised",
                                "the error object is made and thrown away - the program just continues",
                                "add `raise` in front"),
    "py-modify-while-looping": ("decision", "a loop that skips or repeats items",
                                "the list is changed while the loop walks over it, so items are skipped (remove) or the loop grows (append)",
                                "loop over a copy: `for x in list(items):`, or build a new list"),
    "py-float-index": ("decision", "a crash when this position is used",
                       "`/` always gives a decimal (5/2 = 2.5), and a decimal cannot be a position in a list",
                       "use `//` for whole-number division"),
    "py-find-gt-zero": ("idea", "a search that misses the first position",
                        "find()/index() give 0 when the match is at the very start, and `> 0` treats that as 'not found'",
                        "use `>= 0`, `!= -1`, or `in`"),
    "py-falls-through": ("decision", "a function that sometimes returns nothing",
                         "some paths return a value, but if none of the conditions match the function ends and gives back None",
                         "add a final return (or raise) for the remaining cases"),
    # ---------------------------------------------------------------- JavaScript / TypeScript
    "js-assign-in-condition": ("decision", "a condition that changes a value instead of comparing it",
                               "a single = puts a value in; the condition then only checks that value",
                               "use === to compare (or wrap in extra brackets if the assignment is intended)"),
    "js-nan-compare": ("decision", "a NaN check that never works",
                       "NaN is never equal to anything, not even to NaN", "use Number.isNaN(x)"),
    "js-bad-typeof": ("decision", "a typeof check that is always false",
                      "typeof only ever gives: undefined, object, boolean, number, bigint, string, symbol, function",
                      "fix the spelling of the type name"),
    "js-or-literal": ("decision", "a condition that is always true",
                      "`x === \"a\" || \"b\"` means `(x === \"a\") || \"b\"`, and \"b\" alone always counts as true",
                      "write `x === \"a\" || x === \"b\"`, or `[\"a\", \"b\"].includes(x)`"),
    "js-new-literal-compare": ("decision", "a comparison that is never true",
                               "`[]` and `{}` create a NEW empty list/object, which is never the same object as x",
                               "to test for empty use x.length === 0 or Object.keys(x).length === 0"),
    "js-typo-property": ("decision", "a misspelled built-in name",
                         "JavaScript does not complain about an unknown property - it is simply undefined, or creates a new useless one",
                         "use the correct spelling"),
    "js-length-call": ("decision", "a crash: length is not a function",
                       "`.length` is a number, not something you call", "remove the brackets: `.length`"),
    "js-id-selector": ("decision", "an element lookup that always finds nothing",
                       "getElementById wants the bare id (`menu`), and getElementsByClassName the bare class; # and . belong to CSS selectors",
                       "remove the # / . - or use querySelector(\"#menu\")"),
    "js-on-event": ("decision", "an event listener that never fires",
                    "addEventListener uses event names without 'on' (\"click\", not \"onclick\")", "remove the 'on'"),
    "js-return-newline": ("decision", "a return that gives back nothing",
                          "JavaScript ends the statement at a line break after `return`, so the value on the next line is never returned",
                          "start the value on the same line as return: `return (`"),
    "js-fetch-not-awaited": ("decision", "a response used before it arrives",
                             "fetch() gives back a Promise; without await (or .then) the response is not there yet",
                             "write `await fetch(...)`"),
    "js-foreach-async": ("decision", "async work that is not waited for",
                         "forEach does not wait for async functions - the code after it runs before they finish",
                         "use `for (const x of list) { await ... }` or `await Promise.all(list.map(...))`"),
    "js-async-effect": ("decision", "a React effect that cannot clean up",
                        "useEffect must return nothing or a cleanup function; an async function returns a Promise",
                        "define an async function inside the effect and call it"),
    "js-handler-called": ("decision", "a handler that runs immediately",
                          "with brackets the function runs NOW (while the page is drawn / the line runs), and its result becomes the handler",
                          "pass the function itself, or wrap it: `() => handle(x)`"),
    "js-off-by-one": ("decision", "a loop that goes one step too far",
                      "`i <= list.length` reaches list[list.length], which does not exist (undefined)",
                      "use `i < list.length`"),
    "js-no-effect": ("decision", "a line that does nothing",
                     "text and list methods like trim(), toUpperCase(), replace(), filter(), slice() give back a NEW value, and here it is thrown away",
                     "keep the result, e.g. `name = name.trim()`"),
    "js-plus-equals": ("decision", "`=+` where `+=` was probably meant",
                       "`x =+ 1` sets x to 1; it does not add 1", "write `x += 1` (or `x = +y` if a number conversion is meant)"),
    "js-duplicate-case": ("decision", "a switch case that can never run",
                          "the same case value appears twice; only the first one is ever used", "remove or change the duplicate"),
    "js-index-of-gt-zero": ("idea", "a search that misses the first position",
                            "indexOf() gives 0 when the item is first, and `> 0` treats that as 'not found'",
                            "use `>= 0`, `!== -1`, or includes()"),
    "js-bad-month": ("decision", "a date in the wrong month",
                     "months in new Date() count from 0 (January = 0, December = 11); 12 rolls over to January of the next year",
                     "subtract 1 from the month"),
    # ---------------------------------------------------------------- HTML
    "html-duplicate-id": ("decision", "the same id twice on one page",
                          "an id must be unique; scripts and labels only ever find the first element", "give each element its own id"),
    "html-duplicate-attribute": ("decision", "an attribute written twice on one tag",
                                 "the browser keeps only the first one and silently ignores the other", "keep one"),
    "html-label-target": ("decision", "a label connected to nothing",
                          "<label for=\"x\"> needs an element with id=\"x\"; clicking the label does nothing and screen readers cannot connect them",
                          "make the for and the id match"),
    "html-negative-number": ("decision", "a number field that accepts negative values",
                             "a quantity, price or amount field without min lets people type -5 (the browser accepts it)",
                             "add min=\"0\" (or min=\"1\") - and check it again on the server"),
    "html-button-submits": ("decision", "a button that also submits the form",
                            "inside a <form>, a <button> without type is a SUBMIT button - clicking it reloads/sends the page as well as running onclick",
                            "add type=\"button\" if it should only run its script"),
    "html-bad-input-type": ("decision", "an input with a misspelled type",
                            "an unknown type silently becomes a plain text box, so its checks (email, number, date ...) are lost",
                            "use one of the real input types"),
    "html-bad-script-type": ("decision", "a script that never runs",
                             "a script with an unknown type is ignored by the browser", "use type=\"module\" or leave type out"),
    "html-missing-element": ("decision", "a script looks for an element that does not exist",
                             "no page or component in the project has this id, so the lookup gives null and the next line crashes",
                             "fix the id, or add the element"),
    "html-missing-function": ("decision", "a click that calls a function that does not exist",
                              "no script in the project defines this function, so the click shows an error in the console and does nothing",
                              "fix the name, or add the function"),
    "html-no-doctype": ("idea", "the page has no <!doctype html>",
                        "without it browsers use 'quirks mode', where some sizes and layouts behave differently", "add <!doctype html> as the first line"),
    "html-no-viewport": ("idea", "the page is not set up for phones",
                         "without <meta name=\"viewport\"> phones show a zoomed-out desktop page",
                         "add <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"),
    "html-no-lang": ("idea", "the page does not say its language",
                     "screen readers and translators guess the language", "add lang=\"en\" (or your language) to <html>"),
    "html-no-title": ("idea", "the page has no title",
                      "the browser tab and search results show the file name instead", "add <title>...</title> in <head>"),
    "html-img-alt": ("idea", "a picture without a description",
                     "screen readers cannot describe it, and nothing is shown if it fails to load", "add alt=\"...\" (alt=\"\" for decoration)"),
    # ---------------------------------------------------------------- CSS
    "css-unknown-property": ("decision", "a misspelled style property",
                             "the browser silently skips properties it does not know, so this style never applies",
                             "use the correct property name"),
    "css-bad-color": ("decision", "a colour the browser cannot read",
                      "a # colour needs 3, 4, 6 or 8 digits from 0-9 and a-f; anything else is ignored", "fix the colour code"),
    "css-missing-unit": ("decision", "a size without a unit",
                         "apart from 0, sizes need a unit (px, rem, %, ...); `width: 100` is ignored by the browser",
                         "add the unit, e.g. 100px"),
    "css-duplicate-property": ("idea", "the same property twice in one rule",
                               "only the last one counts, so the first value never shows", "keep the one you want"),
    # ---------------------------------------------------------------- JSON
    "json-duplicate-key": ("decision", "the same key twice",
                           "only the LAST value is kept; the other one is silently thrown away", "keep one key"),
    "json-missing-file": ("decision", "package.json points to a file that does not exist",
                          "`npm start` (or importing the package) fails because this file is not there", "fix the path"),
    "json-bad-package-name": ("decision", "a package name npm refuses",
                              "npm package names must be lowercase, without spaces", "use lowercase letters, numbers, - and _"),
    "json-unpinned": ("idea", "a dependency without a version",
                      "\"*\" or \"latest\" installs whatever is newest, so tomorrow's install can break without any change on your side",
                      "use a version range such as ^1.2.3"),
    # ---------------------------------------------------------------- Markdown
    "md-broken-link": ("decision", "a link or picture to a file that does not exist",
                       "readers get a 404 page or a broken picture", "fix the path"),
    "md-broken-anchor": ("decision", "a link to a heading that does not exist",
                         "the link jumps nowhere", "match the heading text (lowercase, spaces become -)"),
    "md-unclosed-fence": ("decision", "a code block that never ends",
                          "everything after the opening ``` is shown as code", "add the closing ```"),
    "md-heading-space": ("decision", "a heading that is shown as plain text",
                         "a heading needs a space after the # (`# Title`, not `#Title`)", "add the space"),
    "md-link-gap": ("decision", "a link that is shown as plain text",
                    "there must be no space between ] and ( in [text](link)", "remove the space"),
    "md-link-space": ("decision", "a link with a space in its address",
                      "a space ends the address, so the link is cut off", "write the space as %20, or rename the file"),
}


def add(out, rel, line, kind, detail):
    out.append({"file": rel, "line": int(line or 0), "kind": kind, "detail": detail})


# =================================================================================================== Python
STR_NO_EFFECT = {"strip", "lstrip", "rstrip", "lower", "upper", "title", "capitalize", "casefold", "swapcase", "zfill",
                 "center", "ljust", "rjust", "removeprefix", "removesuffix", "expandtabs"}
NONE_RESULT = {"sort", "append", "extend", "reverse", "insert"}
STALE_CALLS = re.compile(r"(^|\.)(now|utcnow|today|time|time_ns|uuid1|uuid4|random|randint|token_hex|token_urlsafe|monotonic)$")
EXITS = re.compile(r"(^|\.)(exit|_exit|quit|abort)$")
LOGGERS = {"print", "write", "info", "warning", "warn", "error", "debug", "exception", "critical", "append", "send", "set_text",
           "setText", "insert", "add_row", "title", "set_title", "showinfo", "showerror", "showwarning", "label", "Label"}


def _dotted(f):
    parts = []
    while isinstance(f, ast.Attribute):
        parts.append(f.attr)
        f = f.value
    if isinstance(f, ast.Name):
        parts.append(f.id)
    return ".".join(reversed(parts))


def _terminates(stmts):
    for s in stmts:
        if isinstance(s, (ast.Return, ast.Raise)):
            return True
        if isinstance(s, ast.If) and s.orelse and _terminates(s.body) and _terminates(s.orelse):
            return True
        if isinstance(s, (ast.With, ast.AsyncWith)) and _terminates(s.body):
            return True
        if isinstance(s, ast.Try) and ((_terminates(s.body) and all(_terminates(h.body) for h in s.handlers)
                                        and (not s.orelse or _terminates(s.orelse))) or _terminates(s.finalbody)):
            return True
        if isinstance(s, ast.While) and isinstance(s.test, ast.Constant) and s.test.value and \
                not any(isinstance(n, ast.Break) for n in _walk_same_function(s)):
            return True
        if isinstance(s, ast.Expr) and isinstance(s.value, ast.Call) and EXITS.search(_dotted(s.value.func) or ""):
            return True
        if hasattr(ast, "Match") and isinstance(s, ast.Match):
            if any(isinstance(c.pattern, ast.MatchAs) and c.pattern.pattern is None for c in s.cases) and \
                    all(_terminates(c.body) for c in s.cases):
                return True
    return False


def _walk_same_function(node):
    """Like ast.walk, but does not go into nested functions, classes or lambdas."""
    todo = list(ast.iter_child_nodes(node))
    while todo:
        n = todo.pop()
        yield n
        if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            todo.extend(ast.iter_child_nodes(n))


def _local_names(fn):
    names = {a.arg for a in fn.args.args + fn.args.kwonlyargs + getattr(fn.args, "posonlyargs", [])}
    for a in (fn.args.vararg, fn.args.kwarg):
        if a:
            names.add(a.arg)
    for n in _walk_same_function(fn):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            names.add(n.id)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            names |= {(a.asname or a.name).split(".")[0] for a in n.names}
    return names


def _is_nonempty_literal(n):
    return isinstance(n, ast.Constant) and not isinstance(n.value, bool) and n.value not in (None, 0, "", b"") and \
        isinstance(n.value, (str, bytes, int, float))


def check_python(rel, text):
    try:
        return _check_python(rel, text)
    except RecursionError:
        return []


def _check_python(rel, text):
    out = []
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return out
    parent = {}
    for node in ast.walk(tree):
        for ch in ast.iter_child_nodes(node):
            parent[ch] = node
    modules = {(a.asname or a.name).split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names} | \
              {(a.asname or a.name) for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
    kinds_of = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign):
            v = n.value
            if isinstance(v, ast.Call) and isinstance(v.func, ast.Attribute) and v.func.attr in NONE_RESULT:
                continue                        # `items = items.sort()` is the very mistake we look for - not evidence
            k = ("list" if isinstance(v, (ast.List, ast.ListComp)) or isinstance(v, ast.Call) and _dotted(v.func) in ("list", "sorted")
                 or isinstance(v, ast.Call) and isinstance(v.func, ast.Attribute) and v.func.attr in ("split", "splitlines", "readlines", "keys")
                 else "str" if isinstance(v, (ast.JoinedStr,)) or isinstance(v, ast.Constant) and isinstance(v.value, str)
                 or isinstance(v, ast.Call) and (_dotted(v.func) in ("input", "str") or isinstance(v.func, ast.Attribute) and
                                                 v.func.attr in ("strip", "lower", "upper", "read", "decode", "format", "join", "replace"))
                 else "other")
            for t in n.targets:
                if isinstance(t, ast.Name):
                    kinds_of.setdefault(t.id, set()).add(k)
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for a in n.args.args + n.args.kwonlyargs:
                ann = ast.unparse(a.annotation) if a.annotation else ""
                kinds_of.setdefault(a.arg, set()).add("str" if ann == "str" else "list" if ann.startswith(("list", "List")) else "param")
        elif isinstance(n, (ast.For, ast.comprehension)):
            for t in ast.walk(n.target):
                if isinstance(t, ast.Name):
                    kinds_of.setdefault(t.id, set()).add("other")

    def holds(name, kind):
        k = kinds_of.get(name, set())
        return kind in k and k <= {kind, "param"} if kind == "str" else k == {kind}

    uses_pandas = bool(re.search(r"^\s*(import pandas|from pandas|import polars)", text, re.M))
    module_funcs = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    module_assigned = {t.id for n in ast.walk(tree) if isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign))
                       for t in ([n.target] if not isinstance(n, ast.Assign) else n.targets) if isinstance(t, ast.Name)}

    def enclosing_fn(node):
        p = parent.get(node)
        while p is not None and not isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            p = parent.get(p)
        return p

    def in_try_body(node):
        child, p = node, parent.get(node)
        while p is not None and not isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
            if isinstance(p, ast.Try) and child in p.body and p.handlers:
                return True
            child, p = p, parent.get(p)
        return False

    tests = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.If, ast.While, ast.IfExp, ast.Assert)):
            tests.add(id(n.test))
        elif isinstance(n, ast.comprehension):
            tests |= {id(c) for c in n.ifs}

    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        # --- always-true "or"
        if id(node) in tests:
            cond = node.operand if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not) else node
            if isinstance(cond, ast.BoolOp) and isinstance(cond.op, ast.Or) and \
                    any(isinstance(v, ast.Compare) and isinstance(v.ops[0], (ast.Eq, ast.NotEq, ast.In)) for v in cond.values):
                lit = [v for v in cond.values if _is_nonempty_literal(v)]
                if lit:
                    add(out, rel, line, "py-or-literal", "`or %s` is always true" % ast.unparse(lit[0])[:40])
            if isinstance(cond, ast.Compare) and len(cond.comparators) == 1 and isinstance(cond.ops[0], (ast.Eq, ast.NotEq)) \
                    and isinstance(cond.comparators[0], ast.BoolOp) and isinstance(cond.comparators[0].op, ast.Or) \
                    and all(isinstance(v, ast.Constant) for v in cond.comparators[0].values):
                add(out, rel, line, "py-or-literal", "`== (%s)` only ever compares with the first value" % ast.unparse(cond.comparators[0])[:40])
            # --- method / function used without brackets
            for v in ([cond] + (cond.values if isinstance(cond, ast.BoolOp) else [])):
                v = v.operand if isinstance(v, ast.UnaryOp) and isinstance(v.op, ast.Not) else v
                if isinstance(v, ast.Attribute) and isinstance(v.value, ast.Name) and v.value.id == "self":
                    cls = parent.get(enclosing_fn(node))
                    if isinstance(cls, ast.ClassDef):
                        for d in cls.body:
                            if isinstance(d, (ast.FunctionDef, ast.AsyncFunctionDef)) and d.name == v.attr and not d.decorator_list \
                                    and not any((isinstance(t, ast.Attribute) and t.attr == v.attr or isinstance(t, ast.Name) and t.id == v.attr)
                                                and isinstance(t.ctx, ast.Store) for t in ast.walk(cls)):
                                add(out, rel, line, "py-method-not-called", "`self.%s` without ()" % v.attr)
                elif isinstance(v, ast.Name) and v.id in module_funcs and v.id not in module_assigned:
                    fns, fn = [], enclosing_fn(node)
                    while fn is not None:
                        fns.append(fn)
                        fn = enclosing_fn(fn)
                    if not any(v.id in _local_names(f) for f in fns if not isinstance(f, ast.Lambda)):
                        add(out, rel, line, "py-method-not-called", "`%s` is a function; without () it is always true" % v.id)
        if isinstance(node, (ast.Assign, ast.Return)) and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute):
            m = node.value.func.attr
            recv = node.value.func.value
            if isinstance(recv, ast.Name) and recv.id in modules:
                pass
            elif m in NONE_RESULT and isinstance(recv, ast.Name) and (holds(recv.id, "list") or (
                    m in ("sort", "append", "extend", "reverse") and not uses_pandas and kinds_of.get(recv.id) <= {"param", "list"}
                    and not (m in ("sort", "reverse") and node.value.args)
                    and not (m == "reverse" and node.value.keywords)
                    and all(k.arg in ("key", "reverse") for k in node.value.keywords)
                    and not re.search(r"(?i)df|frame|table|series|data|arr|tensor|index|matrix|query|qs$", recv.id) and not recv.id[:1].isupper()
                    and isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == recv.id for t in node.targets))):
                add(out, rel, line, "py-none-result", "`%s(...)` gives back None" % ast.unparse(node.value.func)[:50])
        if isinstance(node, (ast.Assign, ast.Return)) and isinstance(node.value, ast.Call) and (_dotted(node.value.func) == "random.shuffle" or
                _dotted(node.value.func) == "shuffle" and re.search(r"^\s*from random import [^\n]*\bshuffle\b", text, re.M)):
            add(out, rel, line, "py-none-result", "`shuffle(...)` gives back None")
        # --- result thrown away
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            c = node.value
            recv = c.func.value if isinstance(c.func, ast.Attribute) else None
            if isinstance(c.func, ast.Attribute) and not c.keywords and (
                    (c.func.attr in STR_NO_EFFECT and (isinstance(recv, ast.Constant) and isinstance(recv.value, str)
                                                       or isinstance(recv, ast.Name) and holds(recv.id, "str")
                                                       or isinstance(recv, ast.Name) and kinds_of.get(recv.id) == {"param"} and
                                                       c.func.attr in ("strip", "lstrip", "rstrip", "lower", "upper", "casefold")))
                    or (c.func.attr == "replace" and len(c.args) >= 2 and isinstance(recv, ast.Name) and holds(recv.id, "str"))) and \
                    not (isinstance(recv, ast.Name) and recv.id in modules):
                add(out, rel, line, "py-no-effect", "`%s(...)` - the result is thrown away" % ast.unparse(c.func)[:50])
            elif isinstance(c.func, ast.Name) and c.func.id in ("sorted", "reversed"):
                add(out, rel, line, "py-no-effect", "`%s(...)` - the result is thrown away" % c.func.id)
            elif isinstance(c.func, ast.Name) and re.fullmatch(r"[A-Z]\w*(Error|Exception)", c.func.id):
                add(out, rel, line, "py-exception-not-raised", "`%s(...)` without raise" % c.func.id)
        # --- methods
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and isinstance(parent.get(node), ast.ClassDef):
            a = node.args
            cls = parent.get(node)
            called_on_self = any(isinstance(x, ast.Call) and isinstance(x.func, ast.Attribute) and x.func.attr == node.name and
                                 isinstance(x.func.value, ast.Name) and x.func.value.id == "self" for x in ast.walk(cls))
            if not node.decorator_list and not a.args and not a.vararg and not getattr(a, "posonlyargs", []) and called_on_self:
                add(out, rel, line, "py-missing-self", "`def %s()` has no self" % node.name)
            if node.name == "__init__":
                for r in _walk_same_function(node):
                    if isinstance(r, ast.Return) and r.value is not None and not (isinstance(r.value, ast.Constant) and r.value.value is None) \
                            and not (isinstance(r.value, ast.Call) and isinstance(r.value.func, ast.Attribute) and r.value.func.attr == "__init__"):
                        add(out, rel, r.lineno, "py-return-in-init", "__init__ returns a value")
        # --- stale defaults
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for d in node.args.defaults + [x for x in node.args.kw_defaults if x is not None]:
                if isinstance(d, ast.Call) and STALE_CALLS.search(_dotted(d.func) or ""):
                    add(out, rel, d.lineno, "py-stale-default", "default `%s` is calculated only once" % ast.unparse(d)[:50])
            # --- a function that sometimes returns nothing
            rets = [r for r in _walk_same_function(node) if isinstance(r, ast.Return)]
            has_yield = any(isinstance(r, (ast.Yield, ast.YieldFrom)) for r in _walk_same_function(node))
            if rets and not has_yield and node.body and not _terminates(node.body) and \
                    all(r.value is not None and not (isinstance(r.value, ast.Constant) and r.value.value is None) for r in rets):
                last = node.body[-1]
                while isinstance(last, ast.If) and len(last.orelse) == 1 and isinstance(last.orelse[0], ast.If):
                    last = last.orelse[0]                       # follow the elif chain to its end
                chain, cur = [], node.body[-1]
                while isinstance(cur, ast.If):
                    chain.append(cur)
                    cur = cur.orelse[0] if len(cur.orelse) == 1 and isinstance(cur.orelse[0], ast.If) else None
                thresholds = len(chain) >= 2 and all(isinstance(c.test, ast.Compare) and all(isinstance(o, (ast.Lt, ast.LtE, ast.Gt, ast.GtE))
                                                                                         for o in c.test.ops) for c in chain)
                if thresholds and isinstance(last, ast.If) and not last.orelse and all(_terminates(c.body) for c in chain):
                    add(out, rel, line, "py-falls-through", "`%s` returns None when no branch matches" % node.name)
        # --- finally
        if isinstance(node, ast.Try) and node.finalbody:
            for s in node.finalbody:
                for n in [s] + list(_walk_same_function(s)):
                    if isinstance(n, (ast.Return, ast.Break, ast.Continue)):
                        if isinstance(n, (ast.Break, ast.Continue)) and any(isinstance(p, (ast.For, ast.While)) for p in _walk_same_function(s)):
                            continue            # belongs to a loop inside the finally block
                        add(out, rel, n.lineno, "py-return-in-finally", "`%s` inside finally" % type(n).__name__.lower())
        # --- unreachable
        for field in ("body", "orelse", "finalbody"):
            stmts = getattr(node, field, None)
            if isinstance(stmts, list) and stmts and isinstance(stmts[0], ast.stmt):
                for i, s in enumerate(stmts[:-1]):
                    if isinstance(s, (ast.Return, ast.Raise, ast.Continue, ast.Break)):
                        nxt = stmts[i + 1]
                        if not (isinstance(nxt, ast.Expr) and isinstance(nxt.value, (ast.Constant, ast.Yield, ast.YieldFrom, ast.Await))):
                            add(out, rel, nxt.lineno, "py-unreachable", "after `%s` on line %d" % (type(s).__name__.lower(), s.lineno))
                        break
        # --- missing f
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and "{" in node.value and not isinstance(parent.get(node), ast.JoinedStr):
            fn = enclosing_fn(node)
            p = parent.get(node)
            ph = re.findall(r"\{([A-Za-z_]\w*)(?:\.\w+|\[[^\]]*\])?(?:![rsa])?(?::[^{}]*)?\}", node.value)
            if fn is not None and ph and not re.search(r"\{\{|\}\}", node.value) and isinstance(p, ast.Call) and node in p.args \
                    and not p.keywords and not any(isinstance(x, ast.Dict) for x in p.args):   # template APIs fill {x} from a dict
                fname = p.func.attr if isinstance(p.func, ast.Attribute) else (p.func.id if isinstance(p.func, ast.Name) else "")
                if fname in LOGGERS or (isinstance(parent.get(p), ast.Raise)):
                    if all(x in _local_names(fn) for x in ph) and not (isinstance(p.func, ast.Attribute) and p.func.attr in ("format", "format_map")):
                        add(out, rel, node.lineno, "py-missing-f", "{%s} is printed as it is" % ph[0])
        # --- comparisons
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            l, op, r = node.left, node.ops[0], node.comparators[0]
            is_len = lambda x: isinstance(x, ast.Call) and isinstance(x.func, ast.Name) and x.func.id == "len"
            zero = lambda x: isinstance(x, ast.Constant) and x.value == 0 and not isinstance(x.value, bool)
            if (is_len(l) and zero(r) and isinstance(op, (ast.Lt, ast.GtE))) or (zero(l) and is_len(r) and isinstance(op, (ast.Gt, ast.LtE))):
                add(out, rel, line, "py-len-impossible", "`%s`" % ast.unparse(node)[:50])
            fl = [x for x in (l, r) if isinstance(x, ast.Constant) and isinstance(x.value, float)]
            if isinstance(op, (ast.Eq, ast.NotEq)) and fl and not float(fl[0].value * 64).is_integer():
                add(out, rel, line, "py-float-equal", "`%s`" % ast.unparse(node)[:50])
            if isinstance(op, ast.Gt) and zero(r) and isinstance(l, ast.Call) and isinstance(l.func, ast.Attribute) and l.func.attr in ("find", "index", "rfind"):
                add(out, rel, line, "py-find-gt-zero", "`%s`" % ast.unparse(node)[:50])
        # --- loops
        if isinstance(node, ast.For) and isinstance(node.target, ast.Name) and isinstance(node.iter, ast.Call) and _dotted(node.iter.func) == "range":
            a = node.iter.args
            end = a[-1] if len(a) in (1, 2) else None
            if isinstance(end, ast.BinOp) and isinstance(end.op, ast.Add) and isinstance(end.right, ast.Constant) and end.right.value == 1 \
                    and isinstance(end.left, ast.Call) and _dotted(end.left.func) == "len" and end.left.args and isinstance(end.left.args[0], ast.Name):
                seq, i = end.left.args[0].id, node.target.id
                if any(isinstance(s, ast.Subscript) and isinstance(s.value, ast.Name) and s.value.id == seq and isinstance(s.slice, ast.Name)
                       and s.slice.id == i for s in ast.walk(node)):
                    add(out, rel, line, "py-off-by-one", "range(len(%s) + 1) with %s[%s]" % (seq, seq, i))
        if isinstance(node, ast.For) and isinstance(node.iter, ast.Name) and not any(
                isinstance(x, ast.Name) and x.id == node.iter.id and isinstance(x.ctx, ast.Store) for x in _walk_same_function(node)):
            seq, target = node.iter.id, node.target.id if isinstance(node.target, ast.Name) else None
            loop_nodes = list(_walk_same_function(node))
            leaves = any(isinstance(x, (ast.Break, ast.Return, ast.Raise)) for x in loop_nodes)
            for c in loop_nodes:
                if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and isinstance(c.func.value, ast.Name) and c.func.value.id == seq:
                    if c.func.attr == "remove" and target and len(c.args) == 1 and isinstance(c.args[0], ast.Name) and c.args[0].id == target and not leaves:
                        add(out, rel, c.lineno, "py-modify-while-looping", "`%s.remove(%s)` while looping over %s - the next item is skipped" % (seq, target, seq))
                        break
        if isinstance(node, ast.While) and isinstance(node.test, ast.Compare) and \
                all(isinstance(o, (ast.Lt, ast.LtE, ast.Gt, ast.GtE)) for o in node.test.ops):
            parts = [node.test.left] + node.test.comparators
            simple = all(isinstance(x, (ast.Name, ast.Constant)) or (isinstance(x, ast.Call) and _dotted(x.func) == "len"
                         and len(x.args) == 1 and isinstance(x.args[0], ast.Name)) for x in parts)
            names = {x.id for x in parts if isinstance(x, ast.Name)}
            lens = {x.args[0].id for x in parts if isinstance(x, ast.Call) and x.args and isinstance(x.args[0], ast.Name)}
            body_nodes = [n for s in node.body + node.orelse for n in [s] + list(_walk_same_function(s))]
            changed = {n.id for n in body_nodes if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del))}
            touched = {n.value.id for n in body_nodes if isinstance(n, (ast.Attribute, ast.Subscript)) and isinstance(n.value, ast.Name)} | \
                      {a.id for n in body_nodes if isinstance(n, ast.Call) for a in n.args if isinstance(a, ast.Name)}
            stops = any(isinstance(n, (ast.Break, ast.Return, ast.Raise, ast.Yield, ast.YieldFrom, ast.Global, ast.Nonlocal, ast.Await))
                        or isinstance(n, ast.Call) and EXITS.search(_dotted(n.func) or "") for n in body_nodes)
            unknown_calls = any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id not in ("print", "len", "str", "int", "float")
                                for n in body_nodes)
            if simple and (names or lens) and not ((names | lens) & changed) and not (lens & touched) and not stops \
                    and not (lens and unknown_calls) \
                    and any(not isinstance(x, ast.Constant) for x in parts) and (names - {x.id for x in parts[1:] if isinstance(x, ast.Name)} or lens or names):
                left = parts[0]
                if isinstance(left, (ast.Name, ast.Call)):
                    add(out, rel, line, "py-loop-never-ends", "`while %s` - nothing inside the loop changes it" % ast.unparse(node.test)[:40])
        # --- input validation
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("int", "float") and node.args and \
                isinstance(node.args[0], ast.Call) and _dotted(node.args[0].func) == "input" and not in_try_body(node):
            add(out, rel, line, "py-input-unchecked", "`%s(input(...))` without try" % node.func.id)
        # --- bare except: pass
        if isinstance(node, ast.ExceptHandler) and node.type is None and all(isinstance(s, ast.Pass) or
                                                                            isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) for s in node.body):
            add(out, rel, node.lineno, "py-bare-except", "`except: pass`")
        # --- x = x
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and \
                isinstance(node.value, ast.Name) and node.targets[0].id == node.value.id and enclosing_fn(node) is not None:
            add(out, rel, line, "py-self-assign", "`%s = %s`" % (node.value.id, node.value.id))
        # --- list[a / b]
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.BinOp) and isinstance(node.slice.op, ast.Div) and not uses_pandas:
            if not isinstance(node.value, ast.Name) or not re.search(r"(?i)(df|frame|data|path|dir|root)", node.value.id):
                add(out, rel, line, "py-float-index", "`%s`" % ast.unparse(node)[:50])
    return out


# =================================================================================================== JavaScript / TypeScript
REGEX_BEFORE = set("(,=:[!&|?{};+-*%~^")
REGEX_WORDS = {"return", "typeof", "case", "in", "of", "new", "delete", "void", "throw", "else", "do", "await", "yield", "instanceof"}


def js_lex(text):
    """Returns (code, strings): code has comments blanked and the INSIDE of strings / templates / regexes blanked
    (quotes kept, newlines kept, same length), so patterns only ever see real code.  strings = [(start, end)] of
    every quoted text's inside, to read the real text back when a rule needs it."""
    out, strings, i, n = list(text), [], 0, len(text)
    last = ""                                   # last significant code character / word before the current position

    def blank(a, b):
        for k in range(a, b):
            if out[k] != "\n":
                out[k] = " "
    while i < n:
        c = text[i]
        nx = text[i + 1] if i + 1 < n else ""
        if c == "/" and nx == "/":
            j = text.find("\n", i)
            j = n if j < 0 else j
            blank(i, j)
            i = j
            continue
        if c == "/" and nx == "*":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            blank(i, j)
            i = j
            continue
        if c in "'\"`":
            j = i + 1
            while j < n and text[j] != c:
                if text[j] == "\\":
                    j += 1
                elif c != "`" and text[j] == "\n":
                    break
                j += 1
            strings.append((i + 1, min(j, n)))
            blank(i + 1, min(j, n))
            i = j + 1
            last = "a"
            continue
        if c == "/" and (last == "" or last in REGEX_BEFORE or last in REGEX_WORDS):
            j, cls = i + 1, False
            while j < n and text[j] != "\n":
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == "[":
                    cls = True
                elif text[j] == "]":
                    cls = False
                elif text[j] == "/" and not cls:
                    break
                j += 1
            if j < n and text[j] == "/":
                blank(i + 1, j)
                i = j + 1
                while i < n and text[i].isalpha():
                    i += 1
                last = "a"
                continue
        if c.isalnum() or c in "_$":
            j = i
            while j < n and (text[j].isalnum() or text[j] in "_$"):
                j += 1
            last = text[i:j]
            i = j
            continue
        if not c.isspace():
            last = c
        i += 1
    return "".join(out), strings


def _match_close(code, i):
    """code[i] is ( [ or { - index of the matching closer (or len)."""
    pairs = {"(": ")", "[": "]", "{": "}"}
    stack = []
    for j in range(i, len(code)):
        ch = code[j]
        if ch in pairs:
            stack.append(pairs[ch])
        elif ch in ")]}":
            if not stack or stack.pop() != ch:
                return len(code)
            if not stack:
                return j
    return len(code)


def _split_top(expr, sep):
    """Split expr on sep (e.g. '||') at bracket depth 0 -> [(offset, part)]."""
    parts, depth, start, i = [], 0, 0, 0
    while i < len(expr):
        ch = expr[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif depth == 0 and expr.startswith(sep, i):
            parts.append((start, expr[start:i]))
            i += len(sep)
            start = i
            continue
        i += 1
    parts.append((start, expr[start:]))
    return parts


JS_TYPEOF = {"unknown", "undefined", "object", "boolean", "number", "bigint", "string", "symbol", "function"}
JS_TYPOS = {"lenght": "length", "lengh": "length", "legnth": "length", "lenth": "length", "innerHtml": "innerHTML",
            "innerHtmL": "innerHTML", "textcontent": "textContent", "innertext": "innerText", "getElementByID": "getElementById",
            "getElementbyId": "getElementById", "getElementsByClass": "getElementsByClassName", "querySelectorall": "querySelectorAll",
            "queryselector": "querySelector", "addEventlistener": "addEventListener", "addeventlistener": "addEventListener",
            "preventdefault": "preventDefault", "stopPropogation": "stopPropagation", "toLowercase": "toLowerCase",
            "toUppercase": "toUpperCase", "tolowercase": "toLowerCase", "touppercase": "toUpperCase", "tostring": "toString",
            "indexof": "indexOf", "foreach": "forEach", "stringfy": "stringify", "Stringify": "stringify", "Parse": "parse",
            "appendchild": "appendChild", "removechild": "removeChild", "createelement": "createElement", "setattribute": "setAttribute",
            "getattribute": "getAttribute", "classlist": "classList", "onclik": "onclick", "subString": "substring", "charat": "charAt",
            "tofixed": "toFixed", "parseint": "parseInt", "parsefloat": "parseFloat", "Round": "round", "Floor": "floor", "Ceil": "ceil",
            "Random": "random", "Log": "log"}
NO_EFFECT_JS = {"trim", "trimStart", "trimEnd", "toUpperCase", "toLowerCase", "toLocaleUpperCase", "toLocaleLowerCase",
                "replace", "replaceAll", "slice", "concat", "filter", "substring", "substr", "padStart", "padEnd",
                "repeat", "toFixed", "split", "flat", "toSorted", "toReversed"}
HANDLER_FACTORY = re.compile(r"(bind|debounce|throttle|memo|Handler|^make|^create|^get|^use|^curry|^on[A-Z]|Callback)", re.I)


def _line(text, off):
    return text.count("\n", 0, off) + 1


def check_js(rel, text):
    try:
        return _check_js(rel, text)
    except RecursionError:
        return []


def _check_js(rel, text):
    out = []
    lines_ = text.split("\n")
    if max((len(x) for x in lines_), default=0) > 2000 or len(text) / max(1, len(lines_)) > 200:
        return out                                  # minified / bundled file - not written by a person
    code, strings = js_lex(text)
    is_jsx = rel.lower().endswith((".jsx", ".tsx")) or bool(re.search(r"<[A-Z][\w.]*[\s/>]|</\w+>", code))

    def string_at(pos):
        """Text inside the quote that starts at code[pos]."""
        for a, b in strings:
            if a == pos + 1:
                return text[a:b]
        return None

    # --- conditions: if (...) / while (...)
    for m in re.finditer(r"\b(if|while)\s*\(", code):
        start = m.end() - 1
        end = _match_close(code, start)
        cond = code[start + 1:end]
        line = _line(text, m.start())
        inner = cond.strip()
        if not (inner.startswith("(") and _match_close(inner, 0) == len(inner) - 1) and len(_split_top(cond, ",")) == 1:
            # ((x = y)) means "I know"; (a = 1, b = 2, ...) is generated code
            depth, k = 0, 0
            while k < len(cond):
                ch = cond[k]
                if ch in "([{":
                    depth += 1
                elif ch in ")]}":
                    depth -= 1
                elif ch == "=" and depth == 0:
                    prev, nxt = cond[k - 1] if k else "", cond[k + 1] if k + 1 < len(cond) else ""
                    rhs = re.split(r"&&|\|\||\)|,|;", cond[k + 1:])[0].strip()
                    plain = re.fullmatch(r"-?\d[\d.]*|(['\"])\s*\1|true|false|null|undefined", rhs) or \
                        (m.group(1) == "if" and re.fullmatch(r"[A-Za-z_$][\w$]*", rhs) and rhs not in ("this", "arguments"))
                    if prev not in "=!<>+-*/%&|^?" and nxt not in "=>" and plain:
                        add(out, rel, line, "js-assign-in-condition", "`%s`" % " ".join(text[start + 1:end].split())[:60])
                        break
                    if nxt == "=":
                        k += 2 if k + 2 < len(cond) and cond[k + 2] == "=" else 1
                k += 1
        ors = _split_top(cond, "||")
        if len(ors) > 1 and any(re.search(r"[=!]==?", p) for _, p in ors):
            for off, p in ors:
                ps = p.strip()
                q = re.fullmatch(r"(['\"])\s*\1", ps)              # a quoted text (its inside is blanked)
                if q:
                    real = string_at(start + 1 + off + p.index(ps[0]))
                    if real:
                        add(out, rel, line, "js-or-literal", "`|| \"%s\"` is always true" % real[:30])
                        break
                elif re.fullmatch(r"-?[1-9]\d*(\.\d+)?", ps):
                    add(out, rel, line, "js-or-literal", "`|| %s` is always true" % ps)
                    break
    # --- comparisons
    for m in re.finditer(r"[=!]==?\s*NaN\b|\bNaN\s*[=!]==?", code):
        add(out, rel, _line(text, m.start()), "js-nan-compare", "`%s`" % m.group(0))
    for m in re.finditer(r"\btypeof\s+[\w$.\[\]?]+\s*[=!]==?\s*(['\"])", code):
        s = string_at(m.end() - 1)
        if s is not None and s not in JS_TYPEOF:
            add(out, rel, _line(text, m.start()), "js-bad-typeof", "typeof ... === \"%s\"" % s[:20])
    for m in re.finditer(r"(['\"])\s*\1\s*[=!]==?\s*typeof\b", code):
        s = string_at(m.start())
        if s is not None and s not in JS_TYPEOF:
            add(out, rel, _line(text, m.start()), "js-bad-typeof", "\"%s\" === typeof ..." % s[:20])
    for m in re.finditer(r"[=!]==?\s*(\[\s*\]|\{\s*\})(?!\s*[.\[])", code):
        add(out, rel, _line(text, m.start()), "js-new-literal-compare", "`%s`" % m.group(0))
    for m in re.finditer(r"\.(indexOf|search|findIndex)\s*\(", code):
        end = _match_close(code, m.end() - 1)
        if re.match(r"\s*>\s*0(?![\d.])", code[end + 1:end + 8]):
            add(out, rel, _line(text, m.start()), "js-index-of-gt-zero", "`.%s(...) > 0`" % m.group(1))
    # --- typos in built-in names
    for m in re.finditer(r"\.\s*([A-Za-z]+)\b", code):
        w = m.group(1)
        if w in JS_TYPOS and len(re.findall(r"\.\s*%s\b" % re.escape(w), code)) < 3 and not re.match(r"\s*:", code[m.end():m.end() + 3]) and \
                not re.search(r"(this\.%s\s*=|\b%s\s*:|\b(get|set)\s+%s\b|^\s*%s\s*[=(;]|\b%s\s*\([^)]*\)\s*\{)" % ((re.escape(w),) * 5), code, re.M):
            if w in ("Parse", "Stringify", "stringfy") and not code[max(0, m.start() - 4):m.start()] == "JSON":
                continue
            if w in ("Round", "Floor", "Ceil", "Random") and not code[max(0, m.start() - 4):m.start()] == "Math":
                continue
            if w == "Log" and not code[max(0, m.start() - 7):m.start()] == "console":
                continue
            ln = text.split("\n")[_line(text, m.start()) - 1]
            if JS_TYPOS[w] in ln:
                continue                            # both spellings on purpose: cfg.innerHtml || cfg.innerHTML
            add(out, rel, _line(text, m.start()), "js-typo-property", "`.%s` should be `.%s`" % (w, JS_TYPOS[w]))
    for m in re.finditer(r"(['\"]\s*['\"]|\]|\b(?:\w*(?:list|items|arr|array|names|text|str|string|word|words|rows|values|keys|chars))\b)\s*\.length\s*\(", code, re.I):
        add(out, rel, _line(text, m.start()), "js-length-call", "`.length()`")
    # --- DOM lookups and events
    for m in re.finditer(r"\b(getElementById|getElementsByClassName|getElementsByTagName)\s*\(\s*(['\"])", code):
        s = string_at(m.end() - 1)
        if s and (m.group(1) == "getElementById" and s[:1] in "#." or m.group(1) == "getElementsByClassName" and s[:1] in ".#"):
            add(out, rel, _line(text, m.start()), "js-id-selector", "%s(\"%s\")" % (m.group(1), s[:30]))
    for m in re.finditer(r"\baddEventListener\s*\(\s*(['\"])", code):
        s = string_at(m.end() - 1)
        if s and re.fullmatch(r"on[a-z]+", s) and s not in ("online", "one"):
            add(out, rel, _line(text, m.start()), "js-on-event", "addEventListener(\"%s\")" % s)
    # --- return + line break
    for m in re.finditer(r"(?m)^([ \t]*)return[ \t]*\n(?:[ \t]*\n)*([ \t]+)(?=[\w$(\[{!'\"`-])", code):
        nxt = code[m.end():m.end() + 12]
        if len(m.group(2).expandtabs(4)) > len(m.group(1).expandtabs(4)) and not re.match(r"(case|default)\b", nxt):
            add(out, rel, _line(text, m.start()), "js-return-newline", "`return` alone on its line")
    # --- promises
    for m in re.finditer(r"\b(const|let|var)\s+([\w$]+)\s*=\s*fetch\s*\(", code):
        name = m.group(2)
        rest = code[m.end():m.end() + 3000]
        if not re.search(r"\b%s\s*\.\s*then\b|await\s+%s\b|return\s+%s\s*(;|\n|$)|Promise\.\w+\(" % ((re.escape(name),) * 3), rest) and \
                re.search(r"\b%s\s*\.\s*(json|text|ok|status|blob|headers)\b" % re.escape(name), rest):
            add(out, rel, _line(text, m.start()), "js-fetch-not-awaited", "`%s = fetch(...)` used without await" % name)
    for m in re.finditer(r"(?<![\w$.])fetch\s*\(", code):
        end = _match_close(code, m.end() - 1)
        before = code[max(0, m.start() - 12):m.start()]
        if re.match(r"\s*\.\s*(json|text|blob)\s*\(", code[end + 1:end + 20]) and "await" not in before:
            add(out, rel, _line(text, m.start()), "js-fetch-not-awaited", "`fetch(...).json()` - the Promise has no .json()")
    for m in re.finditer(r"\.forEach\s*\(\s*async\b", code):
        add(out, rel, _line(text, m.start()), "js-foreach-async", "`.forEach(async ...)`")
    for m in re.finditer(r"\buse(Layout)?Effect\s*\(\s*async\b", code):
        add(out, rel, _line(text, m.start()), "js-async-effect", "`useEffect(async ...)`")
    # --- handlers called instead of passed
    if is_jsx:
        for m in re.finditer(r"\bon[A-Z]\w*\s*=\s*\{\s*([A-Za-z_$][\w$.]*)\s*\(", code):
            end = _match_close(code, m.end() - 1)
            if re.match(r"\s*\}", code[end + 1:end + 4]) and not HANDLER_FACTORY.search(m.group(1).split(".")[-1]):
                add(out, rel, _line(text, m.start()), "js-handler-called", "`%s` runs while the page is drawn" % " ".join(text[m.start():end + 2].split())[:50])
    for m in re.finditer(r"\b(setTimeout|setInterval)\s*\(\s*([A-Za-z_$][\w$.]*)\s*\(", code):
        end = _match_close(code, m.end() - 1)
        if re.match(r"\s*,", code[end + 1:end + 4]) and m.group(2) not in ("function", "async") and not HANDLER_FACTORY.search(m.group(2).split(".")[-1]):
            add(out, rel, _line(text, m.start()), "js-handler-called", "`%s(%s(...), ...)` runs it now" % (m.group(1), m.group(2)))
    for m in re.finditer(r"\baddEventListener\s*\(\s*(['\"])\s*\1\s*,\s*([A-Za-z_$][\w$.]*)\s*\(", code):
        end = _match_close(code, m.end() - 1)
        if re.match(r"\s*[,)]", code[end + 1:end + 4]) and not HANDLER_FACTORY.search(m.group(2).split(".")[-1]):
            add(out, rel, _line(text, m.start()), "js-handler-called", "`addEventListener(..., %s(...))` runs it now" % m.group(2))
    # --- loops
    for m in re.finditer(r"\bfor\s*\(\s*(?:let|var)?\s*([\w$]+)\s*=\s*0\s*;\s*\1\s*<=\s*([\w$.]+)\.length\s*;", code):
        body = code[m.end():m.end() + 2000]
        if re.search(r"\b%s\s*\[\s*%s\s*\]" % (re.escape(m.group(2)), re.escape(m.group(1))), body):
            add(out, rel, _line(text, m.start()), "js-off-by-one", "`%s <= %s.length`" % (m.group(1), m.group(2)))
    # --- results thrown away (a statement that is only `x.trim()` etc.)
    for m in re.finditer(r"(?m)^[ \t]*([\w$]+(?:\.[\w$]+)*)\.(%s)\s*\(" % "|".join(NO_EFFECT_JS), code):
        end = _match_close(code, m.end() - 1)
        after = code[end + 1:end + 40]
        prev = code[:m.start()].rstrip()
        args_ = code[m.end():end]
        if m.group(2) in ("replace", "replaceAll") and re.search(r"\bfunction\b|=>", args_):
            continue                                # replace(re, fn) used to walk through matches
        if re.search(r"(?i)(location|router|history|navigation|navigator|nav|editor|doc|range|selection|cm|view)$", m.group(1)):
            continue                                # location.replace(url) navigates; editor.replace(...) edits
        if re.match(r"[ \t]*;?[ \t]*(\n|$|//)", after) and not re.match(r"\s*\.", after) and (prev == "" or prev[-1] in ";{}") \
                and m.group(1) not in ("this", "super", "console", "Object", "Array", "Promise", "window", "document", "$", "jQuery", "_", "app", "router"):
            add(out, rel, _line(text, m.start()), "js-no-effect", "`%s.%s(...)` - the result is thrown away" % (m.group(1)[:30], m.group(2)))
    for m in re.finditer(r"[\w$\]\)]\s+=([+-])\s+[\w$(]", code):
        add(out, rel, _line(text, m.start()), "js-plus-equals", "`=%s` - did you mean `%s=`?" % (m.group(1), m.group(1)))
    # --- switch with the same case twice
    for m in re.finditer(r"\bswitch\s*\(", code):
        pe = _match_close(code, m.end() - 1)
        bs = code.find("{", pe)
        if bs < 0:
            continue
        be = _match_close(code, bs)
        seen, depth, k = {}, 0, bs + 1
        for cm in re.finditer(r"[{}]|\bcase\s+((['\"])\s*\2|[\w$.-]+)\s*:", code[bs + 1:be]):
            tok = cm.group(0)
            if tok == "{":
                depth += 1
                continue
            if tok == "}":
                depth -= 1
                continue
            if depth:
                continue
            val = cm.group(1)
            if cm.group(2):
                val = repr(string_at(bs + 1 + cm.start(1)))
            if val in seen:
                add(out, rel, _line(text, bs + 1 + cm.start()), "js-duplicate-case", "case %s also on line %d" % (val[:30], seen[val]))
            else:
                seen[val] = _line(text, bs + 1 + cm.start())
    # --- months from 0
    for m in re.finditer(r"\bnew\s+Date\s*\(\s*(\d{4})\s*,\s*(\d{1,2})\s*[,)]", code):
        if int(m.group(2)) >= 12:
            add(out, rel, _line(text, m.start()), "js-bad-month", "new Date(%s, %s, ...) - month %s does not exist" % (m.group(1), m.group(2), m.group(2)))
    return out


# =================================================================================================== HTML
INPUT_TYPES = {"button", "checkbox", "color", "date", "datetime-local", "email", "file", "hidden", "image", "month", "number",
               "password", "radio", "range", "reset", "search", "submit", "tel", "text", "time", "url", "week", "datetime"}
SCRIPT_TYPES = {"", "text/javascript", "module", "application/javascript", "application/ecmascript", "text/ecmascript",
                "application/x-javascript", "text/jsx", "text/babel", "text/typescript", "importmap", "speculationrules"}
TEMPLATE_MARK = re.compile(r"\{\{|\{%|<%|\$\{|\[\[|@\{|\{#")
NUMBER_FIELD = re.compile(r"(?i)(qty|quantity|amount|price|total|count|age|weight|stock|units|seats|guests|tickets|items|pay)")


class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags = []              # (tag, [(name, value)], line)
        self.scripts = []           # (attrs, code, first line)
        self.form_depth = 0
        self.in_script = None
        self.buf = []
        self.decl = False

    def handle_decl(self, decl):
        if decl.lower().startswith("doctype"):
            self.decl = True

    def handle_starttag(self, tag, attrs):
        line = self.getpos()[0]
        self.tags.append((tag, attrs, line, self.form_depth))
        if tag == "form":
            self.form_depth += 1
        if tag == "script":
            self.in_script = (dict(attrs), line)
            self.buf = []

    def handle_startendtag(self, tag, attrs):
        self.tags.append((tag, attrs, self.getpos()[0], self.form_depth))

    def handle_endtag(self, tag):
        if tag == "form" and self.form_depth:
            self.form_depth -= 1
        if tag == "script" and self.in_script:
            self.scripts.append((self.in_script[0], "".join(self.buf), self.in_script[1]))
            self.in_script = None

    def handle_data(self, data):
        if self.in_script is not None:
            self.buf.append(data)


def check_html(rel, text):
    out = []
    p = _Page()
    try:
        p.feed(text)
        p.close()
    except Exception:
        return out, p
    templated = bool(TEMPLATE_MARK.search(text))
    ids = {}
    tagnames = [t[0] for t in p.tags]
    for tag, attrs, line, in_form in p.tags:
        names = [a for a, _ in attrs]
        dup = {a for a in names if names.count(a) > 1}
        for a in sorted(dup) if not templated else []:
            add(out, rel, line, "html-duplicate-attribute", "<%s %s=... %s=...>" % (tag, a, a))
        d = dict(attrs)
        i = (d.get("id") or "").strip()
        if i and not TEMPLATE_MARK.search(i):
            if i in ids and not templated:
                add(out, rel, line, "html-duplicate-id", "id=\"%s\" is also used on line %d" % (i[:30], ids[i]))
            ids.setdefault(i, line)
        if tag == "input":
            t = (d.get("type") or "text").strip().lower()
            if t not in INPUT_TYPES and not TEMPLATE_MARK.search(t):
                add(out, rel, line, "html-bad-input-type", "type=\"%s\"" % t[:20])
            if t == "number" and "min" not in d and NUMBER_FIELD.search((d.get("name") or "") + " " + (d.get("id") or "")):
                add(out, rel, line, "html-negative-number", "<input type=\"number\" name=\"%s\"> has no min" % (d.get("name") or d.get("id") or "")[:30])
        if tag == "button" and in_form and "type" not in d and any(a.startswith("on") for a in d):
            add(out, rel, line, "html-button-submits", "<button onclick=...> inside a form, without type")
        if tag == "script":
            t = (d.get("type") or "").strip().lower()
            if t not in SCRIPT_TYPES and re.search(r"java|script|ecma|modul", t):
                add(out, rel, line, "html-bad-script-type", "type=\"%s\"" % t[:30])
        if tag == "img" and "alt" not in d:
            add(out, rel, line, "html-img-alt", "<img src=\"%s\">" % (d.get("src") or "")[:40])
    if not templated:
        for tag, attrs, line, _ in p.tags:
            d = dict(attrs)
            if tag == "label" and d.get("for") and d["for"] not in ids:
                add(out, rel, line, "html-label-target", "<label for=\"%s\"> - no element has that id" % d["for"][:30])
    full_page = "html" in tagnames or "head" in tagnames
    if full_page and not templated:
        if not p.decl:
            add(out, rel, 1, "html-no-doctype", "")
        html_tag = next((t for t in p.tags if t[0] == "html"), None)
        if html_tag and "lang" not in dict(html_tag[1]):
            add(out, rel, html_tag[2], "html-no-lang", "")
        if "head" in tagnames:
            if not any(t[0] == "meta" and (dict(t[1]).get("name") or "").lower() == "viewport" for t in p.tags):
                add(out, rel, 1, "html-no-viewport", "")
            if "title" not in tagnames:
                add(out, rel, 1, "html-no-title", "")
    # inline scripts are JavaScript too
    for attrs, code, line in p.scripts:
        t = (attrs.get("type") or "").lower()
        if attrs.get("src") or t not in SCRIPT_TYPES or not code.strip():
            continue
        for f in check_js(rel + ".js", code):
            f["line"] += line - 1
            f["file"] = rel
            out.append(f)
    return out, p


# =================================================================================================== CSS
CSS_PROPS = set("""
accent-color align-content align-items align-self align-tracks all anchor-name animation animation-composition animation-delay
animation-direction animation-duration animation-fill-mode animation-iteration-count animation-name animation-play-state
animation-range animation-timeline animation-timing-function appearance aspect-ratio backdrop-filter backface-visibility background
background-attachment background-blend-mode background-clip background-color background-image background-origin background-position
background-position-x background-position-y background-repeat background-size block-size border border-block border-block-color
border-block-end border-block-end-color border-block-end-style border-block-end-width border-block-start border-block-start-color
border-block-start-style border-block-start-width border-block-style border-block-width border-bottom border-bottom-color
border-bottom-left-radius border-bottom-right-radius border-bottom-style border-bottom-width border-collapse border-color
border-end-end-radius border-end-start-radius border-image border-image-outset border-image-repeat border-image-slice
border-image-source border-image-width border-inline border-inline-color border-inline-end border-inline-end-color
border-inline-end-style border-inline-end-width border-inline-start border-inline-start-color border-inline-start-style
border-inline-start-width border-inline-style border-inline-width border-left border-left-color border-left-style border-left-width
border-radius border-right border-right-color border-right-style border-right-width border-spacing border-start-end-radius
border-start-start-radius border-style border-top border-top-color border-top-left-radius border-top-right-radius border-top-style
border-top-width border-width bottom box-decoration-break box-shadow box-sizing break-after break-before break-inside caption-side
caret-color clear clip clip-path clip-rule color color-interpolation color-interpolation-filters color-scheme column-count column-fill
column-gap column-rule column-rule-color column-rule-style column-rule-width column-span column-width columns contain
contain-intrinsic-block-size contain-intrinsic-height contain-intrinsic-inline-size contain-intrinsic-size contain-intrinsic-width
container container-name container-type content content-visibility counter-increment counter-reset counter-set cursor cx cy d direction
display dominant-baseline empty-cells fill fill-opacity fill-rule filter flex flex-basis flex-direction flex-flow flex-grow flex-shrink
flex-wrap float flood-color flood-opacity font font-display font-family font-feature-settings font-kerning font-language-override
font-optical-sizing font-palette font-size font-size-adjust font-stretch font-style font-synthesis font-variant font-variant-alternates
font-variant-caps font-variant-east-asian font-variant-ligatures font-variant-numeric font-variant-position font-variation-settings
font-weight forced-color-adjust gap grid grid-area grid-auto-columns grid-auto-flow grid-auto-rows grid-column grid-column-end
grid-column-gap grid-column-start grid-gap grid-row grid-row-end grid-row-gap grid-row-start grid-template grid-template-areas
grid-template-columns grid-template-rows hanging-punctuation height hyphenate-character hyphens image-orientation image-rendering
inline-size inset inset-block inset-block-end inset-block-start inset-inline inset-inline-end inset-inline-start isolation
justify-content justify-items justify-self left letter-spacing lighting-color line-break line-clamp line-height list-style
list-style-image list-style-position list-style-type margin margin-block margin-block-end margin-block-start margin-bottom
margin-inline margin-inline-end margin-inline-start margin-left margin-right margin-top marker marker-end marker-mid marker-start
mask mask-border mask-clip mask-composite mask-image mask-mode mask-origin mask-position mask-repeat mask-size mask-type math-depth
math-style max-block-size max-height max-inline-size max-width min-block-size min-height min-inline-size min-width mix-blend-mode
object-fit object-position offset offset-anchor offset-distance offset-path offset-position offset-rotate opacity order orphans outline
outline-color outline-offset outline-style outline-width overflow overflow-anchor overflow-block overflow-clip-margin overflow-inline
overflow-wrap overflow-x overflow-y overscroll-behavior overscroll-behavior-block overscroll-behavior-inline overscroll-behavior-x
overscroll-behavior-y padding padding-block padding-block-end padding-block-start padding-bottom padding-inline padding-inline-end
padding-inline-start padding-left padding-right padding-top page page-break-after page-break-before page-break-inside paint-order
perspective perspective-origin place-content place-items place-self pointer-events position position-anchor position-area
position-try print-color-adjust quotes r resize right rotate row-gap ruby-align ruby-position rx ry scale scroll-behavior scroll-margin
scroll-margin-block scroll-margin-bottom scroll-margin-inline scroll-margin-left scroll-margin-right scroll-margin-top scroll-padding
scroll-padding-block scroll-padding-bottom scroll-padding-inline scroll-padding-left scroll-padding-right scroll-padding-top
scroll-snap-align scroll-snap-stop scroll-snap-type scroll-timeline scrollbar-color scrollbar-gutter scrollbar-width
shape-image-threshold shape-margin shape-outside shape-rendering speak stop-color stop-opacity stroke stroke-dasharray
stroke-dashoffset stroke-linecap stroke-linejoin stroke-miterlimit stroke-opacity stroke-width tab-size table-layout text-align
text-align-last text-anchor text-combine-upright text-decoration text-decoration-color text-decoration-line text-decoration-skip-ink
text-decoration-style text-decoration-thickness text-emphasis text-emphasis-color text-emphasis-position text-emphasis-style
text-indent text-justify text-orientation text-overflow text-rendering text-shadow text-size-adjust text-transform text-underline-offset
text-underline-position text-wrap text-wrap-mode text-wrap-style top touch-action transform transform-box transform-origin
transform-style transition transition-behavior transition-delay transition-duration transition-property transition-timing-function
translate unicode-bidi user-select vector-effect vertical-align view-timeline view-transition-name visibility white-space
white-space-collapse widows width will-change word-break word-spacing word-wrap writing-mode x y z-index zoom src unicode-range
size marks bleed ascent-override descent-override line-gap-override size-adjust font-named-instance base-palette override-colors
syntax inherits initial-value navigation types field-sizing interpolate-size reading-flow text-spacing-trim text-autospace
""".split())
CSS_LENGTH_PROPS = set("""width height min-width min-height max-width max-height margin margin-top margin-right margin-bottom margin-left
padding padding-top padding-right padding-bottom padding-left top right bottom left font-size border-width border-radius gap row-gap
column-gap letter-spacing word-spacing outline-width outline-offset text-indent flex-basis border-top-width border-right-width
border-bottom-width border-left-width inset""".split())


def _typo_distance(a, b, limit=2):
    """Number of single-letter mistakes (wrong, missing, extra, or two letters swapped) between a and b."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev2, prev = None, list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if prev2 is not None and i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        prev2, prev = prev, cur
    return prev[-1]


def _closest_prop(prop):
    allowed = 1 if len(prop) <= 7 else 2
    best = min(CSS_PROPS, key=lambda k: (_typo_distance(prop, k, allowed), k))
    return best if _typo_distance(prop, best, allowed) <= allowed else None


def _css_strip(text):
    out = re.sub(r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group(0)), text, flags=re.S)
    return re.sub(r"(['\"])(?:\\.|(?!\1).)*\1", lambda m: m.group(1) + " " * (len(m.group(0)) - 2) + m.group(1), out)


def _decls(body, base_off):
    """[(offset, prop, value)] of the declarations in one { } block."""
    res, depth, start = [], 0, 0
    for k, ch in enumerate(body + ";"):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == ";" and depth <= 0:
            part = body[start:k]
            if ":" in part:
                prop, val = part.split(":", 1)
                if prop.strip() and re.fullmatch(r"\s*-?-?[A-Za-z][\w-]*\s*", prop):
                    res.append((base_off + start + (len(part) - len(part.lstrip())), prop.strip().lower(), val.strip()))
            start = k + 1
    return res


def check_css(rel, text):
    out = []
    code = _css_strip(text)
    if "#{" in code or re.search(r"(^|\n)\s*[$@][\w-]+\s*:", code):
        return out                                   # Sass / Less syntax inside a .css file - not plain CSS
    for m in re.finditer(r"\{([^{}]*)\}", code):
        body = m.group(1)
        decls = _decls(body, m.start(1))
        seen = {}
        for off, prop, val in decls:
            line = _line(text, off)
            v = re.sub(r"\s*!important\s*$", "", val, flags=re.I)
            if prop.startswith("-"):
                continue
            if prop not in CSS_PROPS:
                close = _closest_prop(prop)
                if close:
                    add(out, rel, line, "css-unknown-property", "`%s` - did you mean `%s`?" % (prop, close))
                continue
            for hm in re.finditer(r"(?<![\w-])#([0-9A-Za-z]+)\b", re.sub(r"url\([^)]*\)", "", v)):
                h = hm.group(1)
                if len(h) not in (3, 4, 6, 8) or not re.fullmatch(r"[0-9a-fA-F]+", h):
                    add(out, rel, line, "css-bad-color", "`%s: %s`" % (prop, v[:40]))
                    break
            if prop in CSS_LENGTH_PROPS and "(" not in v:
                bad = [t for t in v.split() if re.fullmatch(r"-?(\d+\.?\d*|\.\d+)", t) and float(t) != 0]
                if bad:
                    add(out, rel, line, "css-missing-unit", "`%s: %s` - %s what?" % (prop, v[:30], bad[0]))
            if prop in seen and seen[prop][1] != v and "(" not in v + seen[prop][1] and \
                    not re.search(r"-(webkit|moz|ms|o)-", v + seen[prop][1]) and "!important" not in (val + seen[prop][2]).lower():
                add(out, rel, line, "css-duplicate-property", "`%s` is also set on line %d (to %s)" % (prop, seen[prop][0], seen[prop][1][:20]))
            seen[prop] = (line, v, val)
    return out


# =================================================================================================== JSON
BUILD_DIRS = ("dist/", "build/", "lib/", "out/", "es/", "cjs/", "esm/", "umd/", ".next/", "public/build/")


def check_json(rel, text, project_root, files):
    out = []
    dups = []

    def hook(pairs):
        keys = [k for k, _ in pairs]
        for k in {k for k in keys if keys.count(k) > 1}:
            dups.append(k)
        return dict(pairs)
    try:
        data = json.loads(text, object_pairs_hook=hook)
    except ValueError:
        return out                                   # broken JSON is the syntax check's job
    for k in sorted(set(dups)):
        hits = [mm.start() for mm in re.finditer(r'"%s"\s*:' % re.escape(k), text)]
        add(out, rel, _line(text, hits[1]) if len(hits) > 1 else 1, "json-duplicate-key", "\"%s\"" % k[:40])
    if posixpath.basename(rel) == "package.json" and isinstance(data, dict):
        base = posixpath.dirname(rel)

        def exists(p):
            p = posixpath.normpath(posixpath.join(base, p.lstrip("./") if p.startswith("./") else p))
            return p in files or any(f.startswith(p.rstrip("/") + "/") for f in files) or \
                any(f == p + ext for f in files for ext in (".js", ".ts", ".mjs", ".cjs", "/index.js"))
        for key in ("main", "module", "browser"):
            v = data.get(key)
            if isinstance(v, str) and v and not v.lstrip("./").startswith(BUILD_DIRS) and not exists(v):
                hit = re.search(r'"%s"\s*:' % key, text)
                add(out, rel, _line(text, hit.start()) if hit else 1, "json-missing-file", "\"%s\": \"%s\"" % (key, v[:50]))
        for name, cmd in (data.get("scripts") or {}).items():
            if not isinstance(cmd, str):
                continue
            for sm in re.finditer(r"\b(?:node|nodemon|ts-node|tsx|python3?|deno run|bun)\s+(?:--?[\w-]+(?:=\S+)?\s+)*([\w./-]+\.(?:js|mjs|cjs|ts|py))\b", cmd):
                target = sm.group(1)
                if not target.lstrip("./").startswith(BUILD_DIRS) and not exists(target):
                    hit = re.search(r'"%s"\s*:' % re.escape(name), text)
                    add(out, rel, _line(text, hit.start()) if hit else 1, "json-missing-file", "script \"%s\" runs %s" % (name, target))
        nm = data.get("name")
        if isinstance(nm, str) and (nm != nm.lower() or " " in nm):
            hit = re.search(r'"name"\s*:', text)
            add(out, rel, _line(text, hit.start()) if hit else 1, "json-bad-package-name", "\"%s\"" % nm[:40])
        for sect in ("dependencies", "devDependencies"):
            for dep, ver in (data.get(sect) or {}).items():
                if isinstance(ver, str) and ver.strip() in ("*", "latest", "x", ""):
                    hit = re.search(r'"%s"\s*:' % re.escape(dep), text)
                    add(out, rel, _line(text, hit.start()) if hit else 1, "json-unpinned", "\"%s\": \"%s\"" % (dep, ver))
    return out


# =================================================================================================== Markdown
def _slug(h):
    h = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", h)                                    # pictures (badges) in headings
    h = re.sub(r"<[^>]+>|`|\*|\[([^\]]*)\]\([^)]*\)", lambda m: m.group(1) or "", h)
    h = re.sub(r"(?<![\w])_+(?=\S)|(?<=\S)_+(?![\w])", "", h).strip().lower()           # _emphasis_ is not part of the name
    h = re.sub(r"[^\w\- ]", "", h, flags=re.U)
    return h.replace(" ", "-")


def _md_parts(text):
    """(lines with code blocks/inline code blanked, [slugs of headings], unclosed fence line or None)"""
    lines = text.split("\n")
    out, slugs, counts = [], [], {}
    fence, fence_line = None, None
    for i, ln in enumerate(lines):
        fm = re.match(r"^ {0,3}(`{3,}|~{3,})", ln)
        if fence:
            out.append("")
            if fm and fm.group(1)[0] == fence[0] and len(fm.group(1)) >= len(fence) and not ln.strip()[len(fm.group(1)):].strip():
                fence = None
            continue
        if fm:
            fence, fence_line = fm.group(1), i + 1
            out.append("")
            continue
        clean = re.sub(r"`[^`\n]*`", lambda m: " " * len(m.group(0)), ln)
        out.append(clean)
        hm = re.match(r"^ {0,3}#{1,6}\s+(.+?)\s*#*\s*$", ln)
        if hm:
            s = _slug(hm.group(1))
            n = counts.get(s, 0)
            counts[s] = n + 1
            slugs.append(s if n == 0 else "%s-%d" % (s, n))
    for m in re.finditer(r"<[a-z][^>]*\b(?:name|id)\s*=\s*[\"']([^\"']+)", text, re.I):
        slugs.append(m.group(1).lower())
    for m in re.finditer(r"^\[\^([^\]]+)\]:", text, re.M):                              # footnotes
        slugs += ["fn-" + m.group(1).lower(), "user-content-fn-" + m.group(1).lower()]
    return out, slugs, (fence_line if fence else None)


def check_md(rel, text, files, slugs_of):
    out = []
    lines, slugs, open_fence = _md_parts(text)
    if open_fence:
        add(out, rel, open_fence, "md-unclosed-fence", "the ``` on line %d is never closed" % open_fence)
    base = posixpath.dirname(rel)
    has_headings = len(slugs) > 0
    for i, ln in enumerate(lines, 1):
        if has_headings and re.match(r"^#{1,6}[A-Za-z][^#\n]{2,}$", ln):
            add(out, rel, i, "md-heading-space", "`%s`" % ln[:40])
        for m in re.finditer(r"(?<!\])\[([^\]\n]+)\][ \t]+\((https?://[^)\s]+|\.{0,2}/?[\w./-]+\.(?:md|png|jpe?g|gif|svg|html|pdf))\)", ln):
            add(out, rel, i, "md-link-gap", "`[%s] (%s)`" % (m.group(1)[:20], m.group(2)[:30]))
        for m in re.finditer(r"!?\[(?:[^\[\]\n]|\[[^\]\n]*\])*\]\(([^)\n]*)\)", ln):
            raw = m.group(1).strip()
            if raw.startswith("<") and raw.endswith(">"):
                target = raw[1:-1]
            else:
                tm = re.match(r"^(\S+)(\s+([\"'(]).*)?$", raw)
                if not raw:
                    continue
                if not tm:
                    add(out, rel, i, "md-link-space", "`(%s)`" % raw[:40])
                    continue
                target = tm.group(1)
            if not target or re.match(r"^([a-z][a-z0-9+.-]*:|//)", target, re.I) or TEMPLATE_MARK.search(target):
                continue
            path, _, frag = target.partition("#")
            path = urllib.parse.unquote(path.split("?")[0])
            if not path:
                if frag and frag.lower() not in slugs and has_headings and not frag.lower().startswith(("fn-", "user-content-", "l")) or \
                        frag and frag.lower().startswith("l") and not re.fullmatch(r"l\d+(-l\d+)?", frag.lower()) and frag.lower() not in slugs and has_headings:
                    add(out, rel, i, "md-broken-anchor", "#%s" % frag[:40])
                continue
            full = posixpath.normpath(path.lstrip("/") if path.startswith("/") else posixpath.join(base, path))
            if full.startswith(".."):
                continue                             # outside the project (e.g. ../../issues on GitHub)
            if full not in files and not any(f.startswith(full.rstrip("/") + "/") for f in files):
                add(out, rel, i, "md-broken-link", "`%s` does not exist" % path[:50])
            elif frag and full.lower().endswith(".md") and full in slugs_of and slugs_of[full] and frag.lower() not in slugs_of[full]:
                add(out, rel, i, "md-broken-anchor", "%s#%s" % (path[:30], frag[:30]))
    return out


# =================================================================================================== across files
JS_LOOKUP = re.compile(r"""\b(?:getElementById\s*\(\s*(['"])([\w:-]+)\1|querySelector(?:All)?\s*\(\s*(['"])#([\w-]+)\3\s*\))""")
INLINE_HANDLER = re.compile(r"""\son[a-z]+\s*=\s*(["'])\s*([A-Za-z_$][\w$]*)\s*\(""", re.I)
GLOBAL_FUNCS = {"alert", "confirm", "prompt", "print", "close", "open", "setTimeout", "setInterval", "clearTimeout", "fetch",
                "eval", "parseInt", "parseFloat", "Number", "String", "Boolean", "encodeURIComponent", "decodeURIComponent",
                "scrollTo", "scroll", "blur", "focus", "stop", "postMessage", "requestAnimationFrame", "void", "if", "return"}


def check_project(texts, pages):
    """Rules that need the whole project: element ids looked up by scripts, functions called from onclick."""
    out = []
    web = {r: t for r, t in texts.items() if r.rsplit(".", 1)[-1].lower() in ("html", "htm", "js", "mjs", "cjs", "jsx", "ts", "tsx", "vue", "svelte")}
    has_markup = any(r.rsplit(".", 1)[-1].lower() in ("html", "htm", "jsx", "tsx", "vue", "svelte") for r in web)
    if has_markup:
        dynamic_ids = any(re.search(r"""\bid\s*=\s*\{(?!\s*["'])|\bid\s*=\s*["'][^"']*(\$\{|\{\{)|\.id\s*=\s*[^"'\s]|setAttribute\(\s*["']id["']\s*,\s*[^"'\s]""", t)
                          for t in web.values())
        if not dynamic_ids:
            all_web = "\n".join(web.values())
            for r, t in web.items():
                if r.rsplit(".", 1)[-1].lower() in ("html", "htm") and r not in pages:
                    continue
                for m in JS_LOOKUP.finditer(t):
                    name = m.group(2) or m.group(4)
                    pat = re.compile(r"""\bid\s*[=:]\s*\{?\s*["'`]%s["'`]|\.id\s*=\s*["'`]%s["'`]|setAttribute\(\s*["']id["']\s*,\s*["'`]%s["'`]|\bid=%s\b"""
                                     % ((re.escape(name),) * 4))
                    if not pat.search(all_web):
                        add(out, r, _line(t, m.start()), "html-missing-element", "#%s is not on any page" % name[:40])
    js_defs = "\n".join(t for r, t in web.items() if not r.lower().endswith((".html", ".htm"))) + "\n" + \
              "\n".join(code for p in pages.values() for _, code, _ in p.scripts)
    external = any(re.search(r"""<script[^>]+src\s*=\s*["'](https?:)?//""", t, re.I) for r, t in texts.items() if r.lower().endswith((".html", ".htm")))
    if not external:
        for r, t in texts.items():
            if not r.lower().endswith((".html", ".htm")) or TEMPLATE_MARK.search(t):
                continue
            for m in INLINE_HANDLER.finditer(t):
                fn = m.group(2)
                if fn in GLOBAL_FUNCS or t[m.end(2):m.end(2) + 1] == ".":
                    continue
                if not re.search(r"""\bfunction\s+%s\b|\b(?:const|let|var)\s+%s\b|\b%s\s*[:=]\s*(?:async\s*)?(?:function\b|\(|[\w$]+\s*=>)|window\.%s\s*=|\bclass\s+%s\b|^\s*(?:async\s+)?%s\s*\([^)]*\)\s*\{"""
                                 % ((re.escape(fn),) * 6), js_defs, re.M):
                    add(out, r, _line(t, m.start()), "html-missing-function", "%s() is not defined in any script" % fn)
    return out


# =================================================================================================== run
def check(root):
    project = dedupe.Project(root)
    files = set(project.files)
    texts, found, pages = {}, [], {}
    for rel, size in project.files.items():
        ext = rel.rsplit(".", 1)[-1].lower() if "." in rel else ""
        if ext not in ("py", "js", "mjs", "cjs", "jsx", "ts", "tsx", "html", "htm", "css", "json", "md", "markdown", "vue", "svelte") \
                or size > MAX_SIZE or rel.endswith((".min.js", ".min.css", ".d.ts")) or posixpath.basename(rel) in ("package-lock.json",):
            continue
        try:
            texts[rel] = (project.root / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
    slugs_of = {r: _md_parts(t)[1] for r, t in texts.items() if r.lower().endswith((".md", ".markdown"))}
    for rel, text in texts.items():
        ext = rel.rsplit(".", 1)[-1].lower()
        test = bool(TEST_PATH.search(rel))
        try:
            if ext == "py" and not test:
                found += check_python(rel, text)
            elif ext in ("js", "mjs", "cjs", "jsx", "ts", "tsx") and not test:
                found += check_js(rel, text)
            elif ext in ("html", "htm"):
                fs, page = check_html(rel, text)
                found += fs
                pages[rel] = page
            elif ext == "css":
                found += check_css(rel, text)
            elif ext == "json":
                found += check_json(rel, text, project.root, files)
            elif ext in ("md", "markdown"):
                found += check_md(rel, text, files, slugs_of)
        except Exception as e:                      # one odd file must never stop the whole check
            print("  (skipped %s: %s)" % (rel, type(e).__name__))
            continue
    try:
        found += check_project(texts, pages)
    except Exception:
        pass
    uniq, seen = [], set()
    for f in sorted(found, key=lambda f: (f["file"], f["line"], f["kind"])):
        key = (f["file"], f["line"], f["kind"], f["detail"])
        if key not in seen:
            seen.add(key)
            uniq.append(f)
    return len(texts), uniq


def main():
    ap = argparse.ArgumentParser(description="Arachnid bug hunter (looks only)")
    ap.add_argument("root", nargs="?", default=str(HERE.parent))
    ap.add_argument("--out", default=str(HERE / "reports" / "bugs-report.json"))
    ap.add_argument("--rules", action="store_true", help="list every rule")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    if a.rules:
        for k, (pile, title, why, todo) in RULES.items():
            print("%-28s [%s] %s\n%30s%s\n" % (k, pile, title, "", why))
        return 0
    print("Arachnid bug hunter - looking at:", Path(a.root).resolve())
    n, found = check(a.root)
    ledger.reset("bugs")
    for f in found:
        pile, title, why, todo = RULES[f["kind"]]
        what = title + (": " + f["detail"] if f["detail"] else "")
        print("\n  %s  line %d   [%s]  %s" % (f["file"], f["line"], "DECISION" if pile == "decision" else "IDEA", f["kind"]))
        print("     %s" % what)
        ledger.record("bugs", "found" if pile == "decision" else "idea", "warning" if pile == "decision" else "info",
                      f["file"], f["line"], what, why, todo, f["kind"])
    dec = sum(1 for f in found if RULES[f["kind"]][0] == "decision")
    print("\n---------------- Summary ----------------")
    print("Files read: %d    Hidden bugs to decide on: %d    Small ideas: %d" % (n, dec, len(found) - dec))
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps({"root": str(Path(a.root).resolve()), "findings": found}, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
