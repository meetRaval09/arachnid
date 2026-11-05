#!/usr/bin/env python3
"""
ARACHNID - Step 12: BUNDLE SIZE  (weigh what a visitor really downloads - measured, not guessed)
==============================================================================================
    python bundlesize.py [folder]               weigh every page of the project as it is
    python bundlesize.py [folder] --build       build the project first (in a temporary copy) and weigh the real output
    python bundlesize.py [folder] --online      also ask outside servers how big the files they serve are (read-only)
    options: --budget-js-kb 170  --budget-total-kb 1000   (compressed sizes; the page-weight limits you want to hold)

"Bundle size" = the total weight of the files a browser must fetch to show a page: the HTML, the JavaScript, the CSS,
the images, the fonts. Heavy pages load slowly on phones and weak networks and use more memory.

For every page it follows what the page really loads (scripts, styles, images, fonts, CSS imports, script imports),
weighs each file raw and gzip-compressed (what travels over the network), and then MEASURES what could be saved:
  * images:   re-encodes big ones as WebP in memory (needs Pillow) and reports the real saving
  * scripts/styles that were never minified: minifies them in memory (esbuild) and reports the real saving
  * fonts:    .ttf/.otf/.eot -> .woff2 is typically about half the size
  * libraries: names the big libraries it recognises, and with source maps shows which package takes how much
It never changes a file.  Images/minifying are suggestions for a human; weights are facts.
"""
import argparse
import gzip
import hashlib
import io
import json
import re
import shutil
import sys
import tempfile
import zlib
from collections import defaultdict
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

SKIP = {"node_modules", ".git", "backups", "reports", "arachnid", "__pycache__", ".cache", "coverage"}
TEXTLIKE = {"html", "htm", "js", "mjs", "cjs", "css", "json", "svg", "xml", "txt", "map", "ttf", "otf", "eot", "bmp", "wasm", "glb", "gltf", "obj"}
CAT = {"js": "JavaScript", "mjs": "JavaScript", "cjs": "JavaScript", "css": "CSS", "html": "HTML", "htm": "HTML",
       "png": "images", "jpg": "images", "jpeg": "images", "gif": "images", "webp": "images", "avif": "images", "svg": "images", "ico": "images", "bmp": "images",
       "woff": "fonts", "woff2": "fonts", "ttf": "fonts", "otf": "fonts", "eot": "fonts",
       "mp4": "media", "webm": "media", "mov": "media", "mp3": "media", "wav": "media", "ogg": "media", "glb": "3D models", "gltf": "3D models", "obj": "3D models"}
LIBS = [("jQuery", r"jquery", r"jQuery v?[\d.]+"), ("React", r"react(?:\.|-)", r"react\.(?:production|development)|__SECRET_INTERNALS"), ("React DOM", r"react-dom", r"react-dom"),
        ("Vue", r"vue(?:\.|-)", r"Vue\.js v?[\d.]+"), ("Angular", r"angular", r"@angular"), ("lodash", r"lodash", r"lodash.{0,20}[\d.]+|Lodash"), ("moment", r"moment", r"moment\.js|//! moment"),
        ("three.js", r"three", r"THREE\.REVISION|three\.js"), ("D3", r"d3(?:\.|-)", r"d3js\.org|// https://d3js.org"), ("GSAP", r"gsap|tweenmax", r"GSAP|gsap\.com"),
        ("Bootstrap", r"bootstrap", r"Bootstrap v?[\d.]+"), ("Chart.js", r"chart(?:\.|-)", r"Chart\.js"), ("Axios", r"axios", r"axios"), ("Font Awesome", r"fontawesome|font-awesome", r"Font Awesome"),
        ("Swiper", r"swiper", r"Swiper"), ("Lottie", r"lottie", r"lottie"), ("Plotly", r"plotly", r"plotly")]
LOCAL_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0")


def kb(n):
    return "%.1f KB" % (n / 1024.0) if n < 1024 * 1024 else "%.2f MB" % (n / 1024.0 / 1024.0)


def ext_of(name):
    return Path(name.split("?")[0].split("#")[0]).suffix.lower().lstrip(".")


def gz_size(data, ext):
    if ext in TEXTLIKE:
        return len(gzip.compress(data, 9, mtime=0))
    return len(data)


class Refs(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.refs = []          # (tag, url)
        self.inline = 0

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag == "script" and a.get("src"):
            self.refs.append(("script", a["src"]))
        elif tag == "link" and a.get("href") and re.search(r"stylesheet|icon|preload|modulepreload|manifest", a.get("rel", ""), re.I):
            if not (a.get("rel", "").lower() == "preload" and a.get("as", "") not in ("script", "style", "font", "image", "")):
                self.refs.append(("link", a["href"]))
        elif tag in ("img", "source", "video", "audio", "embed", "iframe", "input"):
            if a.get("src") and (tag != "input" or a.get("type") == "image"):
                self.refs.append((tag, a["src"]))
            for key in ("srcset",):
                if a.get(key):
                    first = a[key].split(",")[0].strip().split(" ")[0]
                    if first:
                        self.refs.append((tag, first))
            if a.get("poster"):
                self.refs.append(("poster", a["poster"]))
        elif tag == "object" and a.get("data"):
            self.refs.append(("object", a["data"]))

    def handle_data(self, data):
        pass


def resolve(site, base_file, url):
    """Turn a reference found inside base_file into a local path, or None (external / data: / not local)."""
    u = url.strip()
    if not u or u.startswith(("data:", "blob:", "javascript:", "mailto:", "#", "about:")):
        return None
    if u.startswith("//"):
        u = "https:" + u
    p = urlparse(u)
    if p.scheme in ("http", "https", "ws", "wss"):
        return ("external", u) if (p.hostname or "") not in LOCAL_HOSTS else None
    path = unquote(p.path)
    if not path:
        return None
    target = (site / path.lstrip("/")) if path.startswith("/") else (base_file.parent / path)
    try:
        target = target.resolve()
        target.relative_to(site.resolve())
    except (ValueError, OSError):
        return None
    return ("local", target) if target.is_file() else None


CSS_URL = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)|@import\s+(?:url\()?['\"]([^'\"]+)['\"]", re.I)
JS_IMPORT = re.compile(r"""(?:\bimport\s*(?:[\w*{}\s,]+\s*from\s*)?|\bexport\s+[\w*{}\s,]+\s+from\s*|\bimport\s*\()\s*['"](\.{0,2}/[^'"]+)['"]""")


def collect_page(site, page_file):
    """Everything this page loads.  Returns (local {path: tag}, external {url: tag})."""
    local, external = {}, {}
    try:
        html = page_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return local, external
    p = Refs()
    try:
        p.feed(html)
    except Exception:
        pass
    queue = [(page_file, tag, u) for tag, u in p.refs]
    seen = set()
    while queue:
        base, tag, url = queue.pop()
        r = resolve(site, base, url)
        if r is None:
            continue
        kind, target = r
        if kind == "external":
            external.setdefault(target, tag)
            continue
        if target in seen:
            continue
        seen.add(target)
        local[target] = tag
        x = target.suffix.lower().lstrip(".")
        if x == "css" and len(seen) < 400:
            try:
                css = target.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for m in CSS_URL.finditer(css):
                queue.append((target, "css", m.group(2) or m.group(3) or ""))
        elif x in ("js", "mjs") and len(seen) < 400:
            try:
                js = target.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if len(js) < 3_000_000:
                for m in JS_IMPORT.finditer(js):
                    queue.append((target, "script", m.group(1)))
    return local, external


def detect_lib(rel, head):
    name = rel.split("/")[-1].lower()
    for lib, fname, sig in LIBS:
        if re.search(fname, name) or re.search(sig, head[:2000], re.I):
            return lib
    return None


def attribute_by_map(path, gz):
    """With a source map next to a bundle: which npm packages take how much of it (approximate)."""
    m = Path(str(path) + ".map")
    if not m.is_file():
        return None
    try:
        data = json.loads(m.read_text(encoding="utf-8"))
        srcs, cont = data.get("sources") or [], data.get("sourcesContent") or []
    except Exception:
        return None
    if not srcs or not cont:
        return None
    by = defaultdict(int)
    for s, c in zip(srcs, cont):
        if c is None:
            continue
        mm = re.search(r"node_modules/((?:@[^/]+/)?[^/]+)", s.replace("\\", "/"))
        by[mm.group(1) if mm else "(your own code)"] += len(c.encode("utf-8"))
    total = sum(by.values()) or 1
    return sorted(((k, int(gz * v / total), 100.0 * v / total) for k, v in by.items()), key=lambda t: -t[1])[:6]


def webp_saving(path, raw):
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        im = Image.open(path)
        w, h = im.size
        if im.format not in ("PNG", "JPEG"):
            return None
        buf = io.BytesIO()
        im.convert("RGBA" if im.mode in ("RGBA", "LA", "P") else "RGB").save(buf, "WEBP", quality=80, method=4)
        return len(buf.getvalue()), w, h
    except Exception:
        return None


def minify_saving(site, items):
    """items: [(rel, code, 'js'|'css', gzip_size)] -> {rel: minified_gz_size} using esbuild when it is installed."""
    try:
        from syntaxcheck import Esbuild
        es = Esbuild(site)
        if not es.ok:
            return {}
        out = es.minified([(rel, code, ld) for rel, code, ld, _ in items])
    except Exception:
        return {}
    res = {}
    for (rel, code, ld, _), mn in zip(items, out):
        if mn is not None:
            res[rel] = len(gzip.compress(mn.encode("utf-8"), 9, mtime=0))
    return res


def external_sizes(urls):
    import urllib.request
    from concurrent.futures import ThreadPoolExecutor

    def one(u):
        try:
            req = urllib.request.Request(u, method="HEAD", headers={"User-Agent": "Arachnid-bundlesize/1.0", "Accept-Encoding": "gzip"})
            with urllib.request.urlopen(req, timeout=8) as r:
                n = r.headers.get("Content-Length")
                return u, int(n) if n and n.isdigit() else None
        except Exception:
            return u, None
    with ThreadPoolExecutor(8) as ex:
        return dict(ex.map(one, sorted(urls)[:40]))


def analyse(site, online=False, budget_js=170, budget_total=1000):
    site = Path(site).resolve()
    pages = []
    for dp, dn, fn in __import__("os").walk(site):
        dn[:] = [d for d in dn if d not in SKIP]
        for f in sorted(fn):
            if f.lower().endswith((".html", ".htm")):
                pages.append(Path(dp) / f)
    pages = sorted(pages)[:60]
    file_cache = {}

    def weigh(path):
        if path not in file_cache:
            data = path.read_bytes()
            x = path.suffix.lower().lstrip(".")
            file_cache[path] = {"raw": len(data), "gz": gz_size(data, x), "ext": x, "sha": hashlib.sha1(data).hexdigest()}
        return file_cache[path]
    ext_all = {}
    results, findings = [], []
    for pf in pages:
        rel_page = pf.relative_to(site).as_posix()
        local, external = collect_page(site, pf)
        html = weigh(pf)
        by_cat = defaultdict(lambda: [0, 0])
        by_cat["HTML"][0] += html["raw"]
        by_cat["HTML"][1] += html["gz"]
        files = []
        for path, tag in local.items():
            w = weigh(path)
            c = CAT.get(w["ext"], "other")
            by_cat[c][0] += w["raw"]
            by_cat[c][1] += w["gz"]
            files.append((path.relative_to(site).as_posix(), c, w["raw"], w["gz"]))
        ext_bytes = 0
        ext_list = sorted(external)
        results.append({"page": rel_page, "files": files, "external": ext_list, "by_cat": {k: v for k, v in by_cat.items()}, "ext_bytes": 0})
        ext_all.update({u: None for u in ext_list})
    if online and ext_all:
        sizes = external_sizes(ext_all)
        for r in results:
            r["ext_bytes"] = sum(sizes.get(u) or 0 for u in r["external"])
            r["ext_unknown"] = sum(1 for u in r["external"] if not sizes.get(u))
    for r in results:
        total_gz = sum(v[1] for v in r["by_cat"].values()) + r["ext_bytes"]
        total_raw = sum(v[0] for v in r["by_cat"].values()) + r["ext_bytes"]
        r["total_gz"], r["total_raw"] = total_gz, total_raw
        js_gz = r["by_cat"].get("JavaScript", [0, 0])[1]
        top_js = sorted([f for f in r["files"] if f[1] == "JavaScript"], key=lambda f: -f[3])[:3]
        if js_gz > budget_js * 1024:
            findings.append(("warning", "js-budget", r["page"], "JavaScript is %s compressed (limit %d KB)%s" % (kb(js_gz), budget_js, "; biggest: " + ", ".join("%s %s" % (f[0], kb(f[3])) for f in top_js) if top_js else ""),
                             "on a mid-range phone, every 100 KB of JavaScript costs roughly 0.1-0.3 s of parsing on top of the download",
                             "remove unused libraries, load the heavy parts only when needed (lazy loading), or switch to a lighter library (python arachnid.py advise)"))
        if total_gz > budget_total * 1024:
            findings.append(("warning", "page-budget", r["page"], "the page weighs %s compressed (limit %d KB)" % (kb(total_gz), budget_total),
                             "heavy pages load slowly on weak networks and cost visitors data", "see the biggest files below"))
    # --- per-file measurements, once per file
    imgs, texts = [], []
    for path, w in file_cache.items():
        rel = path.relative_to(site).as_posix()
        x = w["ext"]
        if x in ("png", "jpg", "jpeg") and w["raw"] > 100 * 1024:
            imgs.append((path, rel, w))
        if x in ("js", "css", "mjs") and ".min." not in path.name and w["raw"] > 20 * 1024:
            texts.append((path, rel, w))
        if x in ("ttf", "otf", "eot") and w["raw"] > 60 * 1024:
            findings.append(("info", "font-format", rel, "font file %s is in an old format" % kb(w["raw"]), "woff2 is typically about half the size and every modern browser reads it", "convert to .woff2 and list it first in @font-face"))
    for path, rel, w in sorted(imgs, key=lambda t: -t[2]["raw"])[:25]:
        r = webp_saving(path, w["raw"])
        if r and r[0] < w["raw"] * 0.85:
            saved = w["raw"] - r[0]
            sev = "warning" if w["raw"] > 300 * 1024 else "info"
            findings.append((sev, "image-weight", rel, "image is %s (%dx%d pixels); as WebP it measures %s - %d%% smaller (%s saved)" % (kb(w["raw"]), r[1], r[2], kb(r[0]), round(100 * saved / w["raw"]), kb(saved)),
                             "images are usually the heaviest part of a page", "convert to WebP/AVIF (keep the original as a fallback if you must support very old browsers)"))
        elif not r and w["raw"] > 300 * 1024:
            findings.append(("warning", "image-weight", rel, "image is %s" % kb(w["raw"]), "images are usually the heaviest part of a page", "compress it, or convert to WebP/AVIF (install Pillow for a measured estimate: pip install pillow)"))
        if r and (r[1] > 2600 or r[2] > 2600):
            findings.append(("info", "image-dimensions", rel, "image is %dx%d pixels - larger than almost any screen" % (r[1], r[2]), "the extra pixels are downloaded but never seen", "resize to the largest size it is shown at (x2 for sharp phones)"))
    if texts:
        items = []
        for path, rel, w in texts[:30]:
            try:
                items.append((rel, path.read_text(encoding="utf-8"), "css" if w["ext"] == "css" else "js", w["gz"]))
            except (UnicodeDecodeError, OSError):
                pass
        mins = minify_saving(site, items)
        exact = bool(mins)
        if not mins:                                           # no esbuild here: a rough whitespace/comment strip, labelled as an estimate
            for rel, code, ld, gz in items:
                t = re.sub(r"/\*[\s\S]*?\*/", "", code)
                t = re.sub(r"(?m)^\s*//.*$", "", t)
                t = re.sub(r"(?m)^[ \t]+|[ \t]+$", "", t)
                t = re.sub(r"\n{2,}", "\n", t)
                mins[rel] = len(gzip.compress(t.encode("utf-8"), 9, mtime=0))
        for rel, code, ld, gz in items:
            if rel in mins and mins[rel] < gz * 0.85:
                findings.append(("info", "not-minified", rel, "not minified: %s compressed; minified it %s %s (%d%% smaller)" % (kb(gz), "measures" if exact else "would be about", kb(mins[rel]), round(100 * (gz - mins[rel]) / gz)),
                                 "comments and spacing are downloaded but never needed by the browser", "run the file through a minifier (esbuild/Vite do this in the build step)"))
    # libraries + source maps
    libs = []
    for path, w in file_cache.items():
        if w["ext"] in ("js", "mjs"):
            try:
                head = path.read_text(encoding="utf-8", errors="replace")[:2500]
            except OSError:
                continue
            rel = path.relative_to(site).as_posix()
            lib = detect_lib(rel, head)
            attr = attribute_by_map(path, w["gz"])
            if lib or attr:
                libs.append((rel, lib, w["gz"], attr))
    dup = defaultdict(list)
    for path, w in file_cache.items():
        if w["raw"] > 10 * 1024:
            dup[w["sha"]].append(path.relative_to(site).as_posix())
    for sha, names in dup.items():
        if len(names) > 1:
            findings.append(("info", "duplicate-file", names[0], "the same file exists %d times: %s" % (len(names), ", ".join(names[:3])), "if both are loaded by pages, visitors download it twice (different addresses are cached separately)", "keep one copy (python arachnid.py fix merges identical files)"))
    order = {"critical": 0, "warning": 1, "info": 2}
    findings.sort(key=lambda f: (order[f[0]], f[2]))
    return site, results, findings, libs, file_cache


def main():
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Arachnid bundle size - weigh what a visitor really downloads")
    ap.add_argument("root", nargs="?", default=str(HERE.parent))
    ap.add_argument("--build", action="store_true", help="build the project in a temporary copy and weigh the real output")
    ap.add_argument("--online", action="store_true", help="also ask outside servers how big their files are")
    ap.add_argument("--budget-js-kb", type=int, default=170)
    ap.add_argument("--budget-total-kb", type=int, default=1000)
    ap.add_argument("--out", default=str(HERE / "reports" / "bundle-report.json"))
    a = ap.parse_args()
    root = Path(a.root).resolve()
    tmp = None
    site = root
    note = "as the files are on disk"
    if a.build:
        import pixelcheck
        cmd = pixelcheck.detect_build(root)
        if not cmd:
            print("No build command found in package.json - weighing the files as they are.")
        else:
            tmp = Path(tempfile.mkdtemp(prefix="arachnid-bundle-"))
            try:
                work = tmp / "w"
                pixelcheck.make_copy(root, work)
                site = pixelcheck.build(work, cmd)
                note = "of the BUILT output (%s)" % cmd
            except Exception as e:
                print("The build did not finish (%s) - weighing the files as they are." % str(e)[:160])
                site = root
    try:
        site, results, findings, libs, cache = analyse(site, a.online, a.budget_js_kb, a.budget_total_kb)
        print("Arachnid bundle size - %s\n(weights %s; 'compressed' = gzip, what travels over the network)" % (root, note))
        if not results:
            print("\nNo HTML pages found - nothing to weigh. (This tool weighs web pages.)")
        for r in results:
            print("\n%s   total %s  (compressed %s)%s" % (r["page"], kb(r["total_raw"]), kb(r["total_gz"]), "" if not r["external"] else "   + %d outside file%s %s" % (
                len(r["external"]), "" if len(r["external"]) == 1 else "s", "(%s counted)" % kb(r["ext_bytes"]) if r["ext_bytes"] else "(size unknown - add --online)")))
            for c, (raw, gz) in sorted(r["by_cat"].items(), key=lambda kv: -kv[1][1]):
                bar = "#" * max(1, int(30 * gz / max(1, r["total_gz"]))) if gz else ""
                print("   %-11s %10s  compressed %10s  %s" % (c, kb(raw), kb(gz), bar))
            for f in sorted(r["files"], key=lambda f: -f[3])[:3]:
                print("      heavy: %-42s %s (compressed %s)" % (f[0][:42], kb(f[2]), kb(f[3])))
        if libs:
            print("\nLIBRARIES INSIDE YOUR SCRIPTS")
            for rel, lib, gz, attr in sorted(libs, key=lambda t: -t[2])[:8]:
                print("   %-34s %-12s %s compressed" % (rel[:34], lib or "", kb(gz)))
                for pkg, g, pct in (attr or [])[:5]:
                    print("        %-30s ~%s  (%d%%)   [from the source map; approximate]" % (pkg[:30], kb(g), round(pct)))
        for sev, title in (("warning", "NEEDS ATTENTION"), ("info", "SAVINGS YOU COULD TAKE")):
            items = [f for f in findings if f[0] == sev]
            if items:
                print("\n%s (%d)" % (title, len(items)))
                for f in items[:12]:
                    print("  %s\n      %s\n      why: %s\n      do:  %s" % (f[2], f[3], f[4], f[5]))
                if len(items) > 12:
                    print("  ... and %d more (see the report file)" % (len(items) - 12))
        if results and not findings:
            print("\nNo weight problems found within your limits (JS %d KB, page %d KB, compressed)." % (a.budget_js_kb, a.budget_total_kb))
        try:
            import ledger
            ledger.reset("bundle")
            for sev, kind, where, what, why, todo in findings:
                ledger.record("bundle", "found", sev if sev != "info" else "info", where, 0, what, why, todo, kind)
        except Exception:
            pass
        out = Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"root": str(root), "pages": [{k: (v if k != "files" else v) for k, v in r.items()} for r in results],
                                   "findings": [dict(zip(("severity", "kind", "where", "what", "why", "todo"), f)) for f in findings],
                                   "libraries": [{"file": l[0], "library": l[1], "gzip": l[2], "packages": l[3]} for l in libs]}, indent=1, default=str), encoding="utf-8")
        print("\nFull report saved to: %s" % out)
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
