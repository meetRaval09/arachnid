#!/usr/bin/env python3
"""
Arachnid - the BEHAVIOUR GATE: "does the product still look and act exactly the same?"
=======================================================================================
Every tool that moves, merges or removes files can ask this gate for a final answer BEFORE it touches your
project. The gate works on temporary copies, never on your real files:

   copy A = the project as it is now
   copy B = the project with the planned change applied

Both copies are built (with the project's own build command, e.g. `npm run build`) and served on this computer.
A real headless browser (Chromium through Playwright) then opens every page of both and compares:

   1. NETWORK  - every file the page loads: how many, their status (200/404 ...) and a fingerprint of the content.
   2. PAGE     - the page's HTML after it has loaded, and the exact position + size of every element.
   3. PIXELS   - a full-page screenshot at desktop and phone width, compared pixel by pixel.

If ANY of these differs - even one pixel, even one extra 404 - the change is NOT applied and the owner decides.
Things that move by themselves (animations, WebGL, videos) are measured twice on copy A first; a signal that
is not stable on its own cannot prove anything, so it is reported as "not verifiable" and (by default) also
sends the change to the owner.  Use allow_unstable=True only if you accept that.

Needs: Python 3.10+, `pip install playwright` + `playwright install chromium`  (the cloud runner has them).
"""
import hashlib
import json
import re
import os
import shutil
import socket
import subprocess
import tempfile
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

VIEWPORTS = [("desktop", 1280, 800), ("phone", 390, 844)]
OUT_DIRS = ("dist", "build", "out", ".output/public")
IGNORE_COPY = ("node_modules", ".git", "dist", "build", "backups", ".cache", ".wrangler", "arachnid", "reports")
FREEZE_CSS = "*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}"
BOXES_JS = """() => Array.from(document.querySelectorAll('body *')).map(e => {
  const r = e.getBoundingClientRect();
  return [e.tagName, Math.round(r.x*100)/100, Math.round(r.y*100)/100, Math.round(r.width*100)/100, Math.round(r.height*100)/100];
})"""


class Unavailable(Exception):
    """The browser part of the gate cannot run on this computer."""


class NoPages(Exception):
    """The project has no HTML pages to look at."""


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def serve(directory):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(Quiet, directory=str(directory)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def detect_build(pkg):
    try:
        data = json.loads((pkg / "package.json").read_text(encoding="utf-8"))
        if "build" in (data.get("scripts") or {}):
            return "npm run build"
    except Exception:
        pass
    return None


def make_copy(src, dst):
    shutil.copytree(src, dst, symlinks=True, ignore=shutil.ignore_patterns(*IGNORE_COPY))
    nm = Path(src) / "node_modules"
    if nm.exists():
        os.symlink(nm, Path(dst) / "node_modules", target_is_directory=True)


def build(work, cmd):
    if cmd is None:
        return work
    r = subprocess.run(cmd, cwd=str(work), shell=True, capture_output=True, text=True, timeout=900)
    if r.returncode != 0:
        raise RuntimeError("build failed: " + (r.stderr or r.stdout)[-300:])
    for d in OUT_DIRS:
        if (work / d).is_dir():
            return work / d
    return work


def pages_in(site, limit=40):
    out = []
    for dp, dn, fn in os.walk(site):
        dn[:] = [d for d in dn if d not in ("node_modules", ".git")]
        for f in sorted(fn):
            if f.lower().endswith((".html", ".htm")):
                out.append(os.path.relpath(os.path.join(dp, f), site).replace(os.sep, "/"))
    return sorted(out)[:limit]


NORMALISE_JS = """(m) => {
  const els = Array.from(document.querySelectorAll('[src],[href],[poster],[data-src]'));
  const clone = document.implementation.createHTMLDocument('').importNode(document.documentElement, true);
  const cels = Array.from(clone.querySelectorAll('[src],[href],[poster],[data-src]'));
  els.forEach((el, i) => {
    for (const a of ['src', 'href', 'poster', 'data-src']) {
      const v = el.getAttribute(a);
      if (v === null) continue;
      let abs; try { abs = new URL(v, document.baseURI).href; } catch (e) { continue; }
      if (m[abs]) cels[i].setAttribute(a, 'sha:' + m[abs]);
    }
  });
  return clone.outerHTML;
}"""


def snapshot(browser, port, page_path, vw):
    """Open one page; return what it loads, its HTML (paths replaced by content fingerprints), layout, errors, pixels."""
    name, w, h = vw
    ctx = browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=1)
    page = ctx.new_page()
    net, urlmap, errors = [], {}, []

    def on_response(resp):
        try:
            body = resp.body()
        except Exception:
            body = b"<unreadable>"
        sha = hashlib.sha1(body).hexdigest()[:16]
        urlmap[resp.url] = sha
        kind = resp.request.resource_type
        # the TEXT of pages / styles / scripts legitimately contains file paths, so only their existence is compared;
        # everything else (images, fonts, media, data) is compared by content
        net.append((resp.status, kind, "" if kind in ("document", "stylesheet", "script") else sha))
    page.on("response", on_response)
    page.on("requestfailed", lambda req: net.append(("FAILED", req.resource_type, req.url.rsplit("/", 1)[-1][:40])))
    page.on("pageerror", lambda e: errors.append(re.sub(r"https?://\S+", "<url>", str(e))[:120]))
    page.on("console", lambda m: errors.append(re.sub(r"https?://\S+", "<url>", m.text)[:120]) if m.type == "error" else None)
    try:
        page.goto("http://127.0.0.1:%d/%s" % (port, page_path), wait_until="networkidle", timeout=30000)
    except Exception:
        pass
    page.add_style_tag(content=FREEZE_CSS)
    try:
        page.evaluate("document.fonts && document.fonts.ready")
    except Exception:
        pass
    page.wait_for_timeout(400)
    snap = {"net": sorted(map(str, net)), "html": page.evaluate(NORMALISE_JS, urlmap), "boxes": page.evaluate(BOXES_JS),
            "errors": sorted(errors), "png": page.screenshot(full_page=True)}
    ctx.close()
    return snap


def same_png(a, b):
    if a == b:
        return True
    try:
        from PIL import Image, ImageChops
        import io
        ia, ib = Image.open(io.BytesIO(a)).convert("RGBA"), Image.open(io.BytesIO(b)).convert("RGBA")
        return ia.size == ib.size and ImageChops.difference(ia, ib).getbbox() is None
    except Exception:
        return False


def compare(root, apply_fn, build_cmd="auto", allow_unstable=False, log=print, repair=False):
    """root: project folder.  apply_fn(copy_root): applies the planned change to a COPY.
    Returns (ok, [lines explaining the verdict])."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise Unavailable("the browser check needs Playwright:  pip install playwright   then   playwright install chromium")
    root = Path(root)
    tmp = Path(tempfile.mkdtemp(prefix="arachnid-gate-"))
    notes = []
    try:
        a_root, b_root = tmp / "A", tmp / "B"
        make_copy(root, a_root)
        make_copy(root, b_root)
        apply_fn(b_root)
        cmd = detect_build(root) if build_cmd == "auto" else build_cmd
        a_broken = False
        try:
            a_site = build(a_root, cmd)
        except RuntimeError:
            if not repair:
                raise
            a_broken, a_site = True, None       # a repair of broken code: the "before" may not even build
        b_site = build(b_root, cmd)
        if a_broken:
            a_site = a_root                     # no usable "before": only the pages of the repaired version are examined
        pa, pb = pages_in(a_site) if not a_broken else pages_in(b_site), pages_in(b_site)
        if pa != pb:
            return False, ["The list of pages differs: only before=%s only after=%s" % (sorted(set(pa) - set(pb))[:3], sorted(set(pb) - set(pa))[:3])]
        if not pa:
            raise NoPages()
        sa, porta = serve(a_site)
        sb, portb = serve(b_site)
        problems, unstable, repaired = [], [], []
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            for page_path in pa:
                for vw in VIEWPORTS:
                    b1 = snapshot(browser, portb, page_path, vw)
                    tag = "%s [%s]" % (page_path, vw[0])
                    if a_broken:
                        a1 = a2 = None
                    else:
                        a1 = snapshot(browser, porta, page_path, vw)
                        a2 = snapshot(browser, porta, page_path, vw)
                    a_has_errors = a_broken or bool(a1["errors"]) or any("FAILED" in x for x in a1["net"])
                    if repair and a_has_errors:
                        # this page was broken before the repair, so it is allowed to change - but it must now be clean
                        if b1["errors"] or any("FAILED" in x for x in b1["net"]):
                            problems.append("%s: still has browser errors after the repair: %s" % (tag, (b1["errors"] or ["a failed request"])[0]))
                        else:
                            repaired.append(tag)
                        continue
                    # --- each signal is only meaningful if copy A agrees with itself
                    for sig in ("net", "html", "boxes", "errors"):
                        stable = a1[sig] == a2[sig]
                        if stable and a1[sig] != b1[sig]:
                            problems.append("%s: %s differs after the change" % (tag, {"net": "the files the page loads", "html": "the page HTML", "boxes": "the position/size of an element", "errors": "the browser errors"}[sig]))
                        elif not stable:
                            unstable.append("%s: %s changes by itself between two loads" % (tag, sig))
                    if same_png(a1["png"], a2["png"]):
                        if not same_png(a1["png"], b1["png"]):
                            problems.append("%s: the screenshot differs by at least one pixel" % tag)
                    else:
                        unstable.append("%s: the picture changes by itself (animation / WebGL / video)" % tag)
            browser.close()
        sa.shutdown()
        sb.shutdown()
        if problems:
            return False, problems[:8]
        if unstable and not allow_unstable:
            return False, ["No difference was found, but it cannot be PROVEN: " + "; ".join(sorted(set(unstable))[:4])
                           + ".  (Use --allow-unstable to accept the signals that are stable.)"]
        if repaired:
            notes.append("%d page view(s) were broken before and now load without any browser error; all other pages are identical." % len(repaired))
        notes.append("Checked %d page(s) x %d screen sizes: files loaded, page HTML, element positions and pixels are identical%s."
                     % (len(pa), len(VIEWPORTS), " (except the parts that move by themselves)" if unstable else ""))
        return True, notes
    except (Unavailable, NoPages):
        raise
    except Exception as e:
        return False, ["Behaviour gate could not finish: %s" % str(e)[:300]]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def tree_hash(base):
    h = {}
    for dp, dn, fn in os.walk(base):
        dn[:] = [d for d in dn if d not in ("node_modules", ".git")]
        for f in fn:
            p = os.path.join(dp, f)
            try:
                h[os.path.relpath(p, base).replace(os.sep, "/")] = hashlib.sha1(open(p, "rb").read()).hexdigest()
            except OSError:
                pass
    return h


def detect_tests(pkg):
    try:
        data = json.loads((pkg / "package.json").read_text(encoding="utf-8"))
        if "test" in (data.get("scripts") or {}) and "no test specified" not in data["scripts"]["test"]:
            return "npm test --silent"
    except Exception:
        pass
    if (pkg / "tests").is_dir() or (pkg / "test").is_dir() or list(pkg.glob("test_*.py")):
        return "python -m pytest -q -x"
    return None


def compare_without_pages(root, apply_fn, build_cmd="auto", repair=False):
    """Projects with no web pages (libraries, back-ends, tools): the build output must be byte-for-byte identical
    and the tests must still pass; with neither a build nor tests there is nothing that could prove safety."""
    root = Path(root)
    tmp = Path(tempfile.mkdtemp(prefix="arachnid-gate-"))
    try:
        a_root, b_root = tmp / "A", tmp / "B"
        make_copy(root, a_root)
        make_copy(root, b_root)
        apply_fn(b_root)
        cmd = detect_build(root) if build_cmd == "auto" else build_cmd
        lines = []
        if cmd:
            try:
                a_out = build(a_root, cmd)
            except RuntimeError:
                if not repair:
                    raise
                b_out = build(b_root, cmd)
                return True, ["The project did not build before the repair; after it, it builds."]
            b_out = build(b_root, cmd)
            if a_out != a_root:
                ha, hb = tree_hash(a_out), tree_hash(b_out)
                if ha != hb:
                    diff = sorted(set(ha) ^ set(hb)) or sorted(k for k in ha if ha[k] != hb.get(k))
                    return False, ["The build output is not identical after the change (e.g. %s)" % ", ".join(diff[:3])]
                lines.append("The build output is byte-for-byte identical (%d files)." % len(ha))
            else:
                lines.append("The project builds after the change.")
        tests = detect_tests(root)
        if tests:
            ra = subprocess.run(tests, cwd=str(a_root), shell=True, capture_output=True, text=True, timeout=900)
            rb = subprocess.run(tests, cwd=str(b_root), shell=True, capture_output=True, text=True, timeout=900)
            if ra.returncode == 0 and rb.returncode != 0:
                return False, ["The tests passed before the change and FAIL after it: " + (rb.stdout + rb.stderr)[-200:]]
            lines.append("Tests: same result before and after (%s)." % ("pass" if rb.returncode == 0 else "failing both times"))
        if not cmd and not tests:
            return False, ["This project has no web pages, no build and no tests, so nothing can prove the change is harmless."]
        return True, lines
    except Exception as e:
        return False, ["Behaviour gate could not finish: %s" % str(e)[:300]]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
