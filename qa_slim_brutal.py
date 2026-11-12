#!/usr/bin/env python3
"""
ARACHNID - BRUTAL SLIM TEST: shorter code, the SAME product?
============================================================
    python qa_slim_brutal.py

A small project written the long, AI-style way (Python, JavaScript and TypeScript), full of code that can be
shorter - and full of TRAPS: code that LOOKS like it can be shortened but would then behave differently
(float sums, a loop variable used after the loop, `x or default`, reading a getter twice, a dropped await, a
comment someone left, ...).  Then the full real flow runs:  slim -> approve all -> apply-approved.

Passes only if:
  1. every expected shorter version is found and applied,
  2. not one trap is touched,
  3. the old and the new project behave IDENTICALLY: every function is called side by side with hundreds of
     inputs, including objects whose comparisons return strange values - results, result types, errors, printed
     output and changes to the inputs must all match,
  4. undo puts every file back byte for byte,
  5. with an AI (a scripted stand-in, so the test is repeatable): the good answers are kept (one PROVEN, one
     TESTED) and every wrong answer is thrown away.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = sys.executable

SHOP_PY = '''"""AI-style verbose code."""
from contextlib import suppress


def is_adult(age):
    if age >= 18:
        return True
    else:
        return False


def has_items(cart):
    if cart.items is not None:
        return True
    return False


def is_empty(cart):
    if len(cart) > 0:
        return False
    else:
        return True


def label(n):
    if n > 1:
        word = "items"
    else:
        word = "item"
    return f"{n} {word}"


def doubled(prices):
    result = []
    for p in prices:
        result.append(p * 2)
    return result


def cheap(prices):
    out = []
    for p in prices:
        if p < 100:
            out.append(p)
    return out


def contains_banned(words, banned):
    for w in words:
        if w in banned:
            return True
    return False


def all_positive(nums):
    for n in nums:
        if not n > 0:
            return False
    return True


def total_price(items):
    total = sum(i.price for i in items)
    return total


def log_it(msg):
    print(msg)
    return None


def shipping(country):
    if country == "IN":
        return 0
    else:
        cost = 99
        return cost


def same(a, b):
    if a == b:
        return True
    return False


class Cart:
    def __init__(self, items):
        self.items = items

    def has_item(self, name):
        for it in self.items:
            if it == name:
                return True
        return False


# ---------------- traps: none of these may change
def trap_loop_var_used_after(xs):
    out = []
    for x in xs:
        out.append(x)
    print(x)
    return out


def trap_self_reference(xs):
    out = []
    for x in xs:
        out.append(len(out) + x)
    return out


def trap_try(xs):
    out = []
    try:
        for x in xs:
            out.append(1 / x)
    except ZeroDivisionError:
        return out
    return out


def trap_comment(age):
    if age >= 18:  # legal age in India
        return True
    else:
        return False


def trap_twice_used(a):
    r = a * 2
    print(r)
    return r


def trap_float_sum(xs):
    total = 0
    for x in xs:
        total += x
    return total


def trap_or_default(code):
    if code is None:
        code = "NONE"
    return code


def trap_suppress(xs):
    out = []
    with suppress(ZeroDivisionError):
        for x in xs:
            out.append(1 / x)
    return out


def trap_len_check(xs):
    if len(xs) == 0:
        return "empty"
    return "full"


class Basket:
    tax = 5
    vals = []
    for v in range(3):
        vals.append(v + tax)


def trap_shadow(xs):
    any = print
    for x in xs:
        if x:
            return True
    return False
'''

CART_JS = '''export function isAdult(age) {
  if (age >= 18) {
    return true;
  } else {
    return false;
  }
}

export function hasCoupon(cart) {
  if (cart.coupon) {
    return true;
  }
  return false;
}

export function total(items) {
  const sum = items.reduce((a, b) => a + b.price, 0);
  return sum;
}

export const double = (x) => {
  return x * 2;
};

export function isMissing(v) {
  return v === null || v === undefined;
}

export function isFree(price) {
  return price === 0 ? true : false;
}

export function shipping(country) {
  if (country === "IN") {
    return 0;
  } else {
    return 99;
  }
}

// traps: none of these may change
export function trapGetter(o) {
  if (o.x) {
    return o.x;
  }
  return 5;
}

export async function trapAwait(u) {
  const res = await fetch(u);
  return await res.json();
}

export function trapOrDefault(n) {
  if (n === undefined) {
    n = 10;
  }
  return n;
}

export function trapLoop(xs) {
  const out = [];
  for (const x of xs) {
    out.push(x * 2);
  }
  return out;
}
'''

PRICE_TS = '''export function isExpensive(price: number): boolean {
  if (price > 1000) {
    return true;
  } else {
    return false;
  }
}

export const withTax = (price: number): number => {
  return price * 1.18;
};
'''

# (file, the function the shorter version must be in, rule)
EXPECTED = [
    ("shop.py", "is_adult", "bool-return"), ("shop.py", "has_items", "bool-return"), ("shop.py", "is_empty", "bool-return"),
    ("shop.py", "label", "ternary-assign"), ("shop.py", "doubled", "list-comprehension"), ("shop.py", "cheap", "list-comprehension"),
    ("shop.py", "contains_banned", "any-all"), ("shop.py", "all_positive", "any-all"), ("shop.py", "total_price", "return-variable"),
    ("shop.py", "log_it", "trailing-return"), ("shop.py", "shipping", "else-after-return"), ("shop.py", "same", "bool-return"),
    ("shop.py", "has_item", "any-all"),
    ("web/cart.js", "isAdult", "js-bool-return"), ("web/cart.js", "hasCoupon", "js-bool-return"), ("web/cart.js", "total", "js-return-variable"),
    ("web/cart.js", "double", "js-arrow-body"), ("web/cart.js", "isMissing", "js-null-check"), ("web/cart.js", "isFree", "js-ternary-bool"),
    ("web/cart.js", "shipping", "js-else-after-return"),
    ("web/price.ts", "isExpensive", "js-bool-return"), ("web/price.ts", "withTax", "js-arrow-body"),
]
TRAPS_PY = ["trap_loop_var_used_after", "trap_self_reference", "trap_try", "trap_suppress", "trap_comment", "trap_twice_used", "trap_float_sum",
            "trap_or_default", "trap_len_check", "Basket", "trap_shadow"]
TRAPS_JS = ["trapGetter", "trapAwait", "trapOrDefault", "trapLoop"]

PY_COMPARE = r'''
import copy, contextlib, importlib.util, io, json, re, sys
def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
old, new = load(sys.argv[1], "old_shop"), load(sys.argv[2], "new_shop")
class Thing(list):
    def __init__(self, items=None, data=()): super().__init__(data); self.items = items
class Item:
    def __init__(self, p): self.price = p
class Weird:                            # comparisons that do NOT return True / False
    def __ge__(self, o): return "yes"
    def __gt__(self, o): return 0
    def __eq__(self, o): return [1]
    def __hash__(self): return 1
    def __bool__(self): return True
pool = [0, 1, -1, 17, 18, 19, 2, 0.5, float("nan"), Weird(), "", "a", None, True]
lists = [[], [1, 2, 3], [150, 50, 99, 100], [0], [-1, 5], [1.5, 2.5], [0.1, 0.2, 0.3], ["x", "b"], [Weird()], [0, 0]]
cases = {
 "is_adult": [(v,) for v in pool], "has_items": [(Thing(i),) for i in (None, 0, [], [1])],
 "is_empty": [(Thing(None, d),) for d in ([], [1], [1, 2])], "label": [(v,) for v in (0, 1, 2, 5, 1.5)],
 "doubled": [(l,) for l in lists], "cheap": [(l,) for l in lists],
 "contains_banned": [(w, b) for w in lists for b in ([], [1], {2, 3}, "abc", [Weird()])],
 "all_positive": [(l,) for l in lists], "total_price": [([Item(p) for p in l],) for l in ([], [1, 2], [0.1, 0.2])],
 "log_it": [("hi",), (None,)], "shipping": [(c,) for c in ("IN", "CA", "", None)],
 "same": [(a, b) for a in pool for b in (1, Weird(), "a", None)],
}
A = lambda s: re.sub(r" at 0x[0-9a-fA-F]+", "", s)
def run(f, args):
    buf = io.StringIO(); a = copy.deepcopy(args)
    with contextlib.redirect_stdout(buf):
        try: r = f(*a); res = ("ok", A(repr(r)), type(r).__name__)
        except Exception as e: res = ("raise", type(e).__name__)
    return res, buf.getvalue(), A(repr(a))
n = bad = 0
out = []
for name, cs in cases.items():
    for c in cs:
        n += 1
        x, y = run(getattr(old, name), c), run(getattr(new, name), c)
        if x != y: bad += 1; out.append("%s%r: %s -> %s" % (name, c, x, y))
for items in ([], ["a"], ["a", "b"], [Weird()]):
    for q in ("a", "z", Weird()):
        n += 1
        x, y = run(lambda i, w: old.Cart(i).has_item(w), (items, q)), run(lambda i, w: new.Cart(i).has_item(w), (items, q))
        if x != y: bad += 1; out.append("Cart.has_item: %s -> %s" % (x, y))
print(json.dumps({"calls": n, "differences": bad, "examples": out[:3]}))
'''

JS_COMPARE = r'''
const fs = require("fs"), vm = require("vm");
function load(f) {
  const src = fs.readFileSync(f, "utf8").replace(/export /g, "").replace(/: (number|boolean)/g, "");
  const ctx = { fetch: async () => ({ json: async () => 1 }) };
  vm.createContext(ctx);
  vm.runInContext(src + ";this.__m={" + process.argv[3] + "}", ctx);
  return ctx.__m;
}
const a = load(process.argv[1]), b = load(process.argv[2]);
let calls = 0, bad = 0; const ex = [];
const vals = [0, 1, -1, 17, 18, 19, 1000, 1001, "", "IN", "CA", null, undefined, NaN, {}, { coupon: 1 }, { coupon: 0 },
  { x: 0 }, { x: 3 }, [], [{ price: 1 }, { price: 2.5 }], true, false, "0"];
for (const k of Object.keys(a)) for (const v of vals) {
  calls++;
  let x, y;
  try { x = JSON.stringify([a[k](v)]) + typeof a[k](v); } catch (e) { x = "E:" + e.name; }
  try { y = JSON.stringify([b[k](v)]) + typeof b[k](v); } catch (e) { y = "E:" + e.name; }
  if (x !== y) { bad++; if (ex.length < 3) ex.push(k + ": " + x + " -> " + y); }
}
console.log(JSON.stringify({ calls, differences: bad, examples: ex }));
'''

MOCK_AI = r'''
import re, sys
prompt = sys.stdin.read()
name = re.findall(r"def (\w+)\(", prompt.split("=== THE FUNCTION")[1])[0]
answers = {
 "count_vowels": 'def count_vowels(text):\n    return sum(1 for ch in text.lower() if ch in "aeiou")\n',
 "average": 'def average(prices):\n    return sum(prices) / len(prices) if prices else 0\n',
 "discount_code": 'def discount_code(code=None):\n    return (code or "NONE").upper().strip()\n',
 "is_premium": 'def is_premium(customer):\n    """A customer who spent a lot, for over a year, and is not blocked."""\n    return bool(\n        customer.total_spent_this_year > 50000 and customer.account_age_in_days > 365 and not customer.is_blocked\n    )\n',
 "price_after_coupon": 'def price_after_coupon(price, code):\n    if code == "WELCOME":\n        return price * 0.9\n    return price / 2 if code == "HALF" else price\n',
 "unique_sorted": 'def unique_sorted(values):\n    return list(set(values))\n',
}
print("```python\n" + answers[name] + "```" if name in answers else "UNCHANGED")
'''

STORE_PY = '''"""A small shop module, written the long way."""


def count_vowels(text):
    count = 0
    for ch in text.lower():
        if ch in "aeiou":
            count = count + 1
        else:
            pass
    return count


def average(prices):
    if len(prices) == 0:
        return 0
    total = 0
    for p in prices:
        total += p
    return total / len(prices)


def discount_code(code=None):
    if code is None:
        code = "NONE"
    result = code.upper()
    result = result.strip()
    return result


def is_premium(customer):
    """A customer who spent a lot, for over a year, and is not blocked."""
    if customer.total_spent_this_year > 50000 and customer.account_age_in_days > 365 and not customer.is_blocked:
        return True
    else:
        return False


def price_after_coupon(price, code):
    if code == "WELCOME":
        return price * 0.9
    if code == "SECRET-2026-VIP":
        return price * 0.5
    if code == "HALF":
        return price / 2
    return price


def unique_sorted(values):
    seen = []
    for v in values:
        if v not in seen:
            seen.append(v)
    seen.sort()
    return seen
'''
AI_GOOD = {"count_vowels": "tested", "is_premium": "proven"}
AI_BAD = ["average", "discount_code", "price_after_coupon", "unique_sorted"]


def sh(args, env=None):
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, cwd=str(HERE))


def function_text(text, name):
    import ast
    for n in ast.walk(ast.parse(text)):
        if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name == name:
            return ast.get_source_segment(text, n)
    return None


def js_function_text(text, name):
    import re
    m = re.search(r"(export )?(async )?(function %s\b|const %s =)" % (name, name), text)
    if not m:
        return None
    import re as _re
    e = _re.compile(r"\n\};?\n").search(text, m.start())
    return text[m.start():e.end()] if e else None


def main():
    tmp = Path(tempfile.mkdtemp(prefix="arachnid-slim-brutal-"))
    keep = tmp / "reports-backup"
    if (HERE / "reports").exists():
        shutil.copytree(HERE / "reports", keep)
    env = dict(os.environ)
    ok = True
    try:
        proj = tmp / "proj"
        (proj / "web").mkdir(parents=True)
        files = {"shop.py": SHOP_PY, "web/cart.js": CART_JS, "web/price.ts": PRICE_TS}
        for rel, t in files.items():
            (proj / rel).write_text(t, encoding="utf-8")
        # ------------------------------------------------------------------ 1-4: no AI
        r = sh([PY, str(HERE / "slim.py"), str(proj), "--out", str(tmp / "slim.json")], env)
        if "skipped - the proof needs esbuild" in r.stdout:
            print("This test needs esbuild for the JavaScript / TypeScript part (npm install esbuild, or set ARACHNID_ESBUILD_DIR).")
            return 1
        rep = json.loads((tmp / "slim.json").read_text(encoding="utf-8"))
        props = rep["proposals"]
        print("1. EXPECTED SHORTER VERSIONS (each must be found)")
        found = 0
        for rel, fname, rule in EXPECTED:
            text = files[rel]
            body = function_text(text, fname) if rel.endswith(".py") else js_function_text(text, fname)
            start = text.find(body) if body else -1
            hit = [p for p in props if p["file"] == rel and p["kind"] == rule and start >= 0
                   and any(start <= pt["at"] < start + len(body) for pt in p["patches"])]
            found += bool(hit)
            print("   %-6s %-13s %-18s %s" % ("FOUND" if hit else "MISSED", rel, fname, rule))
        ok &= found == len(EXPECTED)
        extra = sum(len(p["patches"]) for p in props) - found
        sh([PY, str(HERE / "proposals.py"), "approve", "all"], env)
        r = sh([PY, str(HERE / "proposals.py"), "apply", "--root", str(proj)], env)
        new = {rel: (proj / rel).read_text(encoding="utf-8") for rel in files}
        print("\n2. TRAPS (none may change)")
        touched = 0
        for name in TRAPS_PY:
            same = function_text(SHOP_PY, name) == function_text(new["shop.py"], name)
            touched += not same
            print("   %-9s shop.py      %s" % ("UNTOUCHED" if same else "CHANGED!", name))
        for name in TRAPS_JS:
            same = js_function_text(CART_JS, name) == js_function_text(new["web/cart.js"], name)
            touched += not same
            print("   %-9s web/cart.js  %s" % ("UNTOUCHED" if same else "CHANGED!", name))
        ok &= touched == 0
        print("\n3. SAME BEHAVIOUR (old and new called side by side)")
        (tmp / "old").mkdir()
        for rel, t in files.items():
            (tmp / "old" / Path(rel).name).write_text(t, encoding="utf-8")
        pr = sh([PY, "-c", PY_COMPARE, str(tmp / "old" / "shop.py"), str(proj / "shop.py")], env)
        pyres = json.loads(pr.stdout.strip().splitlines()[-1]) if pr.returncode == 0 else {"calls": 0, "differences": -1, "examples": [pr.stderr[-200:]]}
        names = "isAdult,hasCoupon,total,double,isMissing,isFree,shipping,trapGetter,trapOrDefault"
        jr = sh(["node", "-e", JS_COMPARE, str(tmp / "old" / "cart.js"), str(proj / "web/cart.js"), names], env)
        jsres = json.loads(jr.stdout.strip().splitlines()[-1]) if jr.returncode == 0 else {"calls": 0, "differences": -1, "examples": [jr.stderr[-200:]]}
        tr = sh(["node", "-e", JS_COMPARE, str(tmp / "old" / "price.ts"), str(proj / "web/price.ts"), "isExpensive,withTax"], env)
        tsres = json.loads(tr.stdout.strip().splitlines()[-1]) if tr.returncode == 0 else {"calls": 0, "differences": -1, "examples": [tr.stderr[-200:]]}
        for lang, res in (("Python", pyres), ("JavaScript", jsres), ("TypeScript", tsres)):
            print("   %-10s %4d calls, %d difference(s) %s" % (lang, res["calls"], res["differences"], "; ".join(res["examples"])))
            ok &= res["differences"] == 0 and res["calls"] > 0
        before = sum(t.count("\n") for t in files.values())
        after = sum(t.count("\n") for t in new.values())
        print("   lines: %d before, %d after (%d shorter)" % (before, after, before - after))
        r = sh([PY, str(HERE / "proposals.py"), "--undo"], env)
        restored = all((proj / rel).read_text(encoding="utf-8") == t for rel, t in files.items())
        print("\n4. UNDO puts every file back byte for byte: %s" % ("yes" if restored else "NO"))
        ok &= restored
        # ------------------------------------------------------------------ 5: with an AI (scripted stand-in)
        ai = tmp / "ai"
        ai.mkdir()
        (ai / "store.py").write_text(STORE_PY, encoding="utf-8")
        (tmp / "mock_ai.py").write_text(MOCK_AI, encoding="utf-8")
        env2 = dict(env, ARACHNID_AI_CMD='"%s" "%s"' % (PY, tmp / "mock_ai.py"))
        sh([PY, str(HERE / "slim.py"), str(ai), "--ai", "--out", str(tmp / "ai.json")], env2)
        aip = [p for p in json.loads((tmp / "ai.json").read_text(encoding="utf-8"))["proposals"] if p["kind"].startswith("ai-")]
        print("\n5. AI ANSWERS (good ones kept, every wrong one thrown away)")
        for fname, proof in AI_GOOD.items():
            hit = [p for p in aip if p.get("function") == fname and p["proof"] == proof]
            print("   %-9s %-20s %s" % ("KEPT" if hit else "MISSING", fname, proof.upper()))
            ok &= bool(hit)
        for fname in AI_BAD:
            hit = [p for p in aip if p.get("function") == fname]
            print("   %-9s %-20s wrong answer" % ("THROWN" if not hit else "KEPT!", fname))
            ok &= not hit
        print("\nBRUTAL SLIM SCORE: %d of %d shorter versions found (%d more), %d trap(s) touched, %d behaviour difference(s), "
              "undo %s, AI: %s" % (found, len(EXPECTED), extra, touched, max(0, pyres["differences"]) + max(0, jsres["differences"]) + max(0, tsres["differences"]),
                                   "exact" if restored else "FAILED", "all wrong answers stopped" if ok else "see above"))
        return 0 if ok else 1
    finally:
        if keep.exists():
            shutil.rmtree(HERE / "reports", ignore_errors=True)
            shutil.copytree(keep, HERE / "reports", dirs_exist_ok=True)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
