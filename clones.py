#!/usr/bin/env python3
"""
Arachnid - Step 5: NESTED CLONE FOLDERS   (main/main, src/src/src ...)  - fold them back in, lose nothing
=========================================================================================================
Sometimes a folder ends up with a folder of the same name inside it: `main/main`. It is a stale copy, an old
checkout dropped in by accident, or a tool that ran in the wrong place.

Instead of just flagging it, this tool CONSOLIDATES it into the folder it sits in, so the project ends up with
one correct folder and nothing lost:

   for every file that exists in the inner folder (X/X) and the outer folder (X):
       identical            -> nothing to do
       outer is newer       -> the outer file stays (the inner, older version is kept in the backup)
       inner is newer       -> the inner file is copied UP, the outer one it replaces is kept in the backup
       exists only inside   -> copied UP into the outer folder, same relative place
   X/X/X (a third level) is folded in the same way, deepest first.
   Anything outside that pointed into X/X is rewritten to point at the outer folder, in the same style.
   Finally the now-empty-of-news inner folder is MOVED (not deleted) into arachnid/backups/<time>-clones/ -
   even a nested .git history is kept there.

"Newer" comes from git history when the folder is in a git repo, otherwise from file dates. If the age cannot
be told apart, the OUTER file wins and the inner version is kept in the backup (and listed in the report).

It refuses (and says exactly why) only when it cannot do the folding without a risk:
   * something outside refers to the inner folder in a way that cannot be rewritten safely, or
   * something refers to an inner file whose content differs from the version that would survive
     (rewriting it would silently change what that page shows).

Run:
    python clones.py                 preview (default)
    python clones.py --apply         do it (everything is backed up first)
    python clones.py --undo          put everything back, byte for byte
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
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from dedupe import Project, SKIP_DIRS as _SKIP, TEXT_EXT, find_refs, resolve, new_reference, split_suffix, find_zones, WEB_ROOTS  # noqa: E402

SKIP = set(_SKIP) - {".cms-trash", ".cms-tmp"}
GIT = shutil.which("git")


def sha1(path):
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tree(base):
    out = {}
    for dp, dn, fn in os.walk(base):
        dn[:] = [d for d in dn if d not in SKIP]
        for f in fn:
            p = os.path.join(dp, f)
            out[os.path.relpath(p, base).replace(os.sep, "/")] = p
    return out


def git_time(path):
    d = os.path.dirname(path)
    while True:
        if os.path.exists(os.path.join(d, ".git")):
            break
        nd = os.path.dirname(d)
        if nd == d:
            return None
        d = nd
    if not GIT:
        return None
    try:
        r = subprocess.run([GIT, "log", "-1", "--format=%ct", "--", os.path.relpath(path, d)], cwd=d,
                           capture_output=True, text=True, timeout=20)
        if r.returncode == 0 and r.stdout.strip():
            return int(r.stdout.strip())
    except Exception:
        pass
    return None


def newer(a, b):
    """Is file a newer than file b?  Returns (True/False/None, how-we-know). None = cannot tell."""
    ma, mb_ = int(os.path.getmtime(a)), int(os.path.getmtime(b))
    if abs(ma - mb_) > 3600:
        return ma > mb_, "file dates"
    ga, gb = git_time(a), git_time(b)
    if ga is not None and gb is not None and ga != gb:
        return ga > gb, "git history"
    return None, "dates are too close to tell"


def find_nested(root):
    found = []
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in SKIP]
        base = os.path.basename(dp)
        if base in dn:
            found.append((os.path.relpath(dp, root).replace(os.sep, "/").strip("."),
                          os.path.relpath(os.path.join(dp, base), root).replace(os.sep, "/")))
    found.sort(key=lambda t: len(t[0].split("/")))
    keep = []
    for o, i in found:
        if not any(i.startswith(k[1] + "/") for k in keep):
            keep.append((o, i))
    return keep


def layers_of(root, outer, inner):
    name = posixpath.basename(inner)
    ls, cur = [outer, inner], inner
    while (root / cur / name).is_dir():
        cur = cur + "/" + name
        ls.append(cur)
    return ls


def layer_files(root, layers):
    """For each layer, its OWN files (not those that belong to the next layer down), as {rel: abs}."""
    name = posixpath.basename(layers[1])
    res = []
    for i, l in enumerate(layers):
        t = tree(root / l)
        if i < len(layers) - 1:
            t = {k: v for k, v in t.items() if not k.startswith(name + "/")}
        res.append(t)
    return res


# ----------------------------------------------------------------------------
# Planning
# ----------------------------------------------------------------------------
def plan_one(project, root, outer, inner):
    layers = layers_of(root, outer, inner)
    files = layer_files(root, layers)
    plan = {"outer": outer, "inner": inner, "layers": layers, "copy_up": [], "replace": [], "same": 0,
            "outer_wins": [], "guess": [], "edits": [], "blockers": [], "notes": [],
            "size": sum(os.path.getsize(p) for t in files[1:] for p in t.values() if os.path.exists(p))}
    h = {}
    def H(p):
        if p not in h:
            h[p] = sha1(p)
        return h[p]
    final = {}                        # rel -> abs path of the version that survives in the outer folder
    allrels = sorted(set().union(*[set(t) for t in files]))
    for rel in allrels:
        have = [(i, t[rel]) for i, t in enumerate(files) if rel in t]
        if len({H(p) for _i, p in have}) == 1:
            if have[0][0] == 0:
                if len(have) > 1:
                    plan["same"] += 1
                final[rel] = have[0][1]
            else:
                plan["copy_up"].append(rel)
                final[rel] = have[0][1]
            continue
        # contents differ: newest wins; outer wins when we cannot tell
        win_i, win_p, how = have[0][0], have[0][1], None
        for i, p in have[1:]:
            n, why = newer(p, win_p)
            if n:
                win_i, win_p, how = i, p, why
            elif n is None and how is None:
                how = why
        final[rel] = win_p
        if win_i == 0:
            plan["outer_wins"].append(rel)
            if how and "too close" in how:
                plan["guess"].append(rel)
        else:
            plan["replace"].append(rel)
    plan["final"] = {k: final[k] for k in final}
    plan["final_hash"] = {k: H(v) for k, v in final.items()}
    # --- who outside points into the inner folder?
    exists = lambda p: p in project.files
    lit = re.compile(r"(?<![A-Za-z0-9_.-])%s(?![A-Za-z0-9_-])" % re.escape(inner))
    hash_of_rel = {}
    for rel in project.files:
        if rel == inner or rel.startswith(inner + "/"):
            continue
        ext = rel.rsplit(".", 1)[-1].lower() if "." in rel else ""
        if ext not in TEXT_EXT or project.files[rel] > 3_000_000 or rel.endswith("package-lock.json"):
            continue
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        refs = find_refs(rel, text)
        covered = []
        for r in refs:
            cands = resolve(project, r, exists)
            inside = [(c, b) for c, b in cands if c.startswith(inner + "/")]
            if not inside:
                continue
            covered.append((r.start, r.end))
            if len(cands) != 1:
                plan["blockers"].append("%s: %r could mean more than one file" % (rel, r.raw))
                continue
            c, b = inside[0]
            r.target, r.base = c, b
            rel_in = c[len(inner) + 1:]
            # which layer does it live in, and what survives at that place?
            key = rel_in
            nm = posixpath.basename(inner) + "/"
            while key.startswith(nm) and key not in final:
                key = key[len(nm):]
            if key not in final:
                plan["blockers"].append("%s: %r points to a file the merge cannot account for" % (rel, r.raw))
                continue
            if H(root / c) != plan["final_hash"][key]:
                plan["blockers"].append(
                    "%s: %r points to an inner version of the file that differs from the one that survives" % (rel, r.raw))
                continue
            dest = posixpath.join(outer, key)
            new_raw = new_reference(project, r, dest)
            if new_raw is None:
                plan["blockers"].append("%s: cannot express the new path for %r in the style it was written" % (rel, r.raw))
                continue
            _p, suf = split_suffix(r.raw)
            if new_raw + suf != r.raw:
                plan["edits"].append({"file": rel, "start": r.start, "end": r.end, "old": r.raw, "new": new_raw + suf,
                                      "dest": dest})
        for m in lit.finditer(text):
            if not any(a <= m.start() < b for a, b in covered):
                if ext in ("md", "mdx", "txt", "rst"):          # plain documentation: safe to just update the words
                    plan["edits"].append({"file": rel, "start": m.start(), "end": m.end(), "old": m.group(0),
                                          "new": outer or ".", "dest": outer, "doc": True})
                    continue
                plan["blockers"].append("%s line %d mentions %r in a way I cannot rewrite safely"
                                        % (rel, text.count("\n", 0, m.start()) + 1, m.group(0)))
    # --- strict rule: the product must not change in any way. Anything that could change it is the owner's decision.
    if plan["replace"]:
        plan["blockers"].append("%d file(s) are NEWER inside the nested folder (%s): putting them in place would change the "
                                "product, so the owner decides which version is right" % (len(plan["replace"]), ", ".join(plan["replace"][:3])))
    zones = find_zones(project)
    for rel in plan["copy_up"]:
        dest = posixpath.join(outer, rel)
        if project.in_web_root(dest):
            plan["blockers"].append("%s would become a new public web address" % dest)
            break
        for z, why in zones:
            zz = z.strip("/")
            if zz and (posixpath.dirname(dest) == zz or posixpath.dirname(dest).startswith(zz + "/")):
                plan["blockers"].append("%s would be picked up by a script (%s)" % (dest, why))
                break
        else:
            continue
        break
    plan["blockers"] = plan["blockers"][:6]
    iabs = root / inner
    if (iabs / ".git").exists():
        plan["notes"].append("it contains its own .git (a repository inside a repository) - its history is kept in the backup")
    for f in ("wrangler.toml", "wrangler.jsonc", "netlify.toml", "vercel.json"):
        if (iabs / f).exists():
            plan["notes"].append("it has its own %s (it could have been deployed on its own)" % f)
    return plan


def simulate(project, root, plan):
    """Pretend to apply the edits. Every rewritten path must reach a file with the original content."""
    problems = []
    inner = plan["inner"]
    live = {f for f in project.files if not (f == inner or f.startswith(inner + "/"))}
    for rel in plan["final"]:
        live.add(posixpath.join(plan["outer"], rel))
    by_file = {}
    for e in plan["edits"]:
        by_file.setdefault(e["file"], []).append(e)
    for f, es in by_file.items():
        text = (root / f).read_text(encoding="utf-8")
        for e in sorted(es, key=lambda e: -e["start"]):
            if text[e["start"]:e["end"]] != e["old"]:
                problems.append("%s changed since the scan" % f)
                break
            text = text[:e["start"]] + e["new"] + text[e["end"]:]
        else:
            for r in find_refs(f, text):
                if any(r.raw == e["new"] and not e.get("doc") for e in es):
                    cands = resolve(project, r, lambda p: p in live)
                    want = next(e["dest"] for e in es if e["new"] == r.raw)
                    if not any(c == want for c, _b in cands):
                        problems.append("%s: new path %r does not reach %s" % (f, r.raw, want))
    return problems


# ----------------------------------------------------------------------------
# Applying and undoing
# ----------------------------------------------------------------------------
def apply_plan(root, plan, bdir):
    man = {"root": str(root), "moved": [], "created": [], "restored": [], "edited": []}
    outer = root / plan["outer"]
    def keep(rel):
        d = bdir / "originals" / rel
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / rel, d)
    # 1. copy files UP (replacing the older outer file only after saving it)
    for rel in plan["copy_up"] + plan["replace"]:
        src = plan["final"][rel]
        dst = outer / rel
        dst_rel = dst.relative_to(root).as_posix()
        if dst.exists():
            keep(dst_rel)
            man["restored"].append(dst_rel)
        else:
            man["created"].append(dst_rel)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    # 2. rewrite references
    by_file = {}
    for e in plan["edits"]:
        by_file.setdefault(e["file"], []).append(e)
    for f, es in by_file.items():
        keep(f)
        text = (root / f).read_text(encoding="utf-8")
        for e in sorted(es, key=lambda e: -e["start"]):
            text = text[:e["start"]] + e["new"] + text[e["end"]:]
        with open(root / f, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        man["edited"].append(f)
    # 3. move the inner folder aside (nothing is deleted)
    dest = bdir / "moved" / plan["inner"]
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(root / plan["inner"], dest)
    except OSError:
        shutil.copytree(root / plan["inner"], dest, symlinks=True)
        shutil.rmtree(root / plan["inner"])
    man["moved"].append(plan["inner"])
    return man


def undo():
    base = HERE / "backups"
    ses = sorted(p for p in base.glob("*-clones") if (p / "clones-manifest.json").exists()) if base.exists() else []
    if not ses:
        sys.exit("No clone backups found - nothing to undo.")
    last = ses[-1]
    man = json.loads((last / "clones-manifest.json").read_text(encoding="utf-8"))
    root = Path(man["root"])
    for rel in man["created"]:
        try:
            (root / rel).unlink()
        except OSError:
            pass
    for rel in man["restored"] + man["edited"]:
        shutil.copy2(last / "originals" / rel, root / rel)
    for rel in man["moved"]:
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.rename(last / "moved" / rel, dest)
        except OSError:
            shutil.copytree(last / "moved" / rel, dest, symlinks=True)
            shutil.rmtree(last / "moved" / rel)
    (last / "clones-manifest.json").rename(last / "clones-manifest.undone.json")
    print(f"Moved {len(man['moved'])} folder(s) back, restored {len(man['restored']) + len(man['edited'])} file(s) and "
          f"removed {len(man['created'])} copied-up file(s).")


def mb(n):
    return "%.1f MB" % (n / 1e6) if n >= 1e5 else ("0 KB" if n == 0 else "%d KB" % max(1, n // 1000))


def main():
    ap = argparse.ArgumentParser(description="Arachnid nested-folder consolidator (preview by default).")
    ap.add_argument("root", nargs="?", default=str(HERE.parent))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--undo", action="store_true")
    ap.add_argument("--pixel-check", action="store_true", help="also run the safety gate during a preview (it always runs before --apply)")
    ap.add_argument("--allow-unstable", action="store_true", help="with --pixel-check: accept signals that move by themselves (animations)")
    ap.add_argument("--verify-cmd", default="auto", help="build command for the behaviour gate (default: npm run build if the project has it)")
    ap.add_argument("--out", default=str(HERE / "reports" / "clones-report.json"))
    a = ap.parse_args()
    if a.undo:
        undo()
        return 0
    root = Path(a.root).resolve()
    t0 = datetime.now()
    print("Arachnid nested-folder finder - looking at:", root)
    pairs = find_nested(root)
    if not pairs:
        print("\nNo folder contains a folder of the same name. Nothing to do.")
        return 0
    project = Project(root)
    plans, ready = [], []
    for outer, inner in pairs:
        p = plan_one(project, root, outer, inner)
        if not p["blockers"]:
            p["blockers"] = simulate(project, root, p)
        plans.append(p)
        ok = not p["blockers"]
        print(f"\n  {'FOLD IN' if ok else 'BLOCKED'}  {inner}/   into   {outer or '(root)'}/     ({mb(p['size'])} in the nested folder)")
        print(f"       identical already: {p['same']}   older copies (outer kept): {len(p['outer_wins'])}")
        print(f"       copied UP because only the inner has them: {len(p['copy_up'])}   "
              f"copied UP because the inner is newer: {len(p['replace'])}")
        if p["copy_up"]:
            print("         unique: " + ", ".join(p["copy_up"][:5]) + (f" ... (+{len(p['copy_up']) - 5})" if len(p["copy_up"]) > 5 else ""))
        if p["replace"]:
            print("         newer inside: " + ", ".join(p["replace"][:5]) + (f" ... (+{len(p['replace']) - 5})" if len(p["replace"]) > 5 else ""))
        if p["guess"]:
            print(f"       could not tell which is newer for {len(p['guess'])} file(s): the outer one stays, the inner one is kept in the backup")
        if p["edits"]:
            print(f"       references rewritten: {len(p['edits'])}  e.g. {p['edits'][0]['file']}: {p['edits'][0]['old']} -> {p['edits'][0]['new']}")
        for n in p["notes"]:
            print(f"       note: {n}")
        for b in p["blockers"]:
            print(f"       cannot do it safely: {b}")
        if ok:
            ready.append(p)
    if ready and (a.apply or a.pixel_check):
        from gate import guard_units
        import tempfile as _tf
        print("\nSafety gate: proving the product stays exactly the same (before / after on temporary copies) ...")
        def _apply_units(copy, units):
            for p_ in units:
                apply_plan(copy, p_, Path(_tf.mkdtemp(prefix="arachnid-gate-bk-")))
        passed, failed, notes = guard_units(root, ready, _apply_units, allow_unstable=a.allow_unstable, build_cmd=a.verify_cmd)
        for n in notes:
            print("   " + n)
        for u, why in failed:
            print(f"   HELD BACK for the owner: {u['inner']}/ - {why}")
        ready = passed
    print("\n---------------- Summary ----------------")
    print(f"Nested folders: {len(plans)}    can be folded in safely: {len(ready)}    blocked: {len(plans) - len(ready)}")
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    slim = [{k: v for k, v in p.items() if k not in ("final", "final_hash")} for p in plans]
    Path(a.out).write_text(json.dumps({"checked_at": datetime.now().isoformat(timespec="seconds"), "root": str(root),
                                       "plans": slim}, indent=1), encoding="utf-8")
    try:
        import ledger
        ledger.reset("clones")
        ready_ids = {id(q) for q in ready}
        for p in plans:
            if id(p) in ready_ids:
                ledger.record("clones", "fixed" if a.apply else "fixable", "info", p["inner"], 0,
                              "nested folder %s/ can be folded into %s/ without losing anything" % (p["inner"], p["outer"] or "(root)"), kind="nested-folder")
            elif p["blockers"]:
                ledger.record("clones", "decision", "warning", p["inner"], 0,
                              "nested folder %s/ not folded in: %s" % (p["inner"], "; ".join(p["blockers"][:2])[:220]),
                              "folding it in would change what the product does or exposes", "decide whether to bring its files in or leave them", "nested-folder")
            else:
                ledger.record("clones", "held", "warning", p["inner"], 0, "nested folder %s/ fold-in held back by the safety gate" % p["inner"],
                              "it could change how the product behaves", "decide by hand", "nested-folder")
    except Exception:
        pass
    if not a.apply:
        print("\nPreview only - nothing was changed." + ("  To do it:  python clones.py --apply" if ready else ""))
        print(f"Took {(datetime.now() - t0).total_seconds():.1f}s")
        return 0
    if not ready:
        print("Nothing safe to apply.")
        return 0
    bdir = HERE / "backups" / (datetime.now().strftime("%Y%m%d-%H%M%S") + "-clones")
    bdir.mkdir(parents=True, exist_ok=True)
    total = {"root": str(root), "moved": [], "created": [], "restored": [], "edited": []}
    for p in ready:
        locked = sorted({e["file"] for e in p["edits"] if not os.access(root / e["file"], os.W_OK)})
        if locked:                                 # all-or-nothing: a read-only page means this folder is left alone
            print("SKIPPED %s - these files would need new paths but are read-only: %s" % (p["inner"], ", ".join(locked[:5])))
            continue
        m = apply_plan(root, p, bdir)
        for k in ("moved", "created", "restored", "edited"):
            total[k] += m[k]
    if not total["moved"]:                         # everything was skipped: leave no empty undo step behind
        shutil.rmtree(bdir, ignore_errors=True)
        return 0
    (bdir / "clones-manifest.json").write_text(json.dumps(total, indent=2), encoding="utf-8")
    print(f"\nDone. Backup (the nested folder was MOVED here, not deleted): {bdir}\nTo put everything back:  python clones.py --undo")
    return 0


if __name__ == "__main__":
    sys.exit(main())
