#!/usr/bin/env python3
"""
ARACHNID - PROPOSALS: the "ask a human first" queue
===================================================
Some improvements change HOW code works (faster lookups, a lighter library, a safer network call).
Arachnid never does these by itself.  It writes each one here as a PROPOSAL, and nothing happens until YOU approve it.

    python arachnid.py proposals              list every proposal (id, benefit, risk, file)
    python arachnid.py approve P-1a2b3c ...   say YES to specific proposals   (or:  approve all   = every pending one that has an exact patch)
    python arachnid.py reject  P-1a2b3c ...   say NO (it is not proposed again)
    python arachnid.py apply-approved         apply the approved ones - each through the SAFETY GATE; whatever the gate cannot prove is held back
    python proposals.py --undo                put the last applied batch back

Two kinds of proposal:
  patch   an exact, mechanical edit Arachnid can make after you approve (and the gate agrees)
  manual  a bigger idea (a different framework, a redesign) - Arachnid explains it; you or your team do it
"""
import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
PATH = HERE / "reports" / "proposals.json"


def make_id(tool, kind, file, key):
    return "P-" + hashlib.sha1(("%s|%s|%s|%s" % (tool, kind, file, key)).encode("utf-8")).hexdigest()[:6]


def load():
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save(d):
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(d, indent=1, ensure_ascii=False), encoding="utf-8")


def sync(tool, found, root=None):
    """found: list of dicts (id, kind, file, line, title, benefit, risk, patches|[])). Keeps earlier human decisions."""
    cur = load()
    new_ids = {f["id"] for f in found}
    for pid in [k for k, v in cur.items() if v.get("tool") == tool and k not in new_ids and v.get("status") != "applied"]:
        del cur[pid]
    for f in found:
        old = cur.get(f["id"])
        status = old["status"] if old else "pending"
        extra = {k: old[k] for k in ("approved_at", "held_why") if old and k in old}
        cur[f["id"]] = dict(f, tool=tool, status=status, root=str(root) if root else None, **extra)
    save(cur)
    return cur


def show(d=None, only=None):
    d = d if d is not None else load()
    items = [(k, v) for k, v in d.items() if not only or v["status"] in only]
    if not items:
        print("No proposals. (Run:  python arachnid.py advise   and   python arachnid.py netcheck)")
        return
    order = {"approved": 0, "pending": 1, "held": 2, "rejected": 3, "applied": 4}
    items.sort(key=lambda kv: (order.get(kv[1]["status"], 9), kv[1]["file"], kv[1].get("line", 0)))
    for pid, p in items:
        print("[%s] %-8s %s:%s" % (pid, p["status"].upper(), p["file"], p.get("line", "")))
        print("    %s" % p["title"])
        print("    gain: %s" % p["benefit"])
        print("    risk: %s" % p["risk"])
        if p.get("patches"):
            q = p["patches"][0]
            print("    exact edit available: yes (%d change%s)   ->  '%s'  becomes  '%s'" % (len(p["patches"]), "" if len(p["patches"]) == 1 else "s", q["old"][:60].replace("\n", " "), q["new"][:60].replace("\n", " ")))
        else:
            print("    exact edit available: no - this one is for a human to do")
        if p.get("held_why"):
            print("    held back: " + p["held_why"])
    print("\nSay yes:  python arachnid.py approve <id> [<id> ...]     Say no:  python arachnid.py reject <id>     Then:  python arachnid.py apply-approved")


def set_status(ids, status):
    d = load()
    if ids == ["all"]:
        ids = [k for k, v in d.items() if v["status"] == "pending" and v.get("patches")] if status == "approved" else [k for k, v in d.items() if v["status"] == "pending"]
    done = 0
    for i in ids:
        if i in d and d[i]["status"] in ("pending", "held", "approved", "rejected"):
            d[i]["status"] = status
            if status == "approved":
                d[i]["approved_at"] = datetime.now().isoformat(timespec="seconds")
                d[i].pop("held_why", None)
            done += 1
        else:
            print("  (no proposal called %s)" % i)
    save(d)
    print("%d proposal(s) marked %s." % (done, status.upper()))
    if status == "approved" and done:
        print("Nothing has been changed yet.  To apply them through the safety gate:  python arachnid.py apply-approved")


def apply_patches(text, patches):
    """patches: [{at, old, new}] applied from the end; every 'old' must sit exactly where it was proposed."""
    for pt in sorted(patches, key=lambda p: -p["at"]):
        if text[pt["at"]:pt["at"] + len(pt["old"])] != pt["old"]:
            raise ValueError("the file changed since this was proposed")
        text = text[:pt["at"]] + pt["new"] + text[pt["at"] + len(pt["old"]):]
    return text


def apply_approved(root):
    from gate import guard_units
    d = load()
    if root is None:
        roots = {v.get("root") for v in d.values() if v["status"] == "approved" and v.get("patches") and v.get("root")}
        if len(roots) > 1:
            print("Approved proposals belong to different projects (%s) - run again with --root <folder>." % ", ".join(sorted(roots)))
            return 1
        root = roots.pop() if roots else str(HERE.parent)
    root = Path(root).resolve()
    d = {k: v for k, v in d.items() if v.get("root") and Path(v["root"]).resolve() == root}
    full = load()
    units = [k for k, v in d.items() if v["status"] == "approved" and v.get("patches")]
    manual = [k for k, v in d.items() if v["status"] == "approved" and not v.get("patches")]
    if manual:
        print("%d approved proposal(s) have no exact edit - those are yours to do by hand: %s" % (len(manual), ", ".join(manual)))
    if not units:
        print("No approved proposals with an exact edit - nothing to apply.")
        return 0
    # one proposal = one unit; several proposals in one file are applied together, but only if the file still matches
    originals = {}
    for pid in units:
        f = d[pid]["file"]
        if f not in originals:
            originals[f] = (root / f).read_text(encoding="utf-8")

    def build(subset):
        by = {}
        for pid in subset:
            by.setdefault(d[pid]["file"], []).extend(d[pid]["patches"])
        return {f: apply_patches(originals[f], ps) for f, ps in by.items()}

    ok_units = []
    for pid in units:                                           # first: does each edit still fit the file as it is now?
        try:
            build([pid])
            ok_units.append(pid)
        except ValueError as e:
            d[pid]["status"], d[pid]["held_why"] = "held", str(e) + " - run advise again"
    # shorter-code proposals (slim) carry a PROOF: each one must still be the same program, checked again now
    pre_passed = []
    slim_units = [pid for pid in ok_units if d[pid].get("verify") == "slim"]
    if slim_units:
        from gate import proven_same_program
        for pid in slim_units:
            f = d[pid]["file"]
            if f in proven_same_program(root, [(f, originals[f], build([pid])[f])]):
                pre_passed.append(pid)
            else:
                d[pid]["status"], d[pid]["held_why"] = "held", "the proof that it is the same program did not hold any more"
        for f in sorted({d[pid]["file"] for pid in pre_passed}):      # several in one file: the whole file is proven too
            mine = [pid for pid in pre_passed if d[pid]["file"] == f]
            if len(mine) > 1 and f not in proven_same_program(root, [(f, originals[f], build(mine)[f])]):
                for pid in mine:
                    d[pid]["status"], d[pid]["held_why"] = "held", "together with the other shorter versions in this file the proof did not hold"
                pre_passed = [pid for pid in pre_passed if pid not in mine]
        if pre_passed:
            print("Proved identical (the same program, written shorter): %s" % ", ".join(sorted({d[pid]["file"] for pid in pre_passed})))
        ok_units = [pid for pid in ok_units if pid not in slim_units]
    # AI answers that were TESTED (not proven): the side-by-side test runs again now, on the files as they are
    ai_units = [pid for pid in ok_units if d[pid].get("verify") == "slim-ai"]
    if ai_units:
        import slim_ai
        for pid in ai_units:
            ok, why = slim_ai.retest(root, d[pid])
            if not ok:
                d[pid]["status"], d[pid]["held_why"] = "held", "the side-by-side test did not pass again: " + why
        ok_units = [pid for pid in ok_units if d[pid]["status"] != "held"]
        if not slim_ai.other_proof_exists(root):
            tested = [pid for pid in ai_units if d[pid]["status"] != "held"]
            if tested:
                print("Tested again side by side (no pages, build or tests of its own to check as well): %s" % ", ".join(tested))
            pre_passed += tested
            ok_units = [pid for pid in ok_units if pid not in tested]
    print("Safety gate: proving the product behaves exactly the same after the approved edits ...")

    def apply_units(copy, subset):
        for f, t in build(subset).items():
            with open(Path(copy) / f, "w", encoding="utf-8", newline="") as fh:
                fh.write(t)

    passed, failed, notes = guard_units(root, ok_units, apply_units) if ok_units else ([], [], [])
    passed = list(pre_passed) + list(passed)
    for n in notes:
        print("   " + n)
    for pid, why in failed:
        d[pid]["status"], d[pid]["held_why"] = "held", why
        print("   HELD BACK: %s - %s" % (pid, why))
    if passed:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S") + "-advice"
        bdir = HERE / "backups" / stamp
        new_texts = build(passed)
        for f, t in new_texts.items():
            dest = bdir / f
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / f, dest)
            with open(root / f, "w", encoding="utf-8", newline="") as fh:
                fh.write(t)
        (bdir / "advice-manifest.json").write_text(json.dumps({"root": str(root), "files": sorted(new_texts), "applied": passed}, indent=2), encoding="utf-8")
        for pid in passed:
            d[pid]["status"] = "applied"
        print("\nApplied %d approved proposal(s) in %d file(s).  Backup: %s\nTo put them back:  python arachnid.py undo" % (len(passed), len(new_texts), bdir))
    full.update(d)
    save(full)
    return 0


def undo():
    base = HERE / "backups"
    sessions = sorted(p for p in base.glob("*") if (p / "advice-manifest.json").exists()) if base.exists() else []
    if not sessions:
        sys.exit("No advice backups found - nothing to undo.")
    last = sessions[-1]
    man = json.loads((last / "advice-manifest.json").read_text(encoding="utf-8"))
    root = Path(man["root"])
    for rel in man["files"]:
        shutil.copy2(last / rel, root / rel)
    d = load()
    for pid in man.get("applied", []):
        if pid in d:
            d[pid]["status"] = "approved"
    save(d)
    (last / "advice-manifest.json").rename(last / "advice-manifest.undone.json")
    print("Restored %d file(s) from backup %s.  (The proposals are back to APPROVED; reject them if you changed your mind.)" % (len(man["files"]), last.name))


def main():
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Arachnid proposals - nothing changes until a human approves")
    ap.add_argument("cmd", nargs="?", choices=["list", "approve", "reject", "apply"], default="list")
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--root", default=None)
    ap.add_argument("--undo", action="store_true")
    a = ap.parse_args()
    if a.undo:
        return undo()
    if a.cmd == "list":
        return show()
    if a.cmd in ("approve", "reject"):
        if not a.ids:
            sys.exit("Say which ones, for example:  approve P-1a2b3c   (or: approve all)")
        return set_status(a.ids, "approved" if a.cmd == "approve" else "rejected")
    return apply_approved(a.root)


if __name__ == "__main__":
    sys.exit(main())
