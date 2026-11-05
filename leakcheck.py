#!/usr/bin/env python3
"""
ARACHNID - Step 13: LEAK CHECK  (open each page in a real hidden browser, use it again and again, watch the memory)
==================================================================================================================
    python leakcheck.py [folder]                      test every page (up to 8) with 15 rounds of safe use
    python leakcheck.py [folder] --build              build first (in a temporary copy) and test the real output
    python leakcheck.py [folder] --rounds 25 --max-pages 12
    python leakcheck.py [folder] --steps steps.json   use YOUR clicks instead of the automatic ones

A "memory leak" = the page keeps things in memory that it no longer needs, so memory grows the longer someone uses it,
until the tab gets slow or crashes.  Reading code can only SUSPECT this.  This tool PROVES it:
  1  open the page, let it settle, force the browser to clean up (garbage collection), write down the memory
  2  do one ROUND of normal use: scroll down and up, switch between phone and desktop width, hover and click the
     safe controls (buttons, menu toggles, accordions, tabs, checkboxes) - never links, never form submits
  3  clean up again, write down the memory.  Repeat for every round.
  4  If, after the clean-up, memory / page elements / event listeners keep rising round after round, that is growth
     that normal use caused and the browser could not free: a leak.

Safety: the page is served from your own computer; every request to the outside internet is blocked, dialogs are
dismissed, forms are never submitted.  Nothing in your project is changed.
Honest limits: it only finds leaks that the exercised actions trigger (give it your own clicks with --steps for more);
pages that legitimately grow (a live feed, a chart that keeps adding points) will look like leaks - read the
"grows even when idle" line.  Memory numbers are the browser's own counters.

steps.json example:   [{"click": "#menu-button"}, {"wait": 200}, {"press": "Escape"}, {"hover": ".card"}, {"scroll": 800}]
"""
import argparse
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

SAFE_CLICK_JS = """() => {
  const sel = 'button:not([type=submit]):not(form button), [role=button], [aria-expanded], summary, [data-toggle], [data-bs-toggle], input[type=checkbox], input[type=radio], [role=tab], .accordion, .tab, .menu-toggle, .hamburger, .burger';
  const out = [];
  document.querySelectorAll(sel).forEach((el, i) => {
    if (el.closest('a[href]') || el.disabled) return;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return;
    el.setAttribute('data-arachnid-i', String(i));
    out.push(i);
  });
  return out.slice(0, 14);
}"""
METRICS = ("JSHeapUsedSize", "Nodes", "JSEventListeners", "Documents")


def slope(ys):
    n = len(ys)
    if n < 3:
        return 0.0
    xm, ym = (n - 1) / 2.0, sum(ys) / float(n)
    den = sum((i - xm) ** 2 for i in range(n)) or 1.0
    return sum((i - xm) * (y - ym) for i, y in enumerate(ys)) / den


def rising(ys):
    d = [b - a for a, b in zip(ys, ys[1:])]
    return (sum(1 for x in d if x > 0) / float(len(d))) if d else 0.0


def verdict(heap, nodes, lst, warm):
    h, n, l = heap[warm:], nodes[warm:], lst[warm:]
    sh, sn, sl = slope(h), slope(n), slope(l)
    rh, rn, rl = rising(h), rising(n), rising(l)
    reasons = []
    if sh >= 20 * 1024 and rh >= 0.7 and (h[-1] - h[0]) >= 200 * 1024:
        reasons.append("memory rises about %.0f KB every round (up in %d%% of rounds)" % (sh / 1024.0, round(100 * rh)))
    if sn >= 5 and rn >= 0.7:
        reasons.append("page elements pile up: about +%.0f every round (they are removed from the page but still held in memory)" % sn)
    if sl >= 2 and rl >= 0.7:
        reasons.append("event listeners pile up: about +%.0f every round (something keeps adding listeners and never removing them)" % sl)
    if reasons:
        return "LEAK", reasons, (sh, sn, sl)
    soft = []
    if sh >= 5 * 1024 and rh >= 0.6:
        soft.append("memory creeps up about %.0f KB per round" % (sh / 1024.0))
    if sn >= 1.5 and rn >= 0.6:
        soft.append("elements creep up about +%.1f per round" % sn)
    if soft:
        return "SUSPICIOUS", soft, (sh, sn, sl)
    return "STABLE", [], (sh, sn, sl)


def page_scripts(site, page_path):
    """Local script files a page loads (to point at suspicious code afterwards)."""
    try:
        from bundlesize import collect_page
        local, _ = collect_page(site, site / page_path)
        return {p.relative_to(site).as_posix() for p, tag in local.items() if tag == "script"} | {page_path}
    except Exception:
        return {page_path}


def run_steps(page, steps):
    for st in steps:
        try:
            if "click" in st:
                page.click(st["click"], timeout=1500)
            elif "hover" in st:
                page.hover(st["hover"], timeout=1500)
            elif "press" in st:
                page.keyboard.press(st["press"])
            elif "wait" in st:
                page.wait_for_timeout(int(st["wait"]))
            elif "scroll" in st:
                page.mouse.wheel(0, int(st["scroll"]))
        except Exception:
            pass


ROUND_JS = """async () => {
  const tick = () => new Promise(r => setTimeout(r, 0));
  const raf = () => new Promise(r => requestAnimationFrame(() => r()));
  window.scrollTo(0, document.documentElement.scrollHeight); await tick();
  window.scrollTo(0, 0); await tick();
  const sel = 'button:not([type=submit]), [role=button], [aria-expanded], summary, [data-toggle], [data-bs-toggle], input[type=checkbox], input[type=radio], [role=tab], .accordion, .tab, .menu-toggle, .hamburger, .burger';
  const els = Array.from(document.querySelectorAll(sel)).filter(el => {
    if (el.closest('a[href]') || el.disabled || (el.form && el.type === 'submit')) return false;
    const r = el.getBoundingClientRect();
    return r.width >= 2 && r.height >= 2;
  }).slice(0, 14);
  let n = 0;
  for (const el of els) {
    try {
      el.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
      el.dispatchEvent(new MouseEvent('mouseenter'));
      el.click(); n++;
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    } catch (e) {}
    await tick();
  }
  await raf();
  return n;
}"""


def auto_round(page, cdp, start_url, viewport):
    cdp.send("Emulation.setDeviceMetricsOverride", {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": False})
    page.evaluate("() => new Promise(r => requestAnimationFrame(() => r()))")
    cdp.send("Emulation.setDeviceMetricsOverride", {"width": viewport["width"], "height": viewport["height"], "deviceScaleFactor": 1, "mobile": False})
    n = page.evaluate(ROUND_JS)
    if page.url.split("#")[0] != start_url.split("#")[0]:
        page.goto(start_url, wait_until="load")
    return n


def test_page(browser, port, page_path, rounds, steps):
    import time
    t0 = time.time()
    viewport = {"width": 1280, "height": 800}
    ctx = browser.new_context(viewport=viewport)
    base = "http://127.0.0.1:%d" % port

    def gate(route):
        u = route.request.url
        if u.startswith(base) or u.startswith(("data:", "blob:", "about:")):
            route.continue_()
        else:
            route.abort()
    ctx.route("**/*", gate)
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)[:120]))
    page.on("dialog", lambda d: d.dismiss())
    url = "%s/%s" % (base, page_path)
    page.goto(url, wait_until="load", timeout=20000)
    page.wait_for_timeout(150)
    cdp = ctx.new_cdp_session(page)
    cdp.send("Performance.enable")
    cdp.send("HeapProfiler.enable")

    def sample():
        cdp.send("HeapProfiler.collectGarbage")
        cdp.send("HeapProfiler.collectGarbage")
        m = {x["name"]: x["value"] for x in cdp.send("Performance.getMetrics")["metrics"]}
        return [m.get(k, 0) for k in METRICS]
    first = sample()                                  # idle drift: does it grow with NO interaction at all?
    page.wait_for_timeout(250)
    second = sample()
    idle_growth = second[0] - first[0]
    heap, nodes, lst, docs = [], [], [], []
    acted, warm, done = 0, 3, 0
    for r in range(warm + rounds):
        if steps:
            run_steps(page, steps)
            acted = len(steps)
            if page.url.split("#")[0] != url.split("#")[0]:
                page.goto(url, wait_until="load")
        else:
            acted = auto_round(page, cdp, url, viewport)
        s = sample()
        heap.append(s[0])
        nodes.append(s[1])
        lst.append(s[2])
        docs.append(s[3])
        done = r + 1 - warm
        if done >= 5:                                  # enough evidence early: stop as soon as the answer is clear
            v = verdict(heap, nodes, lst, warm)
            if v[0] == "LEAK":
                break
            if done >= 6 and v[0] == "STABLE" and abs(v[2][0]) < 2048 and abs(v[2][1]) < 0.5 and abs(v[2][2]) < 0.3:
                break
    ctx.close()
    return {"heap": heap, "nodes": nodes, "listeners": lst, "documents": docs, "idle_growth": idle_growth, "controls": acted,
            "errors": errors[:3], "rounds_used": len(heap) - warm, "seconds": round(time.time() - t0, 1)}


def worker(args):
    """One browser handles a list of pages (runs in its own process so pages are tested in parallel)."""
    from playwright.sync_api import sync_playwright
    port, pages, rounds, steps = args
    out = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for pg in pages:
            try:
                res = test_page(browser, port, pg, rounds, steps)
                v, reasons, sl = verdict(res["heap"], res["nodes"], res["listeners"], 3)
                res.update(page=pg, verdict=v, reasons=reasons)
            except Exception as e:
                res = {"page": pg, "error": str(e)[:120]}
            out.append(res)
        browser.close()
    return out


def main():
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Arachnid leak check - prove memory leaks by using each page in a real browser")
    ap.add_argument("root", nargs="?", default=str(HERE.parent))
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--rounds", type=int, default=10, help="most rounds of use per page (it stops earlier when the answer is clear)")
    ap.add_argument("--max-pages", type=int, default=8)
    ap.add_argument("--steps", default=None, help="a JSON file with your own clicks to repeat")
    ap.add_argument("--out", default=str(HERE / "reports" / "leak-report.json"))
    a = ap.parse_args()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("The leak check needs a browser:  pip install playwright   then   playwright install chromium")
        return 1
    import pixelcheck
    root = Path(a.root).resolve()
    steps = json.loads(Path(a.steps).read_text(encoding="utf-8")) if a.steps else None
    tmp, site = None, root
    if a.build:
        cmd = pixelcheck.detect_build(root)
        if cmd:
            tmp = Path(tempfile.mkdtemp(prefix="arachnid-leak-"))
            try:
                work = tmp / "w"
                pixelcheck.make_copy(root, work)
                site = pixelcheck.build(work, cmd)
            except Exception as e:
                print("The build did not finish (%s) - testing the files as they are." % str(e)[:160])
                site = root
    try:
        pages = [p for p in pixelcheck.pages_in(site) if not p.startswith(("arachnid/", "backups/", "reports/"))][:a.max_pages]
        if not pages:
            print("No HTML pages found - nothing to test.")
            return 0
        srv, port = pixelcheck.serve(site)
        import os
        import time
        from concurrent.futures import ProcessPoolExecutor
        n_workers = max(1, min(len(pages), (os.cpu_count() or 2), 4))
        print("Arachnid leak check - %s\nTesting %d page%s with %d browser%s in parallel (about 2-4 s per page) ..." % (root, len(pages), "" if len(pages) == 1 else "s", n_workers, "" if n_workers == 1 else "s"))
        results = []
        t_start = time.time()
        try:
            groups = [pages[i::n_workers] for i in range(n_workers)]
            if n_workers == 1:
                batches = [worker((port, groups[0], a.rounds, steps))]
            else:
                with ProcessPoolExecutor(n_workers) as ex:
                    batches = list(ex.map(worker, [(port, g, a.rounds, steps) for g in groups]))
        finally:
            srv.shutdown()
        by_name = {r["page"]: r for b in batches for r in b}
        for pg in pages:
            r = by_name.get(pg, {"page": pg, "error": "no result"})
            if "error" in r:
                print("\n%s\n   could not be tested: %s" % (pg, r["error"]))
            else:
                results.append(r)
        print("(all pages done in %.1f s)" % (time.time() - t_start))
        # suspicious code, from the code-reading tool
        sus = {}
        try:
            import insights
            proj = insights.Project(site if tmp is None else root)
            allf = insights.leaks_and_memory(proj)
            for res in results:
                if res["verdict"] != "STABLE":
                    scripts = page_scripts(site, res["page"])
                    sus[res["page"]] = [f for f in allf if f["file"] in scripts][:4]
        except Exception:
            pass
        icon = {"LEAK": "LEAK (proven growth)", "SUSPICIOUS": "suspicious", "STABLE": "stable"}
        for res in results:
            h = res["heap"]
            print("\n%s   ->  %s" % (res["page"], icon[res["verdict"]]))
            print("   memory (JS heap) after clean-up:  start %.0f KB  ->  end %.0f KB   |  page elements %d -> %d   |  listeners %d -> %d   (%d rounds in %.1f s, %d controls used per round)" % (
                h[3] / 1024.0, h[-1] / 1024.0, res["nodes"][3], res["nodes"][-1], res["listeners"][3], res["listeners"][-1], res["rounds_used"], res["seconds"], res["controls"]))
            for r in res["reasons"]:
                print("   - " + r)
            if res["idle_growth"] > 100 * 1024:
                print("   - note: it also grows by %.0f KB with NO interaction at all (a timer, animation or live feed is adding things)" % (res["idle_growth"] / 1024.0))
            if res["errors"]:
                print("   - page errors while testing: " + "; ".join(res["errors"]))
            for f in sus.get(res["page"], []):
                print("   code to look at: %s:%s  %s  (%s)" % (f["file"], f["line"], f["what"], f["confidence"]))
        if not results:
            print("\nNothing could be tested.")
        leaks = [r for r in results if r["verdict"] == "LEAK"]
        print("\n%s" % ("%d page%s leak%s memory when used. Look at the listed code first (or at what runs when you click); run this again to prove the fix." % (len(leaks), "" if len(leaks) == 1 else "s", "s" if len(leaks) == 1 else "") if leaks else
                        "No proven leaks in the actions tested. (That does not prove there are none: give it your own clicks with --steps for more coverage.)"))
        try:
            import ledger
            ledger.reset("leak")
            for r in results:
                if r["verdict"] == "STABLE":
                    continue
                top = sus.get(r["page"], [])
                ledger.record("leak", "found", "warning" if r["verdict"] == "LEAK" else "info", r["page"], 0,
                              "%s: %s" % ("memory leak (measured)" if r["verdict"] == "LEAK" else "memory creeping up", "; ".join(r["reasons"])[:200]),
                              "memory that grows with use makes the page slow and eventually crashes the tab",
                              "look at: " + ", ".join("%s:%s" % (f["file"], f["line"]) for f in top) if top else "remove listeners / timers / references when elements are removed", "leak-measured")
        except Exception:
            pass
        out = Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"root": str(root), "rounds": a.rounds, "pages": results}, indent=1), encoding="utf-8")
        print("Full report saved to: %s" % out)
        return 0
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
