#!/usr/bin/env python3
"""
ARACHNID - BRUTAL JAVASCRIPT TEST: debugging leftovers
=====================================================
    python qa_js_brutal.py

A small shop (1 page with an inline script, 2 scripts, a React component, a TypeScript file) with 14 things left
behind while someone was debugging by hand: a `debugger;` in an inline script, one with no semicolon behind an if,
one inside a catch; errors thrown as plain text; an empty catch {} and one that holds only empty lines; a timer
started as "total" but ended as "Total"; a console.group() never closed; console.log("here") lines ... and its
CLEAN TWIN: correct code that LOOKS like the leftovers (`options.debugger`, "debugger;" inside text and comments,
catch blocks that explain themselves, `throw new Error`, matched timers, real log messages, and a test file, a
command-line tool and a generated file, which are allowed to log).

Passes only if a full scan finds EVERY leftover on its exact line, and finds NOTHING AT ALL on the clean twin.
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
 "index.html": "<!doctype html>\n<html lang=\"en\">\n<head>\n  <meta charset=\"utf-8\">\n  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n  <title>Shop</title>\n</head>\n<body>\n  <h1>Shop</h1>\n  <p id=\"out\">Loading</p>\n  <script src=\"js/cart.js\"></script>\n  <script src=\"js/main.js\"></script>\n  <script>\n    window.addEventListener(\"load\", () => {\n      debugger;\n      document.getElementById(\"out\").textContent = \"Ready\";\n    });\n  </script>\n</body>\n</html>\n",
 "js/cart.js": "const cart = [];\n\nfunction addItem(id, qty) {\n  if (!id) throw \"Item id is missing\";\n  if (qty > 99) {\n    throw `Too many: ${qty}`;\n  }\n  cart.push({ id, qty });\n  console.log(\"here\");\n  return cart.length;\n}\n\nfunction total(prices) {\n  console.time(\"total\");\n  let sum = 0;\n  for (const p of prices) sum += p;\n  console.timeEnd(\"Total\");\n  return sum;\n}\n\nfunction restore() {\n  try {\n    const saved = JSON.parse(localStorage.getItem(\"cart\") || \"[]\");\n    cart.push(...saved);\n  } catch (e) {}\n  document.title = \"Cart (\" + cart.length + \")\";\n}\n\nfunction check(item) {\n  if (item.qty < 1) throw 'Quantity must be at least 1';\n  return true;\n}\n",
 "js/main.js": "function render(items) {\n  console.group(\"Render\");\n  for (const it of items) {\n    document.getElementById(\"out\").textContent = it.id;\n    if (it.id === \"bad\") debugger\n  }\n  return items.length;\n}\n\nfunction save(items) {\n  try {\n    localStorage.setItem(\"cart\", JSON.stringify(items));\n  } catch (err) {\n\n\n  }\n  console.log(\"got here!!\");\n}\n\nfunction load() {\n  console.log(\"test\", window.location.hash);\n  return render([]);\n}\n",
 "src/Widget.tsx": "export function Widget(props: { n: number }) {\n  console.log(\"asdf\");\n  return <div className=\"widget\">{props.n}</div>;\n}\n",
 "src/api.ts": "export async function get(url: string): Promise<string> {\n  try {\n    const r = await fetch(url);\n    return await r.text();\n  } catch (e) {\n    debugger;\n    throw \"Network failed\";\n  }\n}\n"
}

CLEAN = {
 "index.html": "<!doctype html>\n<html lang=\"en\">\n<head>\n  <meta charset=\"utf-8\">\n  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n  <title>Shop</title>\n</head>\n<body>\n  <h1>Shop</h1>\n  <p id=\"out\">Loading</p>\n  <p>Tip: press F8 in the debugger; it resumes the page.</p>\n  <script src=\"js/cart.js\"></script>\n  <script src=\"js/main.js\"></script>\n  <script>\n    window.addEventListener(\"load\", () => {\n      // debugger;\n      restore();\n      addItem(\"mug\", timed(\"add\", () => 1));\n      total([1, 2]);\n      save(cart);\n      try { render(cart); } catch (e) { rethrow(e); }\n      document.getElementById(\"out\").textContent = \"Ready\";\n    });\n  </script>\n</body>\n</html>\n",
 "README.md": "# Shop\n\n- `src/Widget.tsx` and `src/api.ts`: the React part\n- `js/generated.js`: made by the builder\n",
 "js/cart.js": "const cart = [];\nconst options = { debugger: false, label: \"throw 'x' catch (e) {}\" };\noptions.debugger = true;\n\nfunction addItem(id, qty) {\n  if (!id) throw new Error(\"Item id is missing\");\n  if (qty > 99) {\n    throw new RangeError(`Too many: ${qty}`);\n  }\n  cart.push({ id, qty });\n  console.log(\"Added item\", id, \"- cart now has\", cart.length);\n  return cart.length;\n}\n\nfunction total(prices) {\n  console.time(\"total\");\n  let sum = 0;\n  for (const p of prices) sum += p;\n  console.timeEnd(\"total\");\n  console.time();\n  console.timeEnd();\n  return sum;\n}\n\nfunction timed(label, fn) {\n  console.time(label);\n  const r = fn();\n  console.timeEnd(label);\n  return r;\n}\n\nfunction restore() {\n  try {\n    const saved = JSON.parse(localStorage.getItem(\"cart\") || \"[]\");\n    cart.push(...saved);\n  } catch (e) {\n    // a broken saved cart is replaced by an empty one\n  }\n  try {\n    localStorage.removeItem(\"old-cart\");\n  } catch (_) {}\n  try {\n    sessionStorage.clear();\n  } catch (ignored) {}\n  document.title = \"Cart (\" + cart.length + \")\";\n}\n\nfunction rethrow(err) {\n  console.error(err);\n  throw err;\n}\n",
 "js/main.js": "const DEBUG = false;\n\nfunction render(items) {\n  console.group(\"Render\");\n  for (const it of items) {\n    document.getElementById(\"out\").textContent = it.id;\n  }\n  console.groupEnd();\n  console.groupCollapsed(\"Details\");\n  console.info(\"items\", items.length);\n  console.groupEnd();\n  return items.length;\n}\n\nfunction save(items) {\n  try {\n    localStorage.setItem(\"cart\", JSON.stringify(items));\n  } catch (err) {\n    console.error(\"Could not save the cart\", err);\n  }\n  if (DEBUG) console.log(\"here\");\n  console.log(\"Saved\", items.length, \"items\");\n}\n",
 "src/Widget.tsx": "export function Widget(props: { n: number }) {\n  console.log(\"Widget shown with\", props.n);\n  return <div className=\"widget\">{props.n}</div>;\n}\n",
 "src/api.ts": "export async function get(url: string): Promise<string> {\n  try {\n    const r = await fetch(url);\n    return await r.text();\n  } catch (e) {\n    throw new Error(\"Network failed: \" + String(e));\n  }\n}\n",
 "tests/cart.test.js": "test(\"adds\", () => {\n  console.log(\"here\");\n  document.body.innerHTML = \"<p id='out'></p>\";\n  expect(1).toBe(1);\n});\n",
 "tools/build.js": "#!/usr/bin/env node\nconst out = process.argv[2] || \"dist\";\nconsole.log(\"here\", out);\n",
 "js/generated.js": "// @generated by the shop builder - DO NOT EDIT\nfunction trap() { debugger; try { go(); } catch (e) {} throw \"x\"; }\ndocument.title = trap();\nconsole.log(\"here\");\n"
}

TRUTH = [
 ('index.html', '      debugger;', 'a debugger; left in a script inside the page'),
 ('js/cart.js', 'throw "Item id is missing"', 'an error thrown as plain text'),
 ('js/cart.js', 'throw `Too many', 'an error thrown as a text template'),
 ('js/cart.js', 'console.log("here")', 'a leftover console.log("here")'),
 ('js/cart.js', 'console.time("total")', 'a timer ended with the wrong name ("Total" is not "total")'),
 ('js/cart.js', '} catch (e) {}', 'a broken saved cart swallowed in silence'),
 ('js/cart.js', "throw 'Quantity", 'an error thrown as text in single quotes'),
 ('js/main.js', 'console.group("Render")', 'a console group that is never closed'),
 ('js/main.js', 'if (it.id === "bad") debugger', 'a debugger with no semicolon, behind an if'),
 ('js/main.js', '} catch (err) {', 'a catch that holds only empty lines'),
 ('js/main.js', 'console.log("got here!!")', 'a leftover console.log("got here!!")'),
 ('src/Widget.tsx', 'console.log("asdf")', 'a leftover console.log in a React component'),
 ('src/api.ts', '    debugger;', 'a debugger inside a catch, in TypeScript'),
 ('src/api.ts', 'throw "Network failed"', 'an error thrown as text, in TypeScript'),
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
    tmp = Path(tempfile.mkdtemp(prefix="arachnid-brutal-js-"))
    keep = tmp / "reports-backup"
    if (HERE / "reports").exists():
        shutil.copytree(HERE / "reports", keep)
    try:
        write(tmp / "shop", SHOP)
        write(tmp / "clean", CLEAN)
        ents = scan(tmp / "shop")
        caught = 0
        print("HIDDEN LEFTOVERS (each must be found on its exact line)")
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
        print("\nBRUTAL JS SCORE: %d of %d hidden leftovers found, %d false alarm(s) on the clean twin" % (caught, len(TRUTH), len(noise)))
        return 0 if caught == len(TRUTH) and not noise else 1
    finally:
        if keep.exists():
            shutil.rmtree(HERE / "reports", ignore_errors=True)
            shutil.copytree(keep, HERE / "reports", dirs_exist_ok=True)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
