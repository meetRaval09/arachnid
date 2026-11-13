#!/usr/bin/env python3
"""
ARACHNID - BRUTAL TYPESCRIPT TEST
=================================
    python qa_ts_brutal.py          (needs TypeScript: `npm install --no-save typescript@5.8.3` in this folder,
                                     or ARACHNID_TSC pointing at a tsc)

A small TypeScript shop with 14 hidden problems: 5 real type errors only TypeScript's own checker can see (a misspelled
property, text returned where a number was promised, a missing argument, a value that may not exist, a comparison that
can never be true), an @ts-ignore with no reason and an @ts-nocheck file, `.only` / `fit` left in three test files,
the Node debugger left in the real start command, the Dockerfile and docker-compose, and source maps published with
the live site ... and its CLEAN TWIN: the same shop fixed, plus look-alikes that must stay quiet (an @ts-ignore that
explains itself, "it.only(" inside text, it.skip / xit, a `debug` and a `dev` script, Dockerfile.dev and
docker-compose.debug.yml, `sourcemap: "hidden"`, a demo folder).

Passes only if a full scan finds EVERY problem on its exact line, and finds NOTHING AT ALL on the clean twin.
Arachnid never changes anything here: tsc runs with --noEmit and writes no files.
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


def _tsc():
    if os.environ.get("ARACHNID_TSC") and Path(os.environ["ARACHNID_TSC"]).exists():
        return os.environ["ARACHNID_TSC"]
    for name in ("tsc.cmd", "tsc"):
        c = HERE / "node_modules" / ".bin" / name
        if c.exists():
            return str(c)
    return None


TSC = _tsc()
if not TSC:
    print("This test needs TypeScript. In the Arachnid folder run:  npm install --no-save typescript@5.8.3")
    sys.exit(2)
os.environ["ARACHNID_TSC"] = TSC

SHOP = {
 "tsconfig.json": "{\n  \"compilerOptions\": {\n    \"strict\": true,\n    \"target\": \"ES2022\",\n    \"module\": \"ESNext\",\n    \"moduleResolution\": \"Bundler\",\n    \"lib\": [\n      \"ES2022\",\n      \"DOM\"\n    ],\n    \"types\": [],\n    \"noEmit\": true,\n    \"skipLibCheck\": true\n  },\n  \"include\": [\n    \"src\"\n  ]\n}\n",
 "package.json": "{\n  \"name\": \"shop\",\n  \"private\": true,\n  \"type\": \"module\",\n  \"scripts\": {\n    \"build\": \"tsc && vite build\",\n    \"start\": \"node --inspect=0.0.0.0:9229 dist/server.js\",\n    \"debug\": \"node --inspect-brk dist/server.js\"\n  }\n}\n",
 "Dockerfile": "FROM node:22-slim\nWORKDIR /app\nCOPY dist ./dist\nEXPOSE 3000\nCMD [\"node\", \"--inspect-brk\", \"dist/server.js\"]\n",
 "docker-compose.yml": "services:\n  shop:\n    build: .\n    ports:\n      - \"3000:3000\"\n    environment:\n      - NODE_OPTIONS=--inspect=0.0.0.0:9229\n",
 "vite.config.ts": "export default {\n  build: {\n    outDir: \"dist\",\n    sourcemap: true,\n  },\n};\n",
 "src/types.ts": "export type Status = \"pending\" | \"shipped\" | \"delivered\";\n\nexport interface Item {\n  id: string;\n  price: number;\n  qty: number;\n}\n\nexport interface User {\n  id: number;\n  name: string;\n}\n",
 "src/cart.ts": "import type { Item, Status, User } from \"./types\";\n\nexport function lineTotal(item: Item): number {\n  return item.prise * item.qty;\n}\n\nexport function formatTotal(total: number): number {\n  return total.toFixed(2);\n}\n\nexport function applyDiscount(total: number, percent: number): number {\n  return total - (total * percent) / 100;\n}\n\nexport function checkout(items: Item[]): number {\n  const total = items.reduce((sum, it) => sum + lineTotal(it), 0);\n  return applyDiscount(total);\n}\n\nexport function ownerName(users: User[], id: number): string {\n  return users.find((u) => u.id === id).name;\n}\n\nexport function isDone(status: Status): boolean {\n  return status === \"shiped\";\n}\n\nexport function parseQty(raw: string): number {\n  // @ts-ignore\n  return raw * 1;\n}\n",
 "src/legacy.ts": "// @ts-nocheck\nexport function oldTotal(items) {\n  return items.map((i) => i.price).reduce((a, b) => a + b);\n}\n",
 "tests/cart.test.ts": "import { checkout, lineTotal } from \"../src/cart\";\n\ndescribe(\"cart\", () => {\n  it.only(\"adds two items\", () => {\n    expect(checkout([])).toBe(0);\n  });\n  it(\"prices one line\", () => {\n    expect(lineTotal({ id: \"a\", price: 2, qty: 3 })).toBe(6);\n  });\n});\n",
 "tests/discount.spec.ts": "import { applyDiscount } from \"../src/cart\";\n\ndescribe.only(\"discount\", () => {\n  it(\"takes 10 percent off\", () => {\n    expect(applyDiscount(100, 10)).toBe(90);\n  });\n});\n",
 "tests/owner.spec.ts": "import { ownerName } from \"../src/cart\";\n\ndescribe(\"owner\", () => {\n  fit(\"finds the owner\", () => {\n    expect(ownerName([{ id: 1, name: \"Asha\" }], 1)).toBe(\"Asha\");\n  });\n});\n"
}

CLEAN = {
 "tsconfig.json": "{\n  \"compilerOptions\": {\n    \"strict\": true,\n    \"target\": \"ES2022\",\n    \"module\": \"ESNext\",\n    \"moduleResolution\": \"Bundler\",\n    \"lib\": [\n      \"ES2022\",\n      \"DOM\"\n    ],\n    \"types\": [],\n    \"noEmit\": true,\n    \"skipLibCheck\": true\n  },\n  \"include\": [\n    \"src\"\n  ]\n}\n",
 "package.json": "{\n  \"name\": \"shop\",\n  \"private\": true,\n  \"type\": \"module\",\n  \"main\": \"src/index.ts\",\n  \"scripts\": {\n    \"build\": \"tsc && vite build\",\n    \"start\": \"node dist/server.js\",\n    \"debug\": \"node --inspect-brk dist/server.js\",\n    \"dev\": \"tsx watch --inspect src/server.ts\",\n    \"test\": \"vitest run\"\n  }\n}\n",
 "Dockerfile": "FROM node:22-slim\nWORKDIR /app\nCOPY dist ./dist\nEXPOSE 3000\n# for debugging run: docker compose -f docker-compose.debug.yml up (it adds --inspect)\nCMD [\"node\", \"dist/server.js\"]\n",
 "Dockerfile.dev": "FROM node:22-slim\nWORKDIR /app\nCMD [\"node\", \"--inspect=0.0.0.0:9229\", \"dist/server.js\"]\n",
 "docker-compose.yml": "services:\n  shop:\n    build: .\n    ports:\n      - \"3000:3000\"\n    environment:\n      - NODE_ENV=production\n",
 "docker-compose.debug.yml": "services:\n  shop:\n    build: .\n    environment:\n      - NODE_OPTIONS=--inspect=0.0.0.0:9229\n",
 "vite.config.ts": "export default {\n  css: { devSourcemap: true },\n  build: {\n    outDir: \"dist\",\n    sourcemap: \"hidden\",\n  },\n};\n",
 "playground/vite.config.ts": "export default {\n  build: {\n    sourcemap: true,\n  },\n};\n",
 "README.md": "# Shop\n\nThe code is in `src/` and the tests in `tests/`. `playground/` is a demo of the build settings.\n",
 "src/types.ts": "export type Status = \"pending\" | \"shipped\" | \"delivered\";\n\nexport interface Item {\n  id: string;\n  price: number;\n  qty: number;\n}\n\nexport interface User {\n  id: number;\n  name: string;\n}\n",
 "src/cart.ts": "import type { Item, Status, User } from \"./types\";\n\nexport function lineTotal(item: Item): number {\n  return item.price * item.qty;\n}\n\nexport function formatTotal(total: number): string {\n  return total.toFixed(2);\n}\n\nexport function applyDiscount(total: number, percent: number): number {\n  return total - (total * percent) / 100;\n}\n\nexport function checkout(items: Item[]): number {\n  const total = items.reduce((sum, it) => sum + lineTotal(it), 0);\n  return applyDiscount(total, 0);\n}\n\nexport function ownerName(users: User[], id: number): string {\n  return users.find((u) => u.id === id)?.name ?? \"unknown\";\n}\n\nexport function isDone(status: Status): boolean {\n  return status === \"shipped\" || status === \"delivered\";\n}\n\nexport function parseQty(raw: string): number {\n  // @ts-ignore - the old scanner hands us a number typed as string (fixed in scanner v3)\n  return raw * 1;\n}\n\nexport function note(): string {\n  // we removed @ts-nocheck from legacy.ts last month\n  return \"it.only( and fit( are words here, not tests\";\n}\n",
 "src/index.ts": "import { checkout, formatTotal, isDone, note, ownerName, parseQty } from \"./cart\";\nimport { oldTotal } from \"./legacy\";\n\nexport function summary(): string {\n  const items = [{ id: \"mug\", price: 4, qty: parseQty(\"2\") }];\n  const total = checkout(items) + oldTotal(items);\n  return [formatTotal(total), isDone(\"shipped\"), ownerName([], 1), note()].join(\" \");\n}\n",
 "src/legacy.ts": "import type { Item } from \"./types\";\n\nexport function oldTotal(items: Item[]): number {\n  return items.map((i) => i.price).reduce((a, b) => a + b, 0);\n}\n",
 "tests/cart.test.ts": "import { checkout, lineTotal } from \"../src/cart\";\n\ndescribe(\"cart\", () => {\n  it(\"only counts real items\", () => {\n    expect(checkout([])).toBe(0);\n  });\n  it.skip(\"prices one line (waiting for the new prices)\", () => {\n    expect(lineTotal({ id: \"a\", price: 2, qty: 3 })).toBe(6);\n  });\n  xit(\"old pricing\", () => {\n    expect(1).toBe(1);\n  });\n});\n",
 "tests/discount.spec.ts": "import { applyDiscount } from \"../src/cart\";\n\nconst only = (n: number) => n;\n\ndescribe(\"discount\", () => {\n  it(\"takes 10 percent off\", () => {\n    expect(applyDiscount(only(100), 10)).toBe(90);\n  });\n});\n",
 "tests/owner.spec.ts": "import { ownerName } from \"../src/cart\";\n\ndescribe(\"owner\", () => {\n  it(\"finds the owner\", () => {\n    expect(ownerName([{ id: 1, name: \"Asha\" }], 1)).toBe(\"Asha\");\n  });\n});\n"
}

TRUTH = [
 ('src/cart.ts', 'return item.prise', 'a misspelled property: prise instead of price (tsc)'),
 ('src/cart.ts', 'return total.toFixed(2);', 'text returned where a number was promised (tsc)'),
 ('src/cart.ts', 'return applyDiscount(total);', 'a function called with one argument too few (tsc)'),
 ('src/cart.ts', 'return users.find(', 'a value that may not exist, used anyway (tsc)'),
 ('src/cart.ts', 'return status === "shiped";', 'a comparison that can never be true: "shiped" is not a status (tsc)'),
 ('src/cart.ts', '  // @ts-ignore', '@ts-ignore with no reason, hiding text multiplied by a number'),
 ('src/legacy.ts', '// @ts-nocheck', 'type checking switched off for a whole file'),
 ('tests/cart.test.ts', 'it.only(', 'it.only: every other test in the file silently skipped'),
 ('tests/discount.spec.ts', 'describe.only(', 'describe.only left in'),
 ('tests/owner.spec.ts', 'fit(', 'fit (focused test, Jasmine style) left in'),
 ('package.json', '"start"', 'the real start command opens the Node debugger to the whole network'),
 ('Dockerfile', 'CMD', 'the container freezes on its first line, waiting for a debugger (--inspect-brk)'),
 ('docker-compose.yml', 'NODE_OPTIONS=--inspect', 'the debugger switched on through NODE_OPTIONS'),
 ('vite.config.ts', 'sourcemap: true', 'source maps published with the live site'),
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
    tmp = Path(tempfile.mkdtemp(prefix="arachnid-brutal-ts-"))
    keep = tmp / "reports-backup"
    if (HERE / "reports").exists():
        shutil.copytree(HERE / "reports", keep)
    try:
        write(tmp / "shop", SHOP)
        write(tmp / "clean", CLEAN)
        ents = scan(tmp / "shop")
        caught = 0
        print("HIDDEN PROBLEMS (each must be found on its exact line)")
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
        print("\nBRUTAL TYPESCRIPT SCORE: %d of %d hidden problems found, %d false alarm(s) on the clean twin" % (caught, len(TRUTH), len(noise)))
        return 0 if caught == len(TRUTH) and not noise else 1
    finally:
        if keep.exists():
            shutil.rmtree(HERE / "reports", ignore_errors=True)
            shutil.copytree(keep, HERE / "reports", dirs_exist_ok=True)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
