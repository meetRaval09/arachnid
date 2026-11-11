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
    "page-overflow": ("decision", "the page is wider than the screen",
                      "something sticks out to the right, so visitors get a sideways scrollbar and the page wobbles "
                      "left and right on phones", "give that element max-width: 100% (or let it wrap / shrink)"),
    "page-sticky-trapped": ("decision", "position: sticky that never sticks",
                            "sticky only works when no box around it cuts off overflow; here a parent with overflow "
                            "hidden/auto (that does not scroll itself) traps it, or no top / bottom is set",
                            "remove overflow from that parent (or use overflow: clip), and give the sticky element top: 0"),
    "page-invisible-text": ("decision", "text in the same colour as its background",
                            "the text is there but nobody can see it", "change the text colour or the background"),
    "page-zindex-ignored": ("idea", "z-index that does nothing",
                            "z-index only works on positioned elements (position: relative / absolute / fixed / sticky) "
                            "or flex / grid children; this element is neither, so it is not lifted above anything",
                            "add position: relative, or remove the z-index"),
    "page-low-contrast": ("idea", "text that is hard to read",
                          "the text colour is too close to its background (contrast below 3:1; 4.5:1 is the "
                          "accessibility guideline)", "darken the text or lighten the background"),
}

MEASURE_JS = r"""
(phone) => {
  const out = {overflow: null, sticky: [], invisible: [], low: [], lowCount: 0};
  const de = document.documentElement, vw = de.clientWidth;
  const desc = (e) => ({tag: e.tagName.toLowerCase(), id: e.id || "", cls: (typeof e.className === "string" ? e.className : "").trim().split(/\s+/).filter(Boolean).slice(0, 3).join(" ")});
  const cs = (e) => getComputedStyle(e);
  const name = (a) => "<" + a.tagName.toLowerCase() + (a.id ? ' id="' + a.id + '"' : "") + (typeof a.className === "string" && a.className.trim() ? ' class="' + a.className.trim().slice(0, 30) + '"' : "") + ">";
  // 1. the page is wider than the screen (unless html/body cut it off: then there is no sideways scrollbar)
  const cut = (v) => v === "hidden" || v === "clip";
  const noScroll = cut(cs(de).overflowX) || (cs(de).overflowX === "visible" && document.body && cut(cs(document.body).overflowX));
  if (!noScroll && de.scrollWidth > vw + 1) {
    const all = Array.from(document.body ? document.body.querySelectorAll("*") : []);
    const sticks = new Set();
    for (const e of all) {
      const r = e.getBoundingClientRect();
      if ((r.width === 0 && r.height === 0) || r.right <= vw + 1) continue;
      let clipped = false, fixed = false;
      for (let a = e; a && a !== document.body; a = a.parentElement) {
        const st = cs(a);
        if (st.position === "fixed") { fixed = true; break; }
        if (a !== e && st.overflowX !== "visible" && a.getBoundingClientRect().right <= vw + 1) { clipped = true; break; }
      }
      if (!clipped && !fixed) sticks.add(e);
    }
    const top = Array.from(sticks).filter((e) => !sticks.has(e.parentElement));
    if (top.length) {
      const e = top[0], r = e.getBoundingClientRect();
      out.overflow = {el: desc(e), by: Math.round(r.right - vw), page: de.scrollWidth, screen: vw, more: top.length - 1};
    }
  }
  if (phone) return out;
  // 2a. z-index that does nothing: not positioned, and not a flex / grid child
  out.zindex = [];
  for (const e of Array.from(document.querySelectorAll("body *"))) {
    const st = cs(e);
    if (st.zIndex === "auto" || st.position !== "static" || st.display === "none") continue;
    const pd = e.parentElement ? cs(e.parentElement).display : "";
    if (/flex|grid/.test(pd)) continue;
    if (out.zindex.length < 5) out.zindex.push({el: desc(e), z: st.zIndex});
  }
  // 2. sticky elements that cannot stick
  const htmlVisible = cs(de).overflowY === "visible" && cs(de).overflowX === "visible";
  for (const e of Array.from(document.querySelectorAll("body *"))) {
    const st = cs(e);
    if ((st.position !== "sticky" && st.position !== "-webkit-sticky") || st.display === "none") continue;
    if (st.top === "auto" && st.bottom === "auto" && st.left === "auto" && st.right === "auto") {
      out.sticky.push({el: desc(e), why: "it has no top / bottom, so it never sticks"});
      continue;
    }
    for (let a = e.parentElement; a && a !== de; a = a.parentElement) {
      if (a === document.body && htmlVisible) break;       // body's overflow moves to the window
      const s2 = cs(a);
      const ov = [s2.overflowX, s2.overflowY];
      if (ov.some((v) => v === "hidden" || v === "auto" || v === "scroll")) {
        const scrolls = a.scrollHeight > a.clientHeight + 1 || a.scrollWidth > a.clientWidth + 1;
        if (!scrolls) out.sticky.push({el: desc(e), why: "the " + name(a) + " around it has overflow: " + (ov[0] === ov[1] ? ov[0] : ov.join(" ")) + " and does not scroll itself"});
        break;
      }
    }
  }
  // 3. invisible / hard-to-read text
  const parse = (c) => { const m = (c || "").match(/rgba?\(([^)]+)\)/); if (!m) return null; const p = m[1].split(/[ ,\/]+/).filter(Boolean).map(Number); return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1]; };
  const lum = (c) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
  const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const SKIP = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "OPTION", "TEMPLATE", "TITLE", "TEXTAREA", "SELECT"]);
  const texts = [];
  const walker = document.createTreeWalker(document.body || de, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n && texts.length < 400; n = walker.nextNode()) {
    const e = n.parentElement;
    if (!e || SKIP.has(e.tagName) || e.closest("svg") || n.textContent.trim().length < 2) continue;
    if (texts.length && texts[texts.length - 1] === e) continue;
    texts.push(e);
  }
  const sy = window.scrollY;
  for (const e of texts) {
    const st = cs(e);
    if (st.visibility !== "visible" || st.display === "none") continue;
    if ((st.webkitBackgroundClip || st.backgroundClip || "").includes("text")) continue;
    const fill = parse(st.webkitTextFillColor);
    if (fill && fill[3] === 0) continue;
    let op = 1;
    for (let a = e; a; a = a.parentElement) op *= parseFloat(cs(a).opacity);
    if (op < 0.2) continue;
    const fg = parse(st.color);
    if (!fg || fg[3] < 0.5) continue;
    window.scrollTo(0, Math.max(0, e.getBoundingClientRect().top + window.scrollY - 200));
    const r = e.getBoundingClientRect();
    if (r.width < 4 || r.height < 4 || r.bottom < 0 || r.right < 0 || r.left > vw) continue;
    const x = Math.min(vw - 1, Math.max(0, r.left + Math.min(r.width / 2, 20)));
    const y = Math.min(innerHeight - 1, Math.max(0, r.top + r.height / 2));
    const stack = document.elementsFromPoint(x, y);
    const i = stack.indexOf(e);
    if (i < 0) continue;                                    // covered by something else
    let bg = null, unknown = false;
    let floating = null;                                    // a fixed/sticky box: what is under it changes while scrolling
    for (let a = e; a && a !== de; a = a.parentElement) { const p2 = cs(a).position; if (p2 === "fixed" || p2 === "sticky") { floating = a; break; } }
    for (const b of stack.slice(i)) {
      if (floating && !floating.contains(b)) { unknown = true; break; }
      const sb = cs(b);
      if (sb.backgroundImage !== "none" || ["IMG", "VIDEO", "CANVAS", "PICTURE", "IFRAME", "svg", "EMBED", "OBJECT", "INPUT"].includes(b.tagName) ||
          (sb.backdropFilter && sb.backdropFilter !== "none")) { unknown = true; break; }
      const c = parse(sb.backgroundColor);
      if (c && c[3] >= 0.9) { bg = c; break; }
      if (c && c[3] > 0.05) { unknown = true; break; }
    }
    if (unknown) continue;
    if (!bg) {                                              // the window's background: html's, else body's (browsers spread it)
      const hb = parse(cs(de).backgroundColor), bb = document.body ? parse(cs(document.body).backgroundColor) : null;
      if ((hb && hb[3] > 0 && hb[3] < 0.9) || (cs(de).backgroundImage !== "none")) continue;
      if (hb && hb[3] >= 0.9) bg = hb;
      else if (document.body && cs(document.body).backgroundImage !== "none") continue;
      else if (bb && bb[3] >= 0.9) bg = bb;
      else if (bb && bb[3] > 0.05) continue;
      else bg = [255, 255, 255, 1];
    }
    const k = ratio(fg, bg);
    const item = {el: desc(e), text: e.textContent.trim().replace(/\s+/g, " ").slice(0, 40), fg: st.color, bg: "rgb(" + bg.slice(0, 3).join(", ") + ")", ratio: Math.round(k * 100) / 100};
    if (k < 1.1) { if (out.invisible.length < 3) out.invisible.push(item); }
    else if (k < 3) { out.lowCount++; if (!out.low.length) out.low.push(item); }
  }
  window.scrollTo(0, sy);
  return out;
}
"""


def element_line(page_file, el):
    """The line of the page where this element is written (found by its id, else its classes, else its tag)."""
    try:
        lines = Path(page_file).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return 0
    tests = []
    if el.get("id"):
        tests.append(re.compile(r"""\bid\s*=\s*["']?%s["'\s>]""" % re.escape(el["id"])))
    if el.get("cls"):
        tests.append(re.compile(r"""<%s\b[^>]*class\s*=\s*["'][^"']*\b%s\b""" % (re.escape(el["tag"]), re.escape(el["cls"].split()[0]))))
        tests.append(re.compile(r"""class\s*=\s*["'][^"']*\b%s\b""" % re.escape(el["cls"].split()[0])))
    if not el.get("id") and not el.get("cls"):
        tests.append(re.compile(r"<%s\b" % re.escape(el["tag"]), re.I))      # only the tag: its first use on the page
    for t in tests:
        for i, ln in enumerate(lines, 1):
            if t.search(ln):
                return i
    return 0


def css_rule_line(site, page_path, el, prop):
    """(css file, line) of the declaration `prop` in a rule whose selector names this element's id or class."""
    marks = (["#" + el["id"]] if el.get("id") else []) + ["." + c for c in (el.get("cls") or "").split()]
    if not marks:
        return None, 0
    page = Path(site) / page_path
    sheets = []
    try:
        html = page.read_text(encoding="utf-8", errors="replace")
        sheets.append((page_path, html))
        for href in re.findall(r"""<link[^>]+rel=["']?stylesheet[^>]*href=["']([^"'#?]+)|<link[^>]+href=["']([^"'#?]+)["'][^>]*rel=["']?stylesheet""", html, re.I):
            h = href[0] or href[1]
            if re.match(r"(?i)(https?:)?//", h):
                continue
            f = (Path(site) / h.lstrip("/")) if h.startswith("/") else (page.parent / h)
            if f.exists():
                sheets.append((os.path.relpath(f, site).replace(os.sep, "/"), f.read_text(encoding="utf-8", errors="replace")))
    except OSError:
        return None, 0
    for rel, text in sheets:
        for m in re.finditer(r"([^{}]*)\{([^{}]*)\}", text):
            sel = m.group(1)
            if any(re.search(re.escape(mk) + r"(?![\w-])", sel) for mk in marks):
                d = re.search(r"(?<![\w-])%s\s*:" % re.escape(prop), m.group(2))
                if d:
                    return rel, text.count("\n", 0, m.start(2) + d.start()) + 1
    return None, 0


def show(el):
    return "<%s%s%s>" % (el["tag"], (' id="%s"' % el["id"]) if el.get("id") else "", (' class="%s"' % el["cls"]) if el.get("cls") else "")



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


def visit(browser, port, page_path, prefix, has_modules=True, declared=(), has_viewport=False):
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
    layout = []
    try:
        wide = page.evaluate(MEASURE_JS, False)
        layout.append(("desktop", wide))
        if has_viewport:                            # the page is made for phones: measure it at phone width too
            page.set_viewport_size({"width": 375, "height": 740})
            page.wait_for_timeout(300)
            layout.append(("phone", page.evaluate(MEASURE_JS, True)))
    except Exception:
        pass
    ctx.close()
    return errors, console, missing, sorted(set(data_failed)), [h.lstrip("!") for h in sorted(external_failed) if h.strip("!")], layout


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
                    try:
                        src = (site / page_path).read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        src = ""
                    vp = bool(re.search(r"""<meta[^>]+name\s*=\s*["']?viewport[^>]+width\s*=\s*device-width""", src, re.I))
                    errors, console, missing, data_failed, ext, layout = visit(browser, port, page_path, prefix, (site / "node_modules").exists(), links.declared_packages(root), vp)
                    seen_overflow = False
                    for size, m in layout:
                        if not m:
                            continue
                        ov = m.get("overflow")
                        if ov and not seen_overflow:
                            seen_overflow = True
                            key = ("page-overflow", "on a %dpx-wide %s screen the page is %dpx wide: %s sticks out by %dpx%s" % (
                                ov["screen"], "phone" if size == "phone" else "desktop", ov["page"], show(ov["el"]), ov["by"],
                                (" (and %d more)" % ov["more"]) if ov["more"] else ""), prefix + page_path, element_line(site / page_path, ov["el"]))
                            found.setdefault(key, ["decision", "", []])[2].append(prefix + page_path)
                        for z in m.get("zindex") or []:
                            f, ln = css_rule_line(site, page_path, z["el"], "z-index")
                            key = ("page-zindex-ignored", "%s has z-index: %s but no position" % (show(z["el"]), z["z"]),
                                   f or prefix + page_path, ln or element_line(site / page_path, z["el"]))
                            found.setdefault(key, ["idea", "", []])[2].append(prefix + page_path)
                        for st in m.get("sticky") or []:
                            key = ("page-sticky-trapped", "%s: %s" % (show(st["el"]), st["why"]), prefix + page_path, element_line(site / page_path, st["el"]))
                            found.setdefault(key, ["decision", "", []])[2].append(prefix + page_path)
                        for it in m.get("invisible") or []:
                            key = ("page-invisible-text", "\"%s\" in %s: text %s on %s" % (it["text"], show(it["el"]), it["fg"], it["bg"]),
                                   prefix + page_path, element_line(site / page_path, it["el"]))
                            found.setdefault(key, ["decision", "", []])[2].append(prefix + page_path)
                        for it in m.get("low") or []:
                            more = m.get("lowCount", 1) - 1
                            key = ("page-low-contrast", "\"%s\" in %s: contrast %.2f:1%s" % (it["text"], show(it["el"]), it["ratio"],
                                   (" (and %d more on this page)" % more) if more > 0 else ""), prefix + page_path, element_line(site / page_path, it["el"]))
                            found.setdefault(key, ["idea", "", []])[2].append(prefix + page_path)
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
