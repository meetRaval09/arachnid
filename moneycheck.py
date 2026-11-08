#!/usr/bin/env python3
"""
ARACHNID - MONEY SAFETY  (looks only, never changes anything)
=============================================================
    python moneycheck.py [folder]

A program can run without a single error and still let a customer pay 0, or pay a NEGATIVE amount (which can turn
into money going the wrong way).  No compiler or linter can see that, because "the amount must be above 0" is a rule
of the shop, not of the language.

This check finds the places where code SENDS a payment (charge, pay, checkout, purchase, capture, a payment
provider's ...create(...) call) with an amount, and looks at the code just before it: is that amount ever compared
with 0 (or 1, or a MIN_... constant) before the money moves?  If not, it asks a person - as a DECISION, never an
alarm, because the payment service or another file may already refuse such amounts.

It understands Python (exactly, through its syntax tree) and JavaScript / TypeScript (by reading the code with
comments and strings blanked out).  Test files are skipped: tests pay 0 or -1 on purpose.
"""
import argparse
import ast
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import dedupe  # noqa: E402
import ledger  # noqa: E402

PY_EXT = {"py"}
JS_EXT = {"js", "mjs", "cjs", "jsx", "ts", "tsx"}
MAX_SIZE = 2_000_000

PAY = {"pay", "payment", "payments", "charge", "charges", "checkout", "purchase", "capture", "payout", "payouts"}
PROVIDER_OBJ = {"order", "orders", "session", "sessions", "transfer", "transfers", "intent", "intents", "invoice", "invoices"}
PROVIDER_VERB = {"create", "confirm", "capture"}
# words that mean "look at / describe a payment", not "send money"
NOT_SENDING = {"get", "list", "fetch", "find", "show", "render", "display", "history", "status", "format", "validate",
               "validation", "check", "verify", "log", "print", "refund", "refunds", "cancel", "retrieve", "search", "is", "has",
               "calc", "calculate", "compute", "describe", "test", "mock", "label", "text", "button", "page", "form", "route",
               "url", "method", "methods", "option", "options", "icon", "modal", "screen", "view", "component", "summary",
               "details", "info", "receipt", "use", "email", "notify", "set", "toggle", "open", "close", "load", "read", "parse"}
AMOUNTISH = {"amount", "amounts", "total", "totals", "subtotal", "price", "prices", "cost", "costs", "sum", "payable",
             "qty", "quantity", "fee", "fees", "grand", "balance", "value", "cents", "paise", "money"}
TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|spec|specs|e2e|cypress|fixtures?|mocks?)(/|$)|(^|/)test_[^/]*$|"
                       r"_test\.py$|\.(test|spec)\.[jt]sx?$", re.I)


def tokens(name):
    """'stripe.paymentIntents.create' -> ['stripe', 'payment', 'intents', 'create']"""
    out = []
    for part in re.split(r"[._$\s]+", name):
        for t in re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+", part):
            out.append(t.lower())
    return out


def is_sending(name):
    t = tokens(name)
    if not t or any(x in NOT_SENDING for x in t):
        return False
    if any(x in PAY for x in t):
        return True
    return t[-1] in PROVIDER_VERB and any(x in PROVIDER_OBJ for x in t[:-1])


def amountish(word):
    return any(x in AMOUNTISH for x in tokens(word)) or word.lower() in ("cart", "basket")


def finding(rel, line, call, watched):
    names = ", ".join(sorted(watched)[:3])
    return {"file": rel, "line": line, "call": call,
            "what": "%s(...) sends money using %s, but nothing before it checks that the amount is above 0" % (call, names),
            "why": "a cart of 0, or a negative amount, could be paid or charged - the program runs fine, so no error shows it",
            "todo": "check the amount first (e.g. refuse when it is 0 or less), or mark this as intended if the payment "
                    "service already refuses such amounts"}


# --------------------------------------------------------------------------------------------------- Python
class _PyFile:
    def __init__(self, text):
        self.tree = ast.parse(text)
        self.parent = {}
        for node in ast.walk(self.tree):
            for ch in ast.iter_child_nodes(node):
                self.parent[ch] = node
        self.file_guards = self._guards(self.tree, None)
        self.file_validation = bool(re.search(r"\b(PositiveInt|PositiveFloat|condecimal\([^)]*g[te]\s*=|conint\([^)]*g[te]\s*=|"
                                              r"Field\([^)]*\bg[te]\s*=|MinValueValidator)", text))

    @staticmethod
    def dotted(f):
        parts = []
        while isinstance(f, ast.Attribute):
            parts.append(f.attr)
            f = f.value
        if isinstance(f, ast.Name):
            parts.append(f.id)
        elif isinstance(f, ast.Call):
            parts.append(_PyFile.dotted(f.func) or "call")
        return ".".join(reversed(parts))

    @staticmethod
    def names_in(node):
        out = set()
        for n in ast.walk(node):
            if isinstance(n, ast.Name):
                out.add(n.id)
            elif isinstance(n, ast.Attribute):
                out.add(n.attr)
        return out

    def watched(self, call):
        """The names that carry the amount into this payment call."""
        w = set()
        for kw in call.keywords:
            if kw.arg and amountish(kw.arg):
                w |= self.names_in(kw.value) or {kw.arg}
            elif kw.arg is None or amountish(kw.arg or ""):
                w |= {x for x in self.names_in(kw.value) if amountish(x)}
            else:
                w |= {x for x in self.names_in(kw.value) if amountish(x)}
        for a in call.args:
            if isinstance(a, ast.Dict):
                for k, v in zip(a.keys, a.values):
                    if isinstance(k, ast.Constant) and isinstance(k.value, str) and amountish(k.value):
                        w |= self.names_in(v) or {k.value}
            w |= {x for x in self.names_in(a) if amountish(x)}
        return {x for x in w if x not in ("self", "cls", "int", "float", "round", "Decimal", "str")}

    @staticmethod
    def _is_limit(n):
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.USub):
            n = n.operand
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
            return abs(n.value) <= 1
        if isinstance(n, ast.Name) and re.search(r"MIN|LIMIT|ZERO", n.id):
            return True
        return isinstance(n, ast.Attribute) and bool(re.search(r"MIN|LIMIT|ZERO", n.attr))

    def _guards(self, scope, before_line):
        """{name: line} of every 'name < 0'-style check (or max(0, name), or validate(name)) in this scope."""
        found = {}
        for n in ast.walk(scope):
            line = getattr(n, "lineno", 0)
            if before_line is not None and line >= before_line:
                continue
            names = set()
            if isinstance(n, ast.Compare) and all(isinstance(o, (ast.Lt, ast.LtE, ast.Gt, ast.GtE)) for o in n.ops):
                sides = [n.left] + list(n.comparators)
                if any(self._is_limit(s) for s in sides):
                    for s in sides:
                        if not self._is_limit(s):
                            names |= self.names_in(s)
            elif isinstance(n, ast.Call):
                fn = self.dotted(n.func).lower()
                if fn in ("max", "builtins.max") and any(self._is_limit(a) for a in n.args):
                    for a in n.args:
                        names |= self.names_in(a)
                elif re.search(r"(validat|ensure|assert|check|require|positive)", fn):
                    for a in list(n.args) + [k.value for k in n.keywords]:
                        names |= self.names_in(a)
            for x in names:
                found.setdefault(x, line)
        return found

    def scope_of(self, node):
        p = self.parent.get(node)
        while p is not None and not isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            p = self.parent.get(p)
        return p or self.tree

    def check(self, rel):
        out = []
        if self.file_validation:
            return out                      # amounts are validated by a schema (pydantic, Django validators)
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Call):
                continue
            name = self.dotted(node.func)
            if not name or not is_sending(name):
                continue
            if isinstance(self.parent.get(node), (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue                    # a decorator, not a payment
            w = self.watched(node)
            if not w:
                continue                    # no amount is passed here - nothing to judge
            local = self._guards(self.scope_of(node), node.lineno)
            if w & set(local) or w & set(self.file_guards):
                continue
            out.append(finding(rel, node.lineno, name, w))
        return out


# --------------------------------------------------------------------------------------------------- JavaScript / TypeScript
def blank_js(text):
    """Comments and string contents become spaces (same length, newlines kept), so code can be read safely."""
    out, i, n = list(text), 0, len(text)
    while i < n:
        c = text[i]
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            j = n if j < 0 else j
            for k in range(i, j):
                out[k] = " "
            i = j
        elif c == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            for k in range(i, j):
                if out[k] != "\n":
                    out[k] = " "
            i = j
        elif c in "'\"`":
            j = i + 1
            while j < n and text[j] != c:
                if text[j] == "\\":
                    j += 1
                elif c != "`" and text[j] == "\n":
                    break
                j += 1
            for k in range(i + 1, min(j, n)):
                if out[k] != "\n":
                    out[k] = " "
            i = j + 1
        else:
            i += 1
    return "".join(out)


JS_CALL = re.compile(r"(?<![\w$.])((?:[A-Za-z_$][\w$]*\s*\??\.\s*)*[A-Za-z_$][\w$]*)\s*\(")
JS_KEYWORDS = {"if", "for", "while", "switch", "catch", "function", "return", "typeof", "new", "await", "async", "super"}
JS_LIMIT = r"(?:-?\s*(?:0(?:\.0+)?|1)\b|[A-Z_]*(?:MIN|LIMIT|ZERO)[A-Z_]*)"
JS_FUNC_HEAD = re.compile(r"(=>\s*$|\bfunction\b[^{]*$|\)\s*(?::\s*[^{;]+)?$)")
JS_NOT_FUNC = re.compile(r"\b(if|for|while|switch|catch|with)\s*\([^{]*$")


def _close_paren(code, i):
    depth = 0
    for j in range(i, len(code)):
        if code[j] in "([{":
            depth += 1
        elif code[j] in ")]}":
            depth -= 1
            if depth == 0:
                return j
    return len(code) - 1


def _function_start(code, pos):
    """Index of the '{' that opens the function containing pos (0 if none)."""
    depth = 0
    for i in range(pos - 1, -1, -1):
        ch = code[i]
        if ch == "}":
            depth += 1
        elif ch == "{":
            if depth:
                depth -= 1
                continue
            head = code[max(0, i - 200):i].rstrip()
            if JS_FUNC_HEAD.search(head) and not JS_NOT_FUNC.search(head[-120:]):
                return i
    return 0


def _js_guarded(region, names):
    for nm in names:
        v = re.escape(nm)
        pat = (r"\b%s\b[\w$.\[\]()?]*\s*(?:<=|<|>=|>)\s*%s|%s\s*(?:<=|<|>=|>)\s*[\w$.()]*\b%s\b|"
               r"Math\.max\s*\([^)]*\b%s\b|\b\w*(?:[vV]alidat|[eE]nsure|[aA]ssert|[cC]heck|[rR]equire|[pP]ositive)\w*\s*\([^)]*\b%s\b"
               % (v, JS_LIMIT, JS_LIMIT, v, v, v))
        if re.search(pat, region):
            return True
    return False


def check_js(rel, text):
    code = blank_js(text)
    if re.search(r"\.(positive|nonnegative)\s*\(|\.(min|gt|gte)\s*\(\s*(0|1)\b", code):
        return []                           # a schema (zod, joi, yup) already insists on a positive amount
    out = []
    for m in JS_CALL.finditer(code):
        name = re.sub(r"\s+|\?", "", m.group(1))
        first = name.split(".")[0]
        if first in JS_KEYWORDS or not is_sending(name):
            continue
        before = code[max(0, m.start() - 30):m.start()]
        if re.search(r"\bfunction\s*\*?\s*$|\b(def|class)\s+$", before):
            continue                        # this is where the function is DEFINED, not a payment
        end = _close_paren(code, m.end() - 1)
        if re.match(r"\s*(?::\s*[^{;=]+)?\{", code[end + 1:end + 80]) and not re.search(r"[=(,:]\s*$|\breturn\s*$|\bawait\s*$", before):
            continue                        # a method definition:  async charge(amount) { ... }
        args_raw = text[m.end():end]        # the real text (string keys like "amount" are needed)
        args = code[m.end():end]
        watched = set()
        for km in re.finditer(r"""["']?([A-Za-z_$][\w$]*)["']?\s*:\s*([^,}]+)""", args_raw):
            if amountish(km.group(1)):
                watched |= {w for w in re.findall(r"[A-Za-z_$][\w$]*", km.group(2)) if w not in ("Math", "round", "Number", "parseInt", "parseFloat", "Math.round")} or {km.group(1)}
        watched |= {w for w in re.findall(r"[A-Za-z_$][\w$]*", args) if amountish(w)}
        watched -= {"Math", "round", "Number", "parseInt", "parseFloat", "toFixed"}
        if not watched:
            continue
        start = _function_start(code, m.start())
        if _js_guarded(code[start:m.start()], watched) or _js_guarded(code, watched):
            continue
        out.append(finding(rel, text.count("\n", 0, m.start()) + 1, name, watched))
    return out


# --------------------------------------------------------------------------------------------------- run
def check(root):
    project = dedupe.Project(root)
    found, read = [], 0
    for rel, size in project.files.items():
        ext = rel.rsplit(".", 1)[-1].lower() if "." in rel else ""
        if ext not in PY_EXT | JS_EXT or size > MAX_SIZE or TEST_PATH.search(rel) or rel.endswith(".min.js"):
            continue
        try:
            text = (project.root / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if not re.search(r"(?i)pay|charge|checkout|purchase|capture|order", text):
            continue
        read += 1
        try:
            found += _PyFile(text).check(rel) if ext in PY_EXT else check_js(rel, text)
        except (SyntaxError, ValueError, RecursionError):
            continue                        # broken files are the syntax check's job
    return read, found


def main():
    ap = argparse.ArgumentParser(description="Arachnid money safety check (looks only)")
    ap.add_argument("root", nargs="?", default=str(HERE.parent))
    ap.add_argument("--out", default=str(HERE / "reports" / "money-report.json"))
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    print("Arachnid money safety - looking at:", Path(a.root).resolve())
    read, found = check(a.root)
    ledger.reset("money")
    for f in found:
        print("\n  %s  line %d   [DECISION]" % (f["file"], f["line"]))
        print("     payment : %s(...)" % f["call"])
        print("     problem : %s" % f["what"])
        ledger.record("money", "found", "warning", f["file"], f["line"], f["what"], f["why"], f["todo"], "unchecked-amount")
    print("\n---------------- Summary ----------------")
    print("Files with payment code read: %d    Payments without an amount check: %d" % (read, len(found)))
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps({"root": str(Path(a.root).resolve()), "findings": found}, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
