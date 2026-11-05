#!/usr/bin/env python3
"""
ARACHNID - Step 10: ADVISOR  (lighter frameworks + faster / smaller code.  It NEVER changes code by itself.)
============================================================================================================
    python advisor.py [folder] [--all]

What it looks for
  STACK       a heavier framework or library than your project needs (React -> Preact, Electron -> a lighter shell,
              Firebase whole -> modular, full OpenCV -> headless ...).  It measures what it can (real folder sizes in
              node_modules, how many parts of the library you really use, how big your own code is) and says what it
              could not measure.
  SPEED       code that gets slower as data grows: searching a list inside a loop, loops inside loops that match items,
              repeated sorting, copying a list again and again, a recursive function that repeats the same work.
              Each one says how the cost changes, e.g. "time O(n*m) -> O(n+m), memory +1 set".
  MEMORY      lists built just to be summed, whole collections copied for no reason.

What happens next
  Every idea above medium confidence goes into the PROPOSALS queue (proposals.py).  Nothing is applied until a human
  approves it:   python arachnid.py proposals   ->   approve <id>   ->   apply-approved
  Only a few ideas come with an exact mechanical edit (a constant list used only for 'is it in here?' -> a set; a
  list built only to be summed -> a generator; `r = r + [x]` in a loop -> append; `sorted(xs)[0]` -> min).  Even then the SAFETY GATE must prove the product behaves the same
  before the edit is kept.  Everything else is explained for a human to do.

Honest limits: Python is analysed properly (syntax tree).  JavaScript/TypeScript is read with careful patterns.
Other languages only get the stack advice.  Reading code can suggest, never prove - only measuring proves.
"""
import argparse
import ast
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import insights  # noqa: E402  (Project scanner, shared helpers)
import proposals  # noqa: E402

JS_EXT = {"js", "jsx", "mjs", "cjs", "ts", "tsx", "vue", "svelte"}


def prop(kind, rel, line, title, benefit, risk, conf, key, patches=None):
    return {"id": proposals.make_id("advisor", kind, rel, key), "kind": kind, "file": rel, "line": line, "title": title,
            "benefit": benefit, "risk": risk, "confidence": conf, "patches": patches or []}


def abs_offset(text, line_starts, lineno, col_bytes):
    """ast gives columns in UTF-8 bytes; turn (line, byte column) into a character offset in the text."""
    start = line_starts[lineno - 1]
    line_end = text.find("\n", start)
    line = text[start: line_end if line_end >= 0 else len(text)]
    return start + len(line.encode("utf-8")[:col_bytes].decode("utf-8", "ignore"))


def starts_of(text):
    s, pos = [0], 0
    for ln in text.split("\n")[:-1]:
        pos += len(ln) + 1
        s.append(pos)
    return s


# ---------------------------------------------------------------------------------------------------------------
# STACK ADVICE
# ---------------------------------------------------------------------------------------------------------------
def dir_size(p):
    total = 0
    for dp, dn, fn in os.walk(p):
        for f in fn:
            try:
                total += os.path.getsize(os.path.join(dp, f))
            except OSError:
                pass
    return total


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "%.0f %s" % (n, unit) if unit == "B" else "%.1f %s" % (n, unit)
        n /= 1024.0


def js_loc(proj):
    n = 0
    for rel, t in proj.texts.items():
        if insights.ext_of(Path(rel)) in JS_EXT:
            n += t.count("\n") + 1
    return n


def py_loc(proj):
    return sum(t.count("\n") + 1 for rel, t in proj.texts.items() if rel.endswith(".py"))


def uses(proj, pattern):
    rx = re.compile(pattern)
    return sorted(rel for rel, t in proj.texts.items() if rx.search(t))


def stack_advice(proj):
    out = []
    root = proj.root
    pkgs = [r for r in proj.texts if r.endswith("package.json") and "node_modules" not in r]
    for rel in pkgs:
        try:
            data = json.loads(proj.texts[rel])
        except Exception:
            continue
        deps = {}
        deps.update(data.get("dependencies") or {})
        runtime = set(deps)
        dev = set((data.get("devDependencies") or {}))
        base = Path(rel).parent
        nm = root / base / "node_modules"

        def measured(name):
            d = nm / name
            return ("measured on disk: %s" % human(dir_size(d))) if d.is_dir() else "size not measured (run `npm install` to measure)"
        loc = js_loc(proj)

        if "react" in runtime and "react-dom" in runtime:
            risky = uses(proj, r"\buseTransition\b|\buseDeferredValue\b|\bunstable_|react-native|@react-three/fiber")
            conf = "medium" if not risky else "low"
            out.append(prop("framework", rel, 1, "Replace React with Preact (same code style, via the `preact/compat` layer)",
                            "download ~140 KB -> ~10 KB of framework code (React+ReactDOM min vs Preact+compat, typical figures); faster start, lower memory. %s; your own code is %d lines of JS/TS" % (measured("react-dom"), loc),
                            "behaviour can differ in rare edge cases (Suspense, concurrent features, some libraries) - needs a human test of every screen" + (
                                ("; uses features Preact only partly supports in: " + ", ".join(risky[:3])) if risky else ""),
                            conf, "react->preact"))
        if "electron" in runtime or "electron" in dev:
            ipc = len(uses(proj, r"\bipcMain\b|\bipcRenderer\b|require\(['\"](?:fs|child_process|path)['\"]\)"))
            out.append(prop("framework", rel, 1, "Move from Electron to a lighter desktop shell (Tauri, or Neutralino / the system web view)",
                            "typical idle memory 150-300 MB -> 30-80 MB and installer 80-150 MB -> 3-10 MB, because Electron ships a whole copy of Chrome; %s" % measured("electron"),
                            "this is a re-platforming: the 'main process' code (%d file%s use Node/IPC features) must be rewritten for the new shell. A decision for a team, not a patch" % (ipc, "" if ipc == 1 else "s"),
                            "medium", "electron"))
        if "firebase" in runtime and uses(proj, r"""from\s+['"]firebase['"]|require\(\s*['"]firebase['"]\s*\)|firebase/compat"""):
            out.append(prop("framework", rel, 1, "Use Firebase's modular imports (`firebase/app`, `firebase/auth` ...) instead of the whole package",
                            "the modular version only ships the parts you call - often several hundred KB less JavaScript; %s" % measured("firebase"),
                            "import lines and calls change style (v9 modular API); behaviour is the same but every call site needs touching", "high", "firebase-modular"))
        if "bootstrap" in runtime and not uses(proj, r"bootstrap(?:\.bundle)?(?:\.min)?\.js|from\s+['\"]bootstrap['\"]|require\(\s*['\"]bootstrap['\"]|data-bs-"):
            out.append(prop("framework", rel, 1, "Bootstrap's JavaScript is installed but nothing uses it - load only its CSS",
                            "~60-80 KB less JavaScript to download and run; %s" % measured("bootstrap"),
                            "none if truly unused: check dropdowns, modals and collapses first", "medium", "bootstrap-js"))
        for big, alt, why in (("plotly.js", "plotly.js-basic-dist, Chart.js or uPlot", "plotly.js is ~3 MB"),
                              ("echarts", "importing only the chart types you draw (`echarts/core`)", "the full bundle is ~1 MB"),
                              ("@fortawesome/fontawesome-free", "inlining only the icons you show (SVG)", "the full icon set is ~1.5 MB of fonts/CSS"),
                              ("socket.io-client", "the browser's built-in WebSocket if you only send simple messages", "adds ~40 KB")):
            if big in runtime:
                out.append(prop("framework", rel, 1, "`%s` is large for what most projects use it for" % big, "%s; consider %s; %s" % (why, alt, measured(big)),
                                "check which features you rely on first", "low", "heavy:" + big))
        if "d3" in runtime and uses(proj, r"""from\s+['"]d3['"]|require\(\s*['"]d3['"]\s*\)"""):
            out.append(prop("framework", rel, 1, "Import only the d3 modules you use (`d3-scale`, `d3-selection` ...) instead of all of `d3`",
                            "the full d3 is ~280 KB minified; most charts need a quarter of it; %s" % measured("d3"), "import lines change; calls stay the same", "medium", "d3-modular"))
        if "react-scripts" in runtime | dev:
            out.append(prop("framework", rel, 1, "Create React App (react-scripts) is no longer maintained - move to Vite",
                            "build and start-up times typically drop from tens of seconds to a few seconds, and it gets security updates again",
                            "build configuration changes; the app code mostly stays", "medium", "cra->vite"))
        elif "webpack" in dev | runtime and "vite" not in dev | runtime:
            out.append(prop("framework", rel, 1, "Consider Vite (or esbuild) instead of webpack for building",
                            "faster dev start and builds (seconds instead of tens of seconds); bundle size is usually similar",
                            "custom webpack loaders/plugins must be re-created; output must be re-tested", "low", "webpack->vite"))
        vv = str(deps.get("vue", "")).lstrip("^~>=v ")
        if vv.startswith("2"):
            out.append(prop("framework", rel, 1, "Vue 2 -> Vue 3 (smaller, faster, and Vue 2 is end-of-life)",
                            "~30% smaller runtime and faster rendering in Vue's own benchmarks; security fixes continue",
                            "template and plugin differences; use the official migration build first", "medium", "vue2->3"))
        if loc and loc < 1500 and runtime & {"react", "vue", "@angular/core", "svelte"}:
            fw = sorted(runtime & {"react", "vue", "@angular/core", "svelte"})[0]
            out.append(prop("framework", rel, 1, "Your app is small (%d lines of JS/TS) compared with the framework (%s)" % (loc, fw),
                            "a framework this size pays off for big apps; for a few screens, plain HTML + a tiny library (Alpine.js ~15 KB, or Preact) is much lighter",
                            "a rewrite - only worth it if load time or memory matters to you", "low", "small-app"))
    # Python
    reqs = [r for r in proj.texts if r.split("/")[-1] == "requirements.txt"]
    names = set()
    for rel in reqs:
        for ln in proj.texts[rel].split("\n"):
            m = re.match(r"\s*([A-Za-z0-9_.\-]+)", ln)
            if m and not ln.strip().startswith(("#", "-")):
                names.add(m.group(1).lower().replace("_", "-"))
    if names:
        rel = reqs[0]
        if "opencv-python" in names and not uses(proj, r"\bcv2\.(?:imshow|namedWindow|waitKey|destroyAllWindows|selectROI|createTrackbar)\b"):
            out.append(prop("framework", rel, 1, "`opencv-python` -> `opencv-python-headless` (you never open an OpenCV window)",
                            "smaller install (~30-60 MB less) and no GUI libraries loaded; faster container/CI start", "none for code that never calls imshow/waitKey; verify on the real machine", "high", "opencv-headless"))
        if "django" in names and py_loc(proj) < 1200 and not uses(proj, r"models\.Model|django\.contrib\.admin"):
            out.append(prop("framework", rel, 1, "Django is a big framework and this project uses none of its database models or admin",
                            "Flask or FastAPI start faster and use noticeably less memory for a small API", "a rewrite of views/URLs/settings; only if size or speed matters to you", "low", "django-small"))
        if "selenium" in names and uses(proj, r"\bfrom selenium\b|\bimport selenium\b"):
            out.append(prop("framework", rel, 1, "Selenium -> Playwright for browser automation",
                            "typically faster and steadier runs with built-in waiting, and one tool installs its own browsers", "test scripts are rewritten", "low", "selenium->playwright"))
    # other languages' dependency files
    try:
        import deps as D
        ds = D.parse_all(proj)
        names = {(d["eco"], d["name"]) for d in ds}
        loc = lambda ext: sum(t.count("\n") + 1 for r, t in proj.texts.items() if r.endswith(ext))
        for d, alt, why, level, users in D.replacement_findings(ds, lambda d: []):
            out.append(prop("framework", d["file"], d["line"], "`%s` -> %s" % (d["name"], alt), why, "check compatibility and run your tests; nothing here is measured",
                            {"high": "high", "medium": "medium"}.get(level, "low"), "dep:" + d["name"]))
        java_names = {d["name"] for d in ds if d["eco"] == "java"}
        if "org.springframework.boot:spring-boot-starter-web" in java_names and 0 < loc(".java") < 3000 and not uses(proj, r"@Entity|JpaRepository|spring-security|@EnableWebSecurity"):
            d0 = next(d for d in ds if d["name"] == "org.springframework.boot:spring-boot-starter-web")
            out.append(prop("framework", d0["file"], d0["line"], "Spring Boot is a large framework for a small web app (%d lines of Java, no database entities or security)" % loc(".java"),
                            "Javalin, Micronaut or Quarkus start in a fraction of a second and idle at roughly 50-100 MB instead of 250-400 MB (typical figures, not measured here)",
                            "a rewrite of controllers/configuration; only if memory or start-up time matters to you", "low", "spring-small"))
        if ("go", "github.com/gin-gonic/gin") in names and 0 < loc(".go") < 2500:
            d0 = next(d for d in ds if d["name"] == "github.com/gin-gonic/gin")
            out.append(prop("framework", d0["file"], d0["line"], "Gin is used in a small Go service - the standard `net/http` router (Go 1.22+) may be enough",
                            "one dependency less, smaller binary, faster builds", "route handlers change signature; only if you use few Gin features", "low", "gin-small"))
        if ("php", "laravel/framework") in names and 0 < loc(".php") < 1500:
            d0 = next(d for d in ds if d["name"] == "laravel/framework")
            out.append(prop("framework", d0["file"], d0["line"], "Laravel is a full-stack framework for a small PHP project (%d lines)" % loc(".php"),
                            "Slim or plain PHP + a router uses much less memory per request", "a rewrite; only worth it if hosting limits or speed matter", "low", "laravel-small"))
        if ("ruby", "rails") in names and 0 < loc(".rb") < 1500:
            d0 = next(d for d in ds if d["name"] == "rails")
            out.append(prop("framework", d0["file"], d0["line"], "Rails for a small Ruby project (%d lines)" % loc(".rb"),
                            "Sinatra or Roda boot faster and use a fraction of the memory", "a rewrite; only if size or speed matters to you", "low", "rails-small"))
    except Exception:
        pass
    return out


# ---------------------------------------------------------------------------------------------------------------
# SPEED / MEMORY - Python (syntax tree)
# ---------------------------------------------------------------------------------------------------------------
LISTY_CALLS = {"list", "sorted"}


def py_findings(rel, text):
    out = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return out
    starts = starts_of(text)

    def seg(node):
        a = abs_offset(text, starts, node.lineno, node.col_offset)
        b = abs_offset(text, starts, node.end_lineno, node.end_col_offset)
        return a, b, text[a:b]

    # --- module-level constant collections used only for membership tests -> set
    consts = {}
    for st in tree.body:
        if isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name) and isinstance(st.value, (ast.List, ast.Tuple)):
            vals = st.value.elts
            if len(vals) >= 4 and (all(isinstance(v, ast.Constant) and isinstance(v.value, str) for v in vals) or
                                   all(isinstance(v, ast.Constant) and type(v.value) is int for v in vals)):
                consts[st.targets[0].id] = st
    if consts:
        bind = Counter()
        member_uses, other_uses = Counter(), Counter()
        parents = {}
        for n in ast.walk(tree):
            for c in ast.iter_child_nodes(n):
                parents[c] = n
        for n in ast.walk(tree):
            if isinstance(n, ast.Name) and n.id in consts:
                if isinstance(n.ctx, ast.Store):
                    bind[n.id] += 1
                else:
                    par = parents.get(n)
                    if isinstance(par, ast.Compare) and n in par.comparators and any(isinstance(o, (ast.In, ast.NotIn)) for o in par.ops) and len(par.ops) == 1:
                        member_uses[n.id] += 1
                    else:
                        other_uses[n.id] += 1
        for name, st in consts.items():
            exported = "__all__" in text and re.search(r"__all__[^\n]*['\"]%s['\"]" % re.escape(name), text)
            if bind[name] == 1 and member_uses[name] >= 1 and other_uses[name] == 0 and not exported \
                    and insights_token_files(name) <= {rel}:
                a, b, src = seg(st.value)
                inner = src[1:-1].rstrip()
                new = "{" + inner + "}"
                out.append(prop("speed", rel, st.lineno,
                                "`%s` is a fixed list used only to ask 'is X in here?' - make it a set" % name,
                                "each check goes from reading up to %d items (time O(n)) to one lookup (O(1)); costs a little extra memory for the hash table" % len(st.value.elts),
                                "none for membership checks (set order is never used here: every use is an `in` / `not in` test)", "high",
                                "const-set:" + name, [{"at": a, "old": src, "new": new}]))
    # --- loops
    list_names = set(consts)
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
            v = n.value
            if isinstance(v, (ast.List, ast.ListComp)) or (isinstance(v, ast.Call) and isinstance(v.func, ast.Name) and v.func.id in LISTY_CALLS):
                list_names.add(n.targets[0].id)

    def loop_bodies(node):
        for x in ast.walk(node):
            if isinstance(x, (ast.For, ast.While, ast.AsyncFor)):
                yield x

    seen = set()
    for lp in loop_bodies(tree):
        body = ast.Module(body=lp.body, type_ignores=[])
        for x in ast.walk(body):
            if isinstance(x, ast.Compare) and len(x.ops) == 1 and isinstance(x.ops[0], (ast.In, ast.NotIn)) and isinstance(x.comparators[0], ast.Name) \
                    and x.comparators[0].id in list_names and x.comparators[0].id not in consts:
                key = ("in", x.comparators[0].id, x.lineno)
                if key not in seen:
                    seen.add(key)
                    nm = x.comparators[0].id
                    out.append(prop("speed", rel, x.lineno, "`... in %s` inside a loop searches the whole list every time" % nm,
                                    "time O(n*m) -> O(n+m) if `%s` becomes a set (or dict) before the loop; memory +1 hash table the size of the list" % nm,
                                    "a set drops duplicates and loses order; fine if `%s` is only used for lookups" % nm, "medium", "in-list:%s:%s" % (nm, lp.lineno)))
            if isinstance(x, ast.Call) and isinstance(x.func, ast.Attribute):
                if x.func.attr == "pop" and len(x.args) == 1 and isinstance(x.args[0], ast.Constant) and x.args[0].value == 0:
                    out.append(prop("speed", rel, x.lineno, "`.pop(0)` inside a loop moves every remaining item each time",
                                    "time O(n) per pop -> O(1) with `collections.deque.popleft()`; same memory", "deque has no slicing; check how the list is used elsewhere", "medium", "pop0:%d" % x.lineno))
                if x.func.attr == "insert" and x.args and isinstance(x.args[0], ast.Constant) and x.args[0].value == 0:
                    out.append(prop("speed", rel, x.lineno, "`.insert(0, ...)` inside a loop moves every item each time",
                                    "time O(n) per insert -> O(1) with `collections.deque.appendleft()`", "deque has no slicing", "medium", "insert0:%d" % x.lineno))
                if x.func.attr == "sort" and not x.args:
                    out.append(prop("speed", rel, x.lineno, "sorting inside a loop repeats the full sort on every pass",
                                    "time O(n log n) per pass; if only one new item is added each time, `bisect.insort` is O(n) and a heap is O(log n)", "only if the list changes by a few items per pass", "low", "sort-loop:%d" % x.lineno))
            if isinstance(x, ast.Call) and isinstance(x.func, ast.Name) and x.func.id == "sorted":
                out.append(prop("speed", rel, x.lineno, "`sorted(...)` inside a loop repeats the full sort on every pass",
                                "time O(n log n) per pass; sort once outside the loop if the data does not change inside it", "only valid if the data does not change inside the loop", "low", "sorted-loop:%d" % x.lineno))
            if isinstance(x, ast.Assign) and len(x.targets) == 1 and isinstance(x.targets[0], ast.Name) and isinstance(x.value, ast.BinOp) \
                    and isinstance(x.value.op, ast.Add) and isinstance(x.value.left, ast.Name) and x.value.left.id == x.targets[0].id \
                    and isinstance(x.value.right, (ast.List, ast.ListComp)):
                nm = x.targets[0].id
                sa, sb, ssrc = seg(x)
                rs = seg(x.value.right)[2]
                if isinstance(x.value.right, ast.List) and len(x.value.right.elts) == 1 and not isinstance(x.value.right.elts[0], ast.Starred):
                    repl = "%s.append(%s)" % (nm, seg(x.value.right.elts[0])[2])
                else:
                    repl = "%s.extend(%s)" % (nm, rs)
                out.append(prop("speed", rel, x.lineno, "`%s = %s + [...]` in a loop copies the whole list every time" % (nm, nm),
                                "time O(n^2) overall -> O(n) with `%s.append(...)` / `%s.extend(...)`; memory: no repeated temporary copies" % (nm, nm),
                                "if another variable points at the same list it would now see the additions - the tests/gate must agree", "medium", "listplus:%d" % x.lineno,
                                [{"at": sa, "old": ssrc, "new": repl}]))
        # nested loop that matches pairs
        for inner in ast.walk(body):
            if isinstance(inner, (ast.For, ast.AsyncFor)) and inner is not lp and isinstance(lp, (ast.For, ast.AsyncFor)):
                outer_vars = {t.id for t in ast.walk(lp.target) if isinstance(t, ast.Name)}
                inner_vars = {t.id for t in ast.walk(inner.target) if isinstance(t, ast.Name)}
                for c in ast.walk(ast.Module(body=inner.body, type_ignores=[])):
                    if isinstance(c, ast.Compare) and len(c.ops) == 1 and isinstance(c.ops[0], ast.Eq):
                        l = {t.id for t in ast.walk(c.left) if isinstance(t, ast.Name)}
                        r = {t.id for t in ast.walk(c.comparators[0]) if isinstance(t, ast.Name)}
                        if (l & outer_vars and r & inner_vars) or (l & inner_vars and r & outer_vars):
                            out.append(prop("speed", rel, inner.lineno, "two loops compare every item of one list with every item of the other",
                                            "time O(n*m) -> O(n+m): put one side in a dict keyed by the compared value, then look each item of the other side up; memory +1 dict",
                                            "if keys repeat, the dict must hold lists; check the matching rule first", "medium", "nested:%d" % inner.lineno))
                            break
    # --- list built just to be consumed by sum/min/max/any/all
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in ("sum", "min", "max", "any", "all") \
                and len(n.args) == 1 and not n.keywords and isinstance(n.args[0], ast.ListComp):
            a, b, src = seg(n.args[0])
            out.append(prop("memory", rel, n.lineno, "`%s([ ... ])` builds a whole list only to throw it away" % n.func.id,
                            "memory O(n) -> O(1): a generator hands over one item at a time%s" % ("; any/all also stop at the first decisive item" if n.func.id in ("any", "all") else ""),
                            "identical result for a plain expression; if the expression has side effects (printing, changing things), any/all would now stop early", "high",
                            "genexp:%d:%s" % (n.lineno, n.func.id), [{"at": a, "old": src, "new": "(" + src[1:-1] + ")"}]))
    # --- sorted(xs)[0] / [-1]: a whole sort just to get the smallest / largest
    for n in ast.walk(tree):
        if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Name) and n.value.func.id == "sorted" \
                and len(n.value.args) == 1 and not isinstance(n.value.args[0], ast.Starred):
            idx = n.slice
            if isinstance(idx, ast.Constant) and idx.value == 0:
                which = 0
            elif isinstance(idx, ast.UnaryOp) and isinstance(idx.op, ast.USub) and isinstance(idx.operand, ast.Constant) and idx.operand.value == 1:
                which = -1
            else:
                continue
            kws = {k.arg: k.value for k in n.value.keywords}
            if None in kws or not set(kws) <= {"key", "reverse"}:
                continue
            rev = False
            if "reverse" in kws:
                if not (isinstance(kws["reverse"], ast.Constant) and isinstance(kws["reverse"].value, bool)):
                    continue
                rev = kws["reverse"].value
            if which == -1 and (kws and not (set(kws) == {"reverse"} and rev is False)):
                continue                       # [-1] with a key can pick a different one of several equal items
            if which == -1 and "key" in kws:
                continue
            fn = ("max" if rev else "min") if which == 0 else "max"
            args = seg(n.value.args[0])[2] + ((", key=" + seg(kws["key"])[2]) if "key" in kws else "")
            sa, sb, ssrc = seg(n)
            out.append(prop("speed", rel, n.lineno, "`sorted(...)[%s]` sorts everything just to get one item" % ("0" if which == 0 else "-1"),
                            "time O(n log n) -> O(n) with `%s(...)`; memory O(n) -> O(1) (no sorted copy)" % fn,
                            "same item is returned; the only difference: an empty collection raises ValueError instead of IndexError", "high",
                            "sortedidx:%d:%s" % (n.lineno, fn), [{"at": sa, "old": ssrc, "new": "%s(%s)" % (fn, args)}]))
    # --- recursion that repeats work
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            calls = [c for c in ast.walk(n) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id == n.name]
            decos = " ".join(ast.dump(d) for d in n.decorator_list)
            if len(calls) >= 2 and "cache" not in decos and "memo" not in text.lower().split(n.name, 1)[0][-400:]:
                out.append(prop("speed", rel, n.lineno, "`%s` calls itself %d times - the same inputs are probably solved again and again" % (n.name, len(calls)),
                                "time can drop from exponential (about 2^n) to linear (n) with `@functools.cache` (remembers answers); memory +1 stored answer per distinct input",
                                "inputs must be hashable; very deep recursion still hits Python's recursion limit; skip if the function has side effects", "medium", "recur:%s" % n.name))
    return out


_PROJ = None


def insights_token_files(name):
    return _PROJ.token_files.get(name, set()) if _PROJ else set()


# ---------------------------------------------------------------------------------------------------------------
# SPEED / MEMORY - JavaScript / TypeScript (patterns, with brace matching)
# ---------------------------------------------------------------------------------------------------------------
LOOP_HEAD = re.compile(r"\b(?:for|while)\s*\(|\.(?:forEach|map|filter|reduce|flatMap|some|every)\s*\(")
ARRAYISH = re.compile(r"\b(?:const|let|var)\s+(\w+)\s*(?::[^=]+)?=\s*(?:\[|Array\.from\(|new Array|[\w.\]\)]+\.(?:map|filter|slice|split|concat)\(|Object\.(?:keys|values|entries)\()")


def match_brace(t, i):
    depth, n = 0, len(t)
    while i < n:
        c = t[i]
        if c in "'\"`":
            q = c
            i += 1
            while i < n and t[i] != q:
                i += 2 if t[i] == "\\" else 1
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def js_findings(rel, text, proj):
    out = []
    code = insights.code_only(text)
    arrays = set(m.group(1) for m in ARRAYISH.finditer(code))
    const_sets = set()
    # constant array used only with .includes -> Set
    for m in re.finditer(r"^(const)\s+(\w+)\s*=\s*\[([^\[\]]*)\]\s*;?[ \t]*$", code, re.M):
        name, inner = m.group(2), m.group(3)
        items = [x.strip() for x in inner.split(",") if x.strip()]
        if len(items) < 4 or not all(re.fullmatch(r"'[^'\\\n]*'|\"[^\"\\\n]*\"|-?\d+(?:\.\d+)?", x) for x in items):
            continue
        if proj.token_files.get(name, set()) - {rel}:
            continue
        occ = [x for x in re.finditer(r"\b%s\b" % re.escape(name), code) if x.start() != m.start(2)]
        incl = [x for x in re.finditer(r"\b%s\.includes\(" % re.escape(name), code)]
        if occ and len(occ) == len(incl):
            lb = m.start(0) + code[m.start(0):].index("[")
            patches = [{"at": lb, "old": "[" + inner + "]", "new": "new Set([" + inner + "])"}]
            for x in incl:
                patches.append({"at": x.start() + len(name) + 1, "old": "includes(", "new": "has("})
            const_sets.add(name)
            out.append(prop("speed", rel, insights.line_of(text, m.start()), "`%s` is a fixed list used only to ask 'is X in here?' - make it a Set" % name,
                            "each check goes from scanning up to %d items (time O(n)) to one lookup (O(1)); small extra memory for the hash table" % len(items),
                            "none for membership checks (`.includes` and `Set.has` compare values the same way); order is never used", "high", "const-set:" + name, patches))
    for hm in LOOP_HEAD.finditer(code):
        j = code.find("{", hm.end())
        k = code.find("\n", hm.end())
        if j < 0 or (k >= 0 and j > hm.end() + 300):
            continue
        e = match_brace(code, j)
        if e < 0:
            continue
        body = code[j:e]
        base = j
        for m in re.finditer(r"\b(\w+)\.(includes|indexOf|find|findIndex)\(", body):
            if m.group(1) in arrays and m.group(1) not in const_sets:
                out.append(prop("speed", rel, insights.line_of(text, base + m.start()),
                                "`%s.%s(...)` inside a loop scans the whole array every time" % (m.group(1), m.group(2)),
                                "time O(n*m) -> O(n+m): build a `Set` (or `Map` keyed by what you search for) once before the loop; memory +1 hash table",
                                "a Set drops duplicates and loses order; fine for pure lookups", "medium", "js-in:%s:%d" % (m.group(1), insights.line_of(text, base + m.start()))))
        for m in re.finditer(r"\b(\w+)\s*=\s*\1\.concat\(|\b(\w+)\s*=\s*\[\s*\.\.\.\2\b", body):
            nm = m.group(1) or m.group(2)
            out.append(prop("speed", rel, insights.line_of(text, base + m.start()), "`%s = [...%s, x]` / `.concat` in a loop copies the whole array every time" % (nm, nm),
                            "time O(n^2) overall -> O(n) with `%s.push(...)`; memory: no repeated temporary copies" % nm,
                            "other references to the same array would now see the additions", "medium", "js-concat:%d" % insights.line_of(text, base + m.start())))
        for m in re.finditer(r"\.\.\.(?:acc|accum|memo|result|prev)\b", body):
            if ".reduce(" in code[max(0, hm.start() - 5): hm.end() + 10] or ".reduce(" in code[hm.start():hm.end()]:
                out.append(prop("speed", rel, insights.line_of(text, base + m.start()), "spreading the accumulator inside `reduce` copies it on every step",
                                "time O(n^2) -> O(n): push into one array (or assign to one object) instead of rebuilding it", "reduce's result must not be shared mid-way", "medium", "js-reduce:%d" % insights.line_of(text, base + m.start())))
                break
        for m in re.finditer(r"\.innerHTML\s*\+=", body):
            out.append(prop("speed", rel, insights.line_of(text, base + m.start()), "`innerHTML +=` inside a loop re-parses the whole element on every pass",
                            "time O(n^2) -> O(n): build one string (or a DocumentFragment) and write it once; fewer layouts, less memory churn",
                            "event listeners attached to old child nodes would be lost by the += form - the fix keeps them", "medium", "js-innerhtml:%d" % insights.line_of(text, base + m.start())))
        for m in re.finditer(r"document\.(?:querySelector(?:All)?|getElementById|getElementsBy\w+)\(", body):
            out.append(prop("speed", rel, insights.line_of(text, base + m.start()), "the page is searched for an element inside a loop",
                            "time: one DOM search per pass -> one search total if the element is looked up before the loop", "only if the element cannot change during the loop", "low", "js-dom:%d" % insights.line_of(text, base + m.start())))
            break
        for m in re.finditer(r"\b(\w+)\.shift\(\)", body):
            out.append(prop("speed", rel, insights.line_of(text, base + m.start()), "`%s.shift()` inside a loop moves every remaining item each time" % m.group(1),
                            "time O(n) per shift -> O(1) with an index pointer (or a linked queue)", "check other users of the array", "low", "js-shift:%d" % insights.line_of(text, base + m.start())))
            break
    dedup = {}
    for f in out:
        dedup[f["id"]] = f
    return list(dedup.values())


# ---------------------------------------------------------------------------------------------------------------
def analyse(root):
    global _PROJ
    proj = insights.Project(root)
    _PROJ = proj
    fs = stack_advice(proj)
    for rel, text in proj.texts.items():
        ext = insights.ext_of(Path(rel))
        if rel.startswith(("tests/", "test/")) or "/tests/" in rel or "/test/" in rel or "node_modules" in rel:
            continue
        if ext == "py":
            fs += py_findings(rel, text)
        elif ext in JS_EXT and not text.count("\n") > 20000:
            fs += js_findings(rel, text, proj)
    uniq = {}
    for f in fs:
        uniq[f["id"]] = f
    return proj, sorted(uniq.values(), key=lambda f: ({"high": 0, "medium": 1, "low": 2}[f["confidence"]], f["file"], f["line"]))


def main():
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Arachnid advisor - lighter frameworks and faster code (proposals only; a human approves)")
    ap.add_argument("root", nargs="?", default=str(HERE.parent))
    ap.add_argument("--all", action="store_true", help="also show low-confidence hints")
    ap.add_argument("--out", default=str(HERE / "reports" / "advisor-report.json"))
    a = ap.parse_args()
    proj, fs = analyse(a.root)
    queue = [f for f in fs if f["confidence"] in ("high", "medium") or f["kind"] == "framework"]
    cur = proposals.sync("advisor", queue, proj.root)
    print("Arachnid advisor - looking at: %s" % proj.root)
    print("Nothing is changed. Every idea waits for your approval.\n")
    groups = [("framework", "STACK  - lighter frameworks / libraries"), ("speed", "SPEED  - code that slows down as data grows"), ("memory", "MEMORY - code that holds more than it needs")]
    shown = 0
    for kind, head in groups:
        items = [f for f in fs if f["kind"] == kind and (a.all or f["confidence"] != "low" or kind == "framework")]
        if not items:
            continue
        print(head)
        for f in items[:12]:
            st = cur.get(f["id"], {}).get("status", "pending")
            print("  [%s] (%s confidence, %s)  %s:%s" % (f["id"], f["confidence"], st, f["file"], f["line"]))
            print("      %s" % f["title"])
            print("      gain: %s" % f["benefit"])
            print("      risk: %s" % f["risk"])
            print("      %s" % ("exact edit ready - waits for your approval" if f["patches"] else "for a human to do"))
            shown += 1
        if len(items) > 12:
            print("  ... and %d more (python proposals.py lists them all)" % (len(items) - 12))
        print()
    if not shown:
        print("Nothing found. (Stack advice covers npm and pip projects; speed advice covers Python and JavaScript/TypeScript.)")
    else:
        print("To say yes to one:  python arachnid.py approve <id>      all exact edits:  python arachnid.py approve all")
        print("To apply what you approved (through the safety gate):  python arachnid.py apply-approved")
    try:
        import ledger
        ledger.reset("advisor")
        for f in fs:
            if f["confidence"] == "low" and f["kind"] != "framework":
                continue
            st = cur.get(f["id"], {}).get("status", "pending")
            if st in ("rejected", "applied"):
                continue
            ledger.record("advisor", "decision" if f["patches"] else "idea", "info", f["file"], f["line"], "[%s] %s" % (f["id"], f["title"]), "", f["benefit"], f["kind"])
    except Exception:
        pass
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"root": str(proj.root), "count": len(fs), "findings": fs}, indent=1, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
