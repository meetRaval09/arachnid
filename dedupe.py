#!/usr/bin/env python3
"""
Arachnid - Step 4: DUPLICATE FILES - find them, merge them, place the survivor where every user is close
==========================================================================================================
Two files with exactly the same content are "duplicates". If two pages each use their own copy of the same
logo, the project carries the logo twice. This tool keeps ONE copy and makes everything point to it.

How it decides (your rule):
  1. For every page / stylesheet / script that USES the file, count the STEPS (folder hops) it takes to
     reach each copy.
  2. Pick the folder where the surviving copy should live: the existing folder with the smallest worst-case
     distance for everybody (ties: smallest total). It is only moved if that is strictly better than today.
  3. Rewrite every reference, delete the extra copies.

How it stays safe (a bug-free move matters more than a clever one):
  * Nothing is touched unless you pass --apply. The default is a preview.
  * Only files that are plain assets (images, video, audio, fonts, 3D models, PDFs) are merged. Duplicate
    CODE files are only reported: two copies of a script are two separate programs at run time.
  * A group is SKIPPED (and the reason is printed) if the file's name shows up anywhere in an unrecognised
    way, if a path is built piece by piece (`"img/" + name`), if a glob / directory listing might pick the
    file up, if the file lives in public/ (its web address would change), or if the new place would clash
    with another file. When in doubt, it does nothing.
  * The plan is simulated first: every reference that worked before must still point to a file with the
    same content afterwards, and no new broken reference may appear. Optional --verify-cmd runs your
    real build on a temporary copy as well.
  * Backups go to arachnid/backups/<time>-dedupe/ and  python dedupe.py --undo  restores everything.

Run:
    python dedupe.py                       preview for the project above arachnid/
    python dedupe.py "C:\\some\\folder"
    python dedupe.py --apply               do it (backup first)
    python dedupe.py --apply --verify-cmd "npm run build"
    python dedupe.py --undo
"""
import argparse
import hashlib
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent

SKIP_DIRS = {"node_modules", ".git", "dist", "build", "__pycache__", ".wrangler", ".vite", ".next", ".cache",
             ".claude", "arachnid", ".venv", "venv", "backups", "reports", ".cms-trash", ".cms-tmp",
             "perf-reports", "coverage"}
ASSET_EXT = {"png", "jpg", "jpeg", "gif", "webp", "avif", "svg", "ico", "bmp", "tif", "tiff", "mp4", "webm", "mov",
             "mp3", "wav", "ogg", "m4a", "woff", "woff2", "ttf", "otf", "eot", "glb", "gltf", "bin", "obj", "hdr",
             "exr", "ktx2", "basis", "pdf", "mkv", "mpeg", "flac", "cur"}
TEXT_EXT = {"html", "htm", "css", "scss", "less", "js", "mjs", "cjs", "jsx", "ts", "tsx", "vue", "svelte", "md",
            "mdx", "json", "gltf", "svg", "xml", "yml", "yaml", "toml", "txt", "frag", "vert", "glsl",
            "webmanifest", "py", "php"}
SKIP_NAMES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", ".gitkeep", ".gitignore", "license", "licence"}
WEB_ROOTS = ("public", "static")           # folders whose files are reached by web address
MAX_TEXT = 3_000_000


# ----------------------------------------------------------------------------
# Indexing
# ----------------------------------------------------------------------------
def walk(root):
    stack = [str(root)]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError:
            continue
        for e in entries:
            try:
                if e.is_dir(follow_symlinks=False):
                    if e.name not in SKIP_DIRS:
                        stack.append(e.path)
                elif e.is_file(follow_symlinks=False):
                    yield e.path, e.stat().st_size
            except OSError:
                continue


def sha1(path):
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Project:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.files = {}          # rel -> size
        self.dirs = set([""])
        for p, size in walk(self.root):
            rel = Path(p).resolve().relative_to(self.root).as_posix()
            self.files[rel] = size
            d = posixpath.dirname(rel)
            while d and d not in self.dirs:
                self.dirs.add(d)
                d = posixpath.dirname(d)
        self.roots = self._find_roots()

    def _find_roots(self):
        roots = {""} | {posixpath.dirname(r) for r in self.files if posixpath.basename(r) == "package.json"}
        return sorted(roots, key=lambda r: -len(r))

    def project_of(self, rel):
        for r in self.roots:
            if r == "" or rel == r or rel.startswith(r + "/"):
                return r
        return ""

    def web_bases(self, proj):
        """Folders a root-relative URL like /img/a.png may point into, most likely first."""
        out = []
        for w in WEB_ROOTS:
            b = posixpath.join(proj, w) if proj else w
            if b in self.dirs:
                out.append(b)
        out.append(proj)
        return out

    def in_web_root(self, rel):
        proj = self.project_of(rel)
        for w in WEB_ROOTS:
            b = posixpath.join(proj, w) if proj else w
            if rel.startswith(b + "/"):
                return True
        return False


# ----------------------------------------------------------------------------
# Finding references
# ----------------------------------------------------------------------------
PATH_CH = r"[^\s'\"`()<>,;|{}]"
PATTERNS = [
    # CSS  url(...)
    ("url", re.compile(r"url\(\s*(['\"]?)(" + PATH_CH + r"+?)\1\s*\)", re.I)),
    # HTML-ish attributes holding exactly one path
    ("attr", re.compile(r"""\b(?:src|href|poster|data-src|data-poster|data-bg|content|xlink:href|data)\s*=\s*(['"])(""" + PATH_CH + r"""+?)\1""", re.I)),
    # markdown link / image
    ("md", re.compile(r"\]\(\s*(<?)(" + PATH_CH + r"+?)>?(?:\s+\"[^\"]*\")?\s*\)")),
    # a string that is nothing but a path: imports, new URL(), fetch(), loader.load()
    ("str", re.compile(r"(['\"`])(" + PATH_CH + r"+?)\1")),
]
SRCSET = re.compile(r"""\bsrcset\s*=\s*(['"])(.*?)\1""", re.I | re.S)
DYNAMIC_PREFIX = re.compile(r"""(['"`])([\w@~./-]*/)\1\s*\+|`([\w@~./-]*/)\$\{""")
GLOB_CALL = re.compile(r"(?:import\.meta\.glob(?:Eager)?|require\.context|\bglob(?:Sync)?|readdir(?:Sync)?|"
                       r"os\.listdir|os\.walk|scandir|listdir|readdirSync)\s*\(([^)]*)\)")
STR_LIT = re.compile(r"""(['"`])((?:\\.|(?!\1)[^\\\n])*)\1""")


def split_suffix(raw):
    m = re.search(r"[?#]", raw)
    return (raw[:m.start()], raw[m.start():]) if m else (raw, "")


class Ref:
    __slots__ = ("file", "start", "end", "raw", "kind", "target", "base")

    def __init__(self, file, start, end, raw, kind):
        self.file, self.start, self.end, self.raw, self.kind = file, start, end, raw, kind
        self.target, self.base = None, None


def find_refs(rel, text):
    """All places in `text` that look like a path to a file. Returns list of Ref (non-overlapping)."""
    found = []
    for kind, rx in PATTERNS:
        for m in rx.finditer(text):
            found.append(Ref(rel, m.start(2), m.end(2), m.group(2), kind))
    for m in SRCSET.finditer(text):
        base = m.start(2)
        for t in re.finditer(r"[^\s,]+(?=(?:\s+[\d.]+[wx])?\s*(?:,|$))", m.group(2)):
            if "/" in t.group(0) or "." in t.group(0):
                found.append(Ref(rel, base + t.start(), base + t.end(), t.group(0), "srcset"))
    found.sort(key=lambda r: (r.start, r.end))
    out, last_end = [], -1
    for r in found:
        if r.start >= last_end:
            out.append(r)
            last_end = r.end
        elif r.start == out[-1].start and r.end < out[-1].end:   # prefer the tighter span
            out[-1] = r
            last_end = r.end
    return out


EXTERNAL = re.compile(r"^(?:[a-z][a-z0-9+.-]*:|//|#|\$|\{|<|%|@[a-z-]+/[^/]+$)", re.I)


def resolve(project, ref, exists):
    """Which real file does this reference point to? Sets ref.target/base. Returns list of candidates."""
    raw, _suf = split_suffix(ref.raw)
    if not raw or EXTERNAL.match(raw):
        return []
    raw = re.sub(r"%20", " ", raw)
    here = posixpath.dirname(ref.file)
    proj = project.project_of(ref.file)
    cands = []
    if raw.startswith("/"):
        for b in project.web_bases(proj):
            c = posixpath.normpath(posixpath.join(b, raw.lstrip("/")))
            if exists(c):
                cands.append((c, b))
    elif raw.startswith("./") or raw.startswith("../"):
        c = posixpath.normpath(posixpath.join(here, raw))
        if exists(c):
            cands.append((c, here))
    else:
        runtime = ref.kind == "str" and ref.file.rsplit(".", 1)[-1].lower() in ("js", "mjs", "cjs", "jsx", "ts", "tsx", "vue", "svelte")
        # HTML / CSS / Markdown read "img/a.png" next to the file itself, nowhere else (browser rule).
        # JavaScript strings are loaded at run time from the web address space (public/, project root).
        bases = project.web_bases(proj) if runtime else [here]
        for b in bases:
            c = posixpath.normpath(posixpath.join(b, raw))
            if exists(c) and (c, b) not in cands:
                cands.append((c, b))
                if not runtime and b == here:      # a browser reads "img/a.png" next to the page itself - that settles it
                    break
    uniq = {}
    for c, b in cands:
        uniq.setdefault(c, b)
    return list(uniq.items())


# ----------------------------------------------------------------------------
# Distance between folders
# ----------------------------------------------------------------------------
def parts(d):
    return tuple(p for p in d.split("/") if p)


def dist(a, b):
    pa, pb = parts(a), parts(b)
    k = 0
    while k < min(len(pa), len(pb)) and pa[k] == pb[k]:
        k += 1
    return (len(pa) - k) + (len(pb) - k)


# ----------------------------------------------------------------------------
# Analysis
# ----------------------------------------------------------------------------
def analyse(project, progress=print):
    root = project.root
    # 1. duplicate groups (by size first, then hash)
    by_size = defaultdict(list)
    for rel, size in project.files.items():
        ext = rel.rsplit(".", 1)[-1].lower() if "." in rel else ""
        name = posixpath.basename(rel).lower()
        if size == 0 or name in SKIP_NAMES or name.startswith("license"):
            continue
        by_size[size].append(rel)
    groups_all = []
    for size, rels in by_size.items():
        if len(rels) < 2:
            continue
        by_hash = defaultdict(list)
        for rel in rels:
            try:
                by_hash[sha1(root / rel)].append(rel)
            except OSError:
                pass
        for h, items in by_hash.items():
            if len(items) > 1:
                groups_all.append({"hash": h, "size": size, "files": sorted(items)})
    # split by project
    groups, cross = [], []
    for g in groups_all:
        per = defaultdict(list)
        for f in g["files"]:
            per[project.project_of(f)].append(f)
        for proj, fs in per.items():
            if len(fs) > 1:
                groups.append({"hash": g["hash"], "size": g["size"], "files": sorted(fs), "project": proj})
        if len(per) > 1:
            cross.append(g)
    # 2. which groups can we merge at all (asset files)?
    for g in groups:
        exts = {f.rsplit(".", 1)[-1].lower() if "." in f else "" for f in g["files"]}
        g["asset"] = exts <= ASSET_EXT
        g["unsafe"] = []
        g["refs"] = []                                  # (Ref, target)
    names = {posixpath.basename(f) for g in groups if g["asset"] for f in g["files"]}
    name_rx = None
    if names:
        name_rx = re.compile(r"(?<![A-Za-z0-9_-])(?:" + "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
                             + r")(?![A-Za-z0-9_-])")
    file_group = {}
    for gi, g in enumerate(groups):
        for f in g["files"]:
            file_group[f] = gi
    exists = lambda p: p in project.files
    texts = {}
    unsafe_zones = []          # (path_prefix, reason)
    dyn_prefixes = []          # (file, prefix)
    mentions = []              # (file, line, name)
    for rel in project.files:
        ext = rel.rsplit(".", 1)[-1].lower() if "." in rel else ""
        if ext not in TEXT_EXT or project.files[rel] > MAX_TEXT:
            continue
        if posixpath.basename(rel).lower() in SKIP_NAMES:
            continue
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        texts[rel] = text
        if ext in ("html", "htm") and re.search(r"<base\s", text, re.I):
            proj_ = project.project_of(rel)
            for g in groups:
                if g["project"] == proj_:
                    g["unsafe"].append("%s uses a <base> tag, which changes how every path in the page is read" % rel)
        refs = find_refs(rel, text)
        covered = []
        for r in refs:
            cands = resolve(project, r, exists)
            if len(cands) == 1 and cands[0][0] in file_group:
                r.target, r.base = cands[0]
                groups[file_group[r.target]]["refs"].append(r)
                covered.append((r.start, r.end))
            elif len(cands) > 1 and any(c in file_group for c, _b in cands):
                for c, _b in cands:
                    if c in file_group:
                        groups[file_group[c]]["unsafe"].append(
                            "%s mentions %r and it could mean %d different files" % (rel, r.raw, len(cands)))
                covered.append((r.start, r.end))
            elif cands:
                covered.append((r.start, r.end))
        # names that appear in some way we do not understand
        if name_rx:
            for m in name_rx.finditer(text):
                if any(a <= m.start() < b for a, b in covered):
                    continue
                mentions.append((rel, text.count("\n", 0, m.start()) + 1, m.group(0), m.start()))
        # paths assembled piece by piece, folder listings and globs
        for m in DYNAMIC_PREFIX.finditer(text):
            p = (m.group(2) or m.group(3) or "").strip("/")
            if p and p not in (".", ".."):
                dyn_prefixes.append((rel, p))
        for m in GLOB_CALL.finditer(text):
            lits = [s.group(2) for s in STR_LIT.finditer(m.group(1))]
            here = posixpath.dirname(rel)
            proj = project.project_of(rel)
            if lits:
                for lit in lits:
                    pre = re.split(r"[*?\[{]", lit)[0]
                    pre = pre.rstrip("/") if pre.endswith("/") else posixpath.dirname(pre)
                    for base in (here, proj, ""):
                        unsafe_zones.append((posixpath.normpath(posixpath.join(base, pre)).strip("."),
                                             "%s lists/globs %r" % (rel, lit)))
            else:
                unsafe_zones.append((here, "%s reads a folder listing at run time" % rel))
    # 3. blame the groups
    for rel, line, name, pos in mentions:
        proj = project.project_of(rel)
        for g in groups:
            if g["project"] == proj and g["asset"] and any(posixpath.basename(f) == name for f in g["files"]):
                g["unsafe"].append("%s line %d mentions %r in a way I cannot rewrite safely" % (rel, line, name))
    for rel, pre in dyn_prefixes:
        for g in groups:
            if g["asset"] and any(("/" + pre + "/") in ("/" + posixpath.dirname(f) + "/") for f in g["files"]):
                g["unsafe"].append("%s builds a path from %r piece by piece" % (rel, pre))
    for g in groups:
        for z, why in unsafe_zones:
            zz = z.strip("/")
            if any(posixpath.dirname(f) == zz or posixpath.dirname(f).startswith(zz + "/") or zz == "" and False
                   for f in g["files"]):
                g["unsafe"].append(why)
    return groups, cross, texts, unsafe_zones


def find_zones(project):
    """Folders that some script lists / globs at run time: anything added or removed there changes behaviour."""
    zones = []
    for rel, size in project.files.items():
        ext = rel.rsplit(".", 1)[-1].lower() if "." in rel else ""
        if ext not in ("js", "mjs", "cjs", "jsx", "ts", "tsx", "py", "php", "vue", "svelte") or size > 3_000_000:
            continue
        try:
            text = (project.root / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for m in GLOB_CALL.finditer(text):
            here, proj = posixpath.dirname(rel), project.project_of(rel)
            lits = [x.group(2) for x in STR_LIT.finditer(m.group(1))]
            if lits:
                for lit in lits:
                    pre = re.split(r"[*?\[{]", lit)[0]
                    pre = pre.rstrip("/") if pre.endswith("/") else posixpath.dirname(pre)
                    for base in (here, proj, ""):
                        zones.append((posixpath.normpath(posixpath.join(base, pre)).strip("."), "%s lists/globs %r" % (rel, lit)))
            else:
                zones.append((here, "%s reads a folder listing at run time" % rel))
    return zones


# ----------------------------------------------------------------------------
# Planning: who is nearest, where should the survivor live
# ----------------------------------------------------------------------------
def plan_group(project, g, unsafe_zones, texts):
    files = g["files"]
    out = {"files": files, "size": g["size"], "project": g["project"], "status": "merge", "reason": "",
           "survivor": None, "destination": None, "delete": [], "edits": [], "before": {}, "after": {}}
    if not g["asset"]:
        out.update(status="report", reason="code / data files are never merged automatically (two copies of a script are two programs)")
        return out
    if g["unsafe"]:
        out.update(status="skip", reason="; ".join(sorted(set(g["unsafe"]))[:3]))
        return out
    refs = g["refs"]
    in_web = [f for f in files if project.in_web_root(f)]
    referrer_dirs = [posixpath.dirname(r.file) for r in refs]
    used = {f: [r for r in refs if r.target == f] for f in files}
    # --- nobody uses any copy: keep the shallowest one, drop the rest
    if not refs:
        keep = sorted(files, key=lambda f: (len(parts(f)), f))[0]
        out.update(survivor=keep, destination=keep, delete=[f for f in files if f != keep],
                   reason="no file refers to any copy - extra copies are dead weight")
        return out
    # --- copies reached by web address: merge in place, never move
    if in_web:
        out.update(status="skip", reason="a copy lives in a web folder (%s): removing it deletes a public web address, so a link "
                   "to it anywhere would break - the owner decides" % ", ".join(f for f in in_web[:2]))
        return out
    else:
        def cost_of(loc):
            ds = [dist(rd, loc) for rd in referrer_dirs]
            return (max(ds), sum(ds))
        # today: every user uses its own copy, after the merge all use the best surviving copy
        now = sum(dist(posixpath.dirname(r.file), posixpath.dirname(r.target)) for r in refs)
        # cost if we simply keep the best existing copy
        best_existing = min(files, key=lambda f: (cost_of(posixpath.dirname(f)), f))
        best_loc, best_cost = posixpath.dirname(best_existing), cost_of(posixpath.dirname(best_existing))
        for d in sorted(project.dirs, key=lambda d: (len(parts(d)), d)):
            if d.split("/")[0] in SKIP_DIRS or any(p.startswith(".") for p in parts(d)):
                continue
            if project.project_of(d + "/x" if d else "x") != g["project"]:
                continue
            if any(d == z.strip("/") or d.startswith(z.strip("/") + "/") for z, _w in unsafe_zones if z.strip("/")):
                continue
            if any(d == w or d.startswith(w + "/") for w in
                   [posixpath.join(g["project"], x) if g["project"] else x for x in WEB_ROOTS]):
                continue
            name = posixpath.basename(files[0])
            target = posixpath.join(d, name) if d else name
            if target in project.files and target not in files:
                continue                                  # a different file already has that name there
            c = cost_of(d)
            if c < best_cost:
                best_loc, best_cost = d, c
        name = posixpath.basename(best_existing)
        dest = posixpath.join(best_loc, name) if best_loc else name
        survivor = dest if dest in files else min(files, key=lambda f: (dist(posixpath.dirname(f), best_loc), f))
        out.update(survivor=survivor, destination=dest, delete=[f for f in files if f != survivor])
        out["moved"] = survivor != dest
        out["reason"] = "survivor placed in %s" % (best_loc or "the project root")
    out["before"] = {"steps_total": sum(dist(posixpath.dirname(r.file), posixpath.dirname(r.target)) for r in refs),
                     "users": len(refs)}
    ds = [dist(posixpath.dirname(r.file), posixpath.dirname(out["destination"])) for r in refs] if not in_web else None
    if ds is not None:
        kd = [dist(posixpath.dirname(r.file), posixpath.dirname(best_existing)) for r in refs]
        out["after"] = {"steps_total": sum(ds), "worst": max(ds), "keep_total": sum(kd), "keep_worst": max(kd)}
    # --- edits: rewrite each reference to the final location
    dest = out["destination"]
    for r in refs:
        new_raw = new_reference(project, r, dest)
        if new_raw is None:
            out.update(status="skip", reason="cannot express the new path for %s in the style it was written" % r.file,
                       edits=[], delete=[], survivor=None)
            return out
        old_path, suf = split_suffix(r.raw)
        if new_raw + suf != r.raw:
            out["edits"].append({"file": r.file, "start": r.start, "end": r.end, "old": r.raw, "new": new_raw + suf})
    if not out["edits"] and not out["delete"] and not out.get("moved"):
        out.update(status="none", reason="already the best layout")
    return out


def new_reference(project, r, dest):
    """The text to put where the old path was, written in the same style."""
    raw, _suf = split_suffix(r.raw)
    if raw.startswith("/"):
        base = r.base
        if dest == base or dest.startswith(base.rstrip("/") + "/") or base == "":
            return "/" + posixpath.relpath(dest, base or ".")
        return None
    here = posixpath.dirname(r.file)
    rel = posixpath.relpath(dest, here or ".")
    if raw.startswith("./") or raw.startswith("../"):
        return rel if rel.startswith(".") else "./" + rel
    # plain "img/a.png": it was relative to r.base
    if r.base == here:
        return rel
    base = r.base
    if dest.startswith(base.rstrip("/") + "/") or base == "":
        return posixpath.relpath(dest, base or ".")
    return None


# ----------------------------------------------------------------------------
# Verification (simulation) and applying
# ----------------------------------------------------------------------------
def apply_edits_to_text(text, edits):
    for e in sorted(edits, key=lambda e: -e["start"]):
        assert text[e["start"]:e["end"]] == e["old"], "file changed since the scan"
        text = text[:e["start"]] + e["new"] + text[e["end"]:]
    return text


def simulate(project, plans, texts, hashes):
    """Pretend to apply every plan. Return list of problems (empty = safe)."""
    problems = []
    removed, created = set(), {}
    for p in plans:
        if p["status"] != "merge" or not p["survivor"]:
            continue
        for f in p["delete"]:
            removed.add(f)
        if p["survivor"] != p["destination"]:
            removed.add(p["survivor"])
            created[p["destination"]] = hashes[p["survivor"]]
    live = (set(project.files) - removed) | set(created)
    edited = defaultdict(list)
    for p in plans:
        if p["status"] == "merge":
            for e in p["edits"]:
                edited[e["file"]].append(e)
    new_texts = {}
    for f, es in edited.items():
        try:
            new_texts[f] = apply_edits_to_text(texts[f], es)
        except AssertionError as ex:
            problems.append("%s: %s" % (f, ex))
    # every reference to a merged file must still resolve, to a file with identical content
    for p in plans:
        if p["status"] != "merge" or not p["survivor"]:
            continue
        want = hashes[p["survivor"]]
        for f in set(e["file"] for e in p["edits"]) | {r_file for r_file in []}:
            pass
        users = {e["file"] for e in p["edits"]}
        for f in users:
            for r in find_refs(f, new_texts[f]):
                cands = resolve(project, r, lambda x: x in live)
                for c, _b in cands:
                    if c in project.files and hashes.get(c) == want and c not in live:
                        problems.append("%s still points at a removed file %s" % (f, c))
                if r.raw in [e["new"] for e in p["edits"] if e["file"] == f]:
                    ok = any(hashes.get(c) == want or created.get(c) == want for c, _b in cands)
                    if not ok:
                        problems.append("%s: new path %r does not reach the file" % (f, r.raw))
    # a name clash at a destination
    seen = {}
    for p in plans:
        if p["status"] == "merge" and p["survivor"] and p["survivor"] != p["destination"]:
            if p["destination"] in seen or (p["destination"] in project.files and p["destination"] not in p["files"]):
                problems.append("two things want the same new place: %s" % p["destination"])
            seen[p["destination"]] = 1
    return problems, new_texts


def do_apply(root, plans, new_texts, bdir=None):
    root = Path(root)
    manifest = {"root": str(root), "edited": [], "deleted": [], "created": []}
    def backup(rel):
        if bdir is None:
            return
        dest = bdir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / rel, dest)
    for f, t in new_texts.items():
        backup(f)
        with open(root / f, "w", encoding="utf-8", newline="") as fh:
            fh.write(t)
        manifest["edited"].append(f)
    for p in plans:
        if p["status"] != "merge" or not p["survivor"]:
            continue
        if p["survivor"] != p["destination"]:
            backup(p["survivor"])
            dst = root / p["destination"]
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / p["survivor"], dst)
            manifest["created"].append(p["destination"])
            manifest["deleted"].append(p["survivor"])
            (root / p["survivor"]).unlink()
        for f in p["delete"]:
            backup(f)
            manifest["deleted"].append(f)
            (root / f).unlink()
    return manifest


def undo():
    base = HERE / "backups"
    sessions = sorted(p for p in base.glob("*-dedupe") if (p / "dedupe-manifest.json").exists()) if base.exists() else []
    if not sessions:
        sys.exit("No merge backups found - nothing to undo.")
    last = sessions[-1]
    man = json.loads((last / "dedupe-manifest.json").read_text(encoding="utf-8"))
    root = Path(man["root"])
    for rel in man["created"]:
        try:
            (root / rel).unlink()
        except OSError:
            pass
    for rel in man["edited"] + man["deleted"]:
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(last / rel, dst)
    (last / "dedupe-manifest.json").rename(last / "dedupe-manifest.undone.json")
    print(f"Restored {len(man['edited'])} edited and {len(man['deleted'])} removed/moved files from {last.name}.")


def build_check(root, plans, new_texts, cmd):
    """Apply the plan to a temporary copy and run the project's own build."""
    root = Path(root)
    pkg = root
    tmp = Path(tempfile.mkdtemp(prefix="arachnid-dedupe-"))
    try:
        work = tmp / "p"
        shutil.copytree(pkg, work, symlinks=True, ignore=shutil.ignore_patterns(
            "node_modules", ".git", "dist", "build", "backups", ".cache", ".wrangler", "arachnid"))
        nm = pkg / "node_modules"
        if nm.exists():
            os.symlink(nm, work / "node_modules", target_is_directory=True)
        do_apply(work, plans, new_texts, None)
        r = subprocess.run(cmd, cwd=str(work), shell=True, capture_output=True, text=True, timeout=600)
        return r.returncode == 0, (r.stderr or r.stdout)[-400:]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ----------------------------------------------------------------------------
# Command line
# ----------------------------------------------------------------------------
def mb(n):
    return "%.1f MB" % (n / 1e6) if n >= 1e5 else ("0 KB" if n == 0 else "%d KB" % max(1, n // 1000))


def main():
    ap = argparse.ArgumentParser(description="Arachnid duplicate-file merger (preview by default).")
    ap.add_argument("root", nargs="?", default=str(HERE.parent))
    ap.add_argument("--apply", action="store_true", help="really do it (backup first)")
    ap.add_argument("--verify-cmd", default=None, help='e.g. "npm run build" - run on a temporary copy before applying')
    ap.add_argument("--undo", action="store_true")
    ap.add_argument("--pixel-check", action="store_true", help="also run the safety gate during a preview (it always runs before --apply)")
    ap.add_argument("--allow-unstable", action="store_true", help="with --pixel-check: accept signals that move by themselves (animations)")
    ap.add_argument("--out", default=str(HERE / "reports" / "dedupe-report.json"))
    a = ap.parse_args()
    if a.undo:
        undo()
        return 0
    t0 = datetime.now()
    project = Project(a.root)
    print("Arachnid duplicate finder - looking at:", project.root)
    groups, cross, texts, zones = analyse(project)
    plans = [plan_group(project, g, zones, texts) for g in groups]
    hashes = {f: g["hash"] for g in groups for f in g["files"]}
    merge = [p for p in plans if p["status"] == "merge" and p["survivor"]]
    problems, new_texts = simulate(project, merge, texts, hashes) if merge else ([], {})
    if problems:
        print("\nSAFETY CHECK FAILED - the plan was thrown away:")
        for pr in problems[:10]:
            print("  -", pr)
        merge = []
    gate_held = []
    if merge and (a.apply or a.pixel_check):
        from gate import guard_units
        print("\nSafety gate: proving the product stays exactly the same (before / after on temporary copies) ...")
        def _apply_units(copy, units):
            _pr, nt = simulate(project, units, texts, hashes)
            do_apply(copy, units, nt, None)
        passed, failed, notes = guard_units(project.root, merge, _apply_units, allow_unstable=a.allow_unstable,
                                            build_cmd=(a.verify_cmd or "auto"))
        for n in notes:
            print("   " + n)
        for u, why in failed:
            print(f"   HELD BACK for the owner: {posixpath.basename(u['files'][0])} x{len(u['files'])} - {why}")
        gate_held = [u for u, _w in failed]
        merge = passed
        if merge:
            _pr, new_texts = simulate(project, merge, texts, hashes)
        else:
            new_texts = {}
    saved = sum(p["size"] * len(p["delete"]) for p in merge)
    print(f"\nDuplicate groups inside single projects: {len(groups)}   (also {len(cross)} identical files that sit in two "
          f"DIFFERENT projects - those are never merged, see the end)")
    for p in merge:
        print(f"\n  MERGE  {posixpath.basename(p['files'][0])}   ({mb(p['size'])} x {len(p['files'])} copies)")
        for f in p["files"]:
            tag = " <- survivor" if f == p["survivor"] else "   (deleted)"
            print(f"       {f}{tag}")
        if p["survivor"] != p["destination"]:
            print(f"       moved to: {p['destination']}")
        if p["before"] and p["after"]:
            a_ = p["after"]
            print(f"       steps: {p['before']['users']} user(s) walked {p['before']['steps_total']} step(s) in total to their own copies; "
                  f"after the merge: {a_['steps_total']} in total, worst single user {a_['worst']} "
                  f"(just keeping the nearest copy in place would be {a_['keep_total']} in total, worst {a_['keep_worst']})")
        print(f"       why: {p['reason']}")
        for e in p["edits"][:6]:
            print(f"       edit {e['file']}: {e['old']}  ->  {e['new']}")
        if len(p["edits"]) > 6:
            print(f"       ... and {len(p['edits']) - 6} more edits")
    skipped = [p for p in plans if p["status"] == "skip"]
    reported = [p for p in plans if p["status"] == "report"]
    if skipped:
        print("\n  LEFT ALONE (not safe to merge automatically):")
        for p in skipped[:15]:
            print(f"    {posixpath.basename(p['files'][0])}  x{len(p['files'])}  - {p['reason']}")
        if len(skipped) > 15:
            print(f"    ... and {len(skipped) - 15} more")
    if reported:
        print(f"\n  CODE / DATA duplicates (reported only): {len(reported)} group(s), e.g. {reported[0]['files'][:2]}")
    if cross:
        dirs = defaultdict(int)
        for g in cross:
            dirs[tuple(sorted({project.project_of(f) or '(root)' for f in g['files']}))] += 1
        print("\n  The same files exist in more than one project folder (each one is deployed on its own, so they cannot share):")
        for k, v in sorted(dirs.items(), key=lambda kv: -kv[1])[:6]:
            print(f"    {v:4d} files shared by: {', '.join(k)}")
    print("\n---------------- Summary ----------------")
    print(f"Merge: {len(merge)} group(s), {sum(len(p['delete']) for p in merge)} extra copies removed, "
          f"{mb(saved)} saved, {sum(len(p['edits']) for p in merge)} reference(s) rewritten")
    print(f"Left alone: {len(skipped)} group(s)    Reported only: {len(reported)}")
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps({"checked_at": datetime.now().isoformat(timespec="seconds"), "root": str(project.root),
                                       "plans": plans, "cross_project_files": len(cross)}, indent=1), encoding="utf-8")
    try:
        import ledger
        ledger.reset("dedupe")
        held_ids = {id(u) for u in gate_held}
        for p in merge:
            ledger.record("dedupe", "fixed" if a.apply else "fixable", "info", p["survivor"], 0,
                          "%d identical copies of %s can be merged into one" % (len(p["files"]), posixpath.basename(p["files"][0])), kind="duplicate")
        for p in gate_held:
            ledger.record("dedupe", "held", "warning", p["files"][0], 0, "%d identical copies; merge held back by the safety gate" % len(p["files"]),
                          "merging could change how the product behaves", "decide whether to merge by hand", "duplicate")
        for p in skipped:
            ledger.record("dedupe", "decision", "info", p["files"][0], 0, "%d identical copies left alone: %s" % (len(p["files"]), p["reason"]), kind="duplicate")
        for p in reported:
            ledger.record("dedupe", "idea", "info", p["files"][0], 0, "%d identical code/data files" % len(p["files"]), kind="duplicate-code")
    except Exception:
        pass
    if not a.apply:
        print("\nPreview only - nothing was changed. To do it:  python dedupe.py --apply")
        print(f"Took {(datetime.now() - t0).total_seconds():.1f}s")
        return 0
    if not merge:
        print("Nothing safe to apply.")
        return 0
    if a.verify_cmd:
        print(f"\nRunning your build on a temporary copy: {a.verify_cmd} ...")
        ok, tail = build_check(project.root, merge, new_texts, a.verify_cmd)
        if not ok:
            print("The build FAILED with the merge applied, so nothing was changed.\n" + tail)
            return 1
        print("Build passed.")
    bdir = HERE / "backups" / (datetime.now().strftime("%Y%m%d-%H%M%S") + "-dedupe")
    man = do_apply(project.root, merge, new_texts, bdir)
    (bdir / "dedupe-manifest.json").write_text(json.dumps(man, indent=2), encoding="utf-8")
    print(f"\nDone. Backup: {bdir}\nTo put everything back:  python dedupe.py --undo")
    return 0


if __name__ == "__main__":
    sys.exit(main())
