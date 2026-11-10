#!/usr/bin/env python3
"""
ARACHNID - PAGE HEALTH  (looks only, never changes anything)
============================================================
    python pagecheck.py [folder]

Opens every page of the project in a real browser (Chromium, the engine of Chrome and Edge) - exactly what you
would do by hand with DevTools open on the Console and Network tabs - and reports what goes wrong while it loads:

    a JavaScript error           "Cannot read properties of null (reading 'addEventListener')"   (the page half works)
    an error in the console      console.error(...) written while the page loads
    a file that fails to load    a picture, style, script or font that answers 404 (Not found)

If the project is built (package.json has a "build" script) the BUILT pages are opened, in a temporary copy; the
project folder itself is never touched.  Requests to your own backend (/api/...) cannot be answered here, so
errors on pages that depend on them are marked as ideas, not decisions.

Switch it off with  "pagecheck": false  in arachnid.config.json, or ARACHNID_NO_BROWSER=1.
"""
import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse, unquote

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ledger  # noqa: E402
import links  # noqa: E402
import pixelcheck  # noqa: E402

SKIP_DIRS = {"node_modules", ".git", "backups", "reports", "arachnid", ".cache", "coverage", "__pycache__", ".venv", "venv",
             "test", "tests", "__tests__", "fixtures", "e2e", "cypress", "vendor"}
MAX_PAGES = 40
DATA_TYPES = ("fetch", "xhr", "websocket", "eventsource", "other")
PERMISSION = re.compile(r"GeolocationPositionError|NotAllowedError|Permission(s)? (denied|dismissed)|permission was denied|"
                        r"NotReadableError|SecurityError: .*(camera|microphone|clipboard|notification)", re.I)

RULES = {
    "page-error": ("decision", "a JavaScript error while the page loads",
                   "the script stops at this error, so everything it was going to do after it (buttons, menus, data) "
                   "silently does not happen", "open the page with DevTools (F12) > Console to see it, and fix the line"),
    "page-console-error": ("decision", "an error written to the console while the page loads",
                           "visitors do not see it, but something on the page reported that it failed",
                           "open DevTools (F12) > Console on this page"),
    "page-missing-file": ("decision", "a file the page asks for does not exist",
                          "the browser gets 404 (Not found): a picture is missing, a style or script is not applied",
                          "fix the path, or add the file"),
}


def switched_off(root):
    if os.environ.get("ARACHNID_NO_BROWSER") in ("1", "true", "yes"):
        return True
    try:
        cfg = json.loads((Path(root) / "arachnid.config.json").read_text(encoding="utf-8"))
        return cfg.get("pagecheck") is False
    except (OSError, ValueError):
        return False


def pages_in(site):
    out = []
    for dp, dn, fn in os.walk(site):
        dn[:] = sorted(d for d in dn if d not in SKIP_DIRS and not d.startswith("."))
        for f in sorted(fn):
            if f.lower().endswith((".html", ".htm")):
                out.append(os.path.relpath(os.path.join(dp, f), site).replace(os.sep, "/"))
    out.sort(key=lambda p: (p.count("/"), p != "index.html", p))      # the main pages first
    return out[:MAX_PAGES]


def local_path(url, port, prefix):
    """http://127.0.0.1:PORT/js/app.js -> js/app.js (inside the project; prefix = the build folder, if any)"""
    u = urlparse(url)
    if u.hostname not in ("127.0.0.1", "localhost") or u.port != port:
        return None
    return (prefix + unquote(u.path).lstrip("/")) or "index.html"


def where(stack, port, prefix):
    """The first line of a stack trace that is in one of the project's own files -> (file, line)."""
    for m in re.finditer(r"(https?://[^\s()]+?):(\d+):(\d+)", stack or ""):
        p = local_path(m.group(1), port, prefix)
        if p:
            return p, int(m.group(2))
    return None, 0


def tidy(msg, port):
    return re.sub(r"https?://(127\.0\.0\.1|localhost):%d/" % port, "/", str(msg)).strip().splitlines()[0][:200] if msg else ""


def line_of(page_file, wanted):
    """The line of the page that mentions this file (so the report can point at it)."""
    try:
        text = Path(page_file).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    name = wanted.rsplit("/", 1)[-1]
    for i, ln in enumerate(text.splitlines(), 1):
        if name and name in ln:
            return i
    return 0


def visit(browser, port, page_path, prefix, has_modules=True, declared=()):
    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    page = ctx.new_page()
    errors, console, missing, data_failed, external_failed = [], [], [], [], set()

    def on_response(resp):
        if resp.status < 400:
            return
        p = local_path(resp.url, port, prefix)
        kind = resp.request.resource_type
        if p is None:
            if kind in ("script",) + DATA_TYPES:
                external_failed.add(urlparse(resp.url).hostname)
            return
        if kind in DATA_TYPES:
            data_failed.append(unquote(urlparse(resp.url).path))
        elif p.endswith("favicon.ico") or ("node_modules/" in p and (not has_modules or links.package_of(p) in declared)):
            return                                  # a package listed in package.json but not installed: run npm install
        else:
            missing.append((p, resp.status, kind))

    def on_failed(req):
        if local_path(req.url, port, prefix) is None:
            if req.resource_type in DATA_TYPES:
                external_failed.add("!" + (urlparse(req.url).hostname or ""))      # data first: the likely reason
            elif req.resource_type == "script":
                external_failed.add(urlparse(req.url).hostname)
        elif req.resource_type in DATA_TYPES:
            data_failed.append(unquote(urlparse(req.url).path))

    def on_console(m):
        if m.type != "error":
            return
        text = m.text or ""
        if text.startswith("Failed to load resource") or "net::ERR_" in text:
            return                                  # the file itself is reported (or it is an outside address)
        loc = m.location or {}
        f = local_path(loc.get("url") or "", port, prefix)
        console.append((tidy(text, port), f, (loc.get("lineNumber") or 0) + 1 if f else 0))

    def on_error(e):
        stack = getattr(e, "stack", "") or ""
        f, ln = where(stack, port, prefix)
        errors.append((tidy(getattr(e, "message", "") or str(e), port), f, ln))

    page.on("response", on_response)
    page.on("requestfailed", on_failed)
    page.on("console", on_console)
    page.on("pageerror", on_error)
    try:
        page.goto("http://127.0.0.1:%d/%s" % (port, page_path), wait_until="networkidle", timeout=20000)
    except Exception:
        pass
    try:
        page.wait_for_timeout(700)                  # errors from code that runs right after loading
    except Exception:
        pass
    ctx.close()
    return errors, console, missing, sorted(set(data_failed)), [h.lstrip("!") for h in sorted(external_failed) if h.strip("!")]


def check(root, log=print):
    from playwright.sync_api import sync_playwright
    root = Path(root).resolve()
    tmp = None
    prefix = ""
    try:
        cmd = pixelcheck.detect_build(root)
        if cmd:
            if not (root / "node_modules").exists():
                log("  This project is built with `%s`, but its packages are not installed (no node_modules folder)."
                    "\n  Run `npm install` once, then scan again, to open the built pages." % cmd)
                return None
            tmp = Path(tempfile.mkdtemp(prefix="arachnid-pages-"))
            work = tmp / "site"
            pixelcheck.make_copy(root, work)
            site = pixelcheck.build(work, cmd)
            prefix = (os.path.relpath(site, work).replace(os.sep, "/") + "/") if site != work else ""
        else:
            site = root
        pages = pages_in(site)
        if not pages:
            log("  No HTML pages to open.")
            return []
        srv, port = pixelcheck.serve(site)
        found = {}
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch()
                for page_path in pages:
                    log("  opening %s" % (prefix + page_path))
                    errors, console, missing, data_failed, ext = visit(browser, port, page_path, prefix, (site / "node_modules").exists(), links.declared_packages(root))
                    hint = ""
                    if data_failed:
                        hint = (" (this page asks a server for %s, which only your backend can answer; if that is the "
                                "reason, start the backend and check again)" % ", ".join(data_failed[:2]))
                    elif ext:
                        hint = " (%s could not be reached; if that is the reason, check your internet)" % ext[0]
                    for kind, items in (("page-error", errors), ("page-console-error", console)):
                        for msg, f, ln in items:
                            h, pile = hint, "idea" if hint else "decision"
                            if PERMISSION.search(msg):
                                h, pile = (" (the page asked for a permission - location, camera, notifications ... - "
                                           "that the test browser does not give; check that the page copes when a "
                                           "visitor says no)"), "idea"
                            key = (kind, msg, f or prefix + page_path, ln)
                            found.setdefault(key, [pile, h, []])[2].append(prefix + page_path)
                    for p, status, kind in missing:
                        key = ("page-missing-file", "%s (%s, answer %d)" % (p, kind, status), prefix + page_path,
                               line_of(site / page_path, p))
                        found.setdefault(key, ["decision", "", []])[2].append(prefix + page_path)
                browser.close()
        finally:
            srv.shutdown()
        out = []
        for (kind, msg, f, ln), (pile, hint, on) in found.items():
            on = sorted(set(on))
            where_txt = "" if on == [f] else (" - on %s" % on[0] if len(on) == 1 else " - on %d pages (%s, ...)" % (len(on), on[0]))
            out.append({"file": f, "line": ln, "kind": kind, "pile": pile, "detail": msg + where_txt + hint})
        return sorted(out, key=lambda x: (x["file"], x["line"]))
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description="Arachnid page health: open every page in a real browser (looks only)")
    ap.add_argument("root", nargs="?", default=str(HERE.parent))
    ap.add_argument("--out", default=str(HERE / "reports" / "pages-report.json"))
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    print("Arachnid page health - opening the pages of:", Path(a.root).resolve())
    if switched_off(a.root):
        print("  Switched off (arachnid.config.json \"pagecheck\": false, or ARACHNID_NO_BROWSER).")
        return 0
    try:
        import playwright  # noqa: F401
    except ImportError:
        print("  Skipped: opening pages needs Playwright:  pip install playwright   then   playwright install chromium")
        return 0
    try:
        found = check(a.root)
    except Exception as e:
        print("  Could not open the pages: %s" % str(e)[:300].replace("run fix again", "scan again"))
        return 0
    if found is None:
        return 0
    ledger.reset("pages")
    for f in found:
        _, title, why, todo = RULES[f["kind"]]
        what = title + ": " + f["detail"]
        print("\n  %s  line %d   [%s]  %s\n     %s" % (f["file"], f["line"], f["pile"].upper(), f["kind"], what))
        ledger.record("pages", "found" if f["pile"] == "decision" else "idea",
                      "warning" if f["pile"] == "decision" else "info", f["file"], f["line"], what, why, todo, f["kind"])
    dec = sum(1 for f in found if f["pile"] == "decision")
    print("\n---------------- Summary ----------------")
    print("Problems while loading pages: %d    Ideas: %d" % (dec, len(found) - dec))
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps({"root": str(Path(a.root).resolve()), "findings": found}, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
