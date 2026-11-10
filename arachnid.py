#!/usr/bin/env python3
"""
ARACHNID - one command for everything
=====================================
    python arachnid.py scan  [folder]     LOOK ONLY: crawl, spelling, syntax, duplicate files, nested folders
    python arachnid.py fix   [folder]     FIX what is safe (every code/file fix goes through the safety gate first)
    python arachnid.py insights [folder]  SUGGESTIONS only: memory leaks, heavy memory, dead code, lighter libraries
    python arachnid.py advise [folder]    ADVICE: lighter frameworks, faster/smaller code (proposals - nothing changes)
    python arachnid.py netcheck [folder]  every outside connection, mapped and checked (add --online to knock on each address)
    python arachnid.py size [folder]      WEIGH every page (what a visitor downloads); --build weighs the real build, --online adds outside files
    python arachnid.py leaks [folder]     PROVE memory leaks: use each page again and again in a hidden browser and watch the memory (about 1-3 s per page)
    python arachnid.py proposals          list what waits for your yes;  approve <id>|all, reject <id>, apply-approved (through the safety gate)
    python arachnid.py triage             sort the last results: ALARM (critical) / needs you / handled / ideas
    python arachnid.py undo               put back the most recent fix (run it again to go further back)
    python arachnid.py selftest           prove, on this computer, that the whole chain works (temporary project)
    python arachnid.py crawl [folder]     just print the file tree

[folder] defaults to the folder above arachnid/.  Nothing is ever deleted: removed things go to arachnid/backups/.
Order of fixing:  1 syntax  ->  2 unused imports/variables  ->  3 duplicate files  ->  4 nested folders  ->  5 spelling  ->  6 meaning breakers in text  ->  7 terminology
(broken code is repaired first so that the later steps can read it; spelling last because it changes visible text
on purpose and therefore does not go through the gate unless you add --gate-spelling).
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = sys.executable


def run(tool, *args):
    print("\n" + "=" * 78)
    print("  " + " ".join([tool] + [a for a in args if not a.startswith("--out")]))
    print("=" * 78, flush=True)
    return subprocess.call([PY, str(HERE / tool), *args])


def scan(root, webhook=None):
    steps = [("crawler.py", [root]), ("spellcheck.py", [root]), ("syntaxcheck.py", [root, "--fix", "--preview"]),
             ("logiccheck.py", [root]), ("moneycheck.py", [root]), ("bugcheck.py", [root]), ("seccheck.py", [root]), ("vulncheck.py", [root]), ("textcheck.py", [root]), ("terms.py", [root]), ("netcheck.py", [root]), ("bundlesize.py", [root]), ("advisor.py", [root]), ("links.py", [root]), ("pagecheck.py", [root]), ("dedupe.py", [root]), ("clones.py", [root]), ("insights.py", [root])]
    codes = {}
    for tool, args in steps:
        if not (HERE / tool).exists():
            continue
        codes[tool] = run(tool, *args)
    print("\n" + "=" * 78)
    print("  SCAN FINISHED - nothing was changed. To fix what is safe:  python arachnid.py fix")
    print("=" * 78)
    return run("triage.py", *(["--webhook", webhook] if webhook else []))


def fix(root, gate_spelling, webhook=None, with_triage=True):
    print("Fixing, step by step. Every step makes a backup first; every code or file change must pass the safety gate.")
    run("syntaxcheck.py", root, "--fix")
    run("logiccheck.py", root, "--fix")
    run("dedupe.py", root, "--apply")
    run("clones.py", root, "--apply")
    run("spellcheck.py", root, "--fix", *(["--gate"] if gate_spelling else []))
    run("textcheck.py", root, "--fix")
    run("terms.py", root, "--fix")
    run("links.py", root)
    print("\nDone. To put the last change back:  %s undo   (repeat to go further back)"
          % ("arachnid" if os.environ.get("ARACHNID_IN_DOCKER") else "python arachnid.py"))
    if not with_triage:
        return 0
    return run("triage.py", *(["--webhook", webhook] if webhook else []))


def undo():
    base = HERE / "backups"
    sessions = sorted(p for p in base.iterdir() if p.is_dir()) if base.exists() else []
    def stamp(p):
        ms = [p / m for m in ("manifest.json", "dedupe-manifest.json", "clones-manifest.json", "logic-manifest.json", "text-manifest.json", "terms-manifest.json", "advice-manifest.json") if (p / m).exists()]
        return max((m.stat().st_mtime for m in ms), default=0)
    live = [p for p in sessions if any((p / m).exists() for m in ("manifest.json", "dedupe-manifest.json", "clones-manifest.json", "logic-manifest.json", "text-manifest.json", "terms-manifest.json", "advice-manifest.json"))]
    if not live:
        print("Nothing to undo.")
        return 0
    live.sort(key=lambda p: (stamp(p), p.name))
    last = live[-1]
    tool = ("dedupe.py" if last.name.endswith("-dedupe") else "clones.py" if last.name.endswith("-clones")
            else "logiccheck.py" if last.name.endswith("-logic")
            else "textcheck.py" if last.name.endswith("-text")
            else "terms.py" if last.name.endswith("-terms")
            else "proposals.py" if last.name.endswith("-advice") else "spellcheck.py")
    print("Undoing the most recent change:", last.name)
    return run(tool, "--undo")


def tree_snapshot(base):
    snap = {}
    for dp, dn, fn in os.walk(base):
        for f in fn:
            p = Path(dp) / f
            snap[p.relative_to(base).as_posix()] = p.read_bytes()
    return snap


def selftest():
    """Plant one problem of each kind in a throw-away website, fix them all through the real chain, check, undo, check."""
    import struct
    import zlib
    tmp = Path(tempfile.mkdtemp(prefix="arachnid-e2e-"))
    site = tmp / "site"
    reports = HERE / "reports"
    saved_reports = tmp / "saved-reports"
    if reports.exists():
        shutil.copytree(reports, saved_reports)          # the self-test must not overwrite the owner's real reports
    try:
        def png(path, rgb):
            raw = b"".join(b"\x00" + bytes(rgb) * 40 for _ in range(20))
            def ch(t, d):
                c = struct.pack(">I", len(d)) + t + d
                return c + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", struct.pack(">IIBBBBB", 40, 20, 8, 2, 0, 0, 0))
                             + ch(b"IDAT", zlib.compress(raw)) + ch(b"IEND", b""))
        (site / "pages" / "home").mkdir(parents=True)
        (site / "pages" / "about").mkdir(parents=True)
        (site / "package.json").write_text("{}")
        png(site / "pages" / "home" / "logo.png", (200, 30, 30))
        png(site / "assets" / "logo-copy.png", (200, 30, 30))                       # 1) duplicate file
        (site / "pages" / "home" / "home.html").write_text(
            '<html><head><style>img{width:40px}</style></head><body><h1>Home</h1><img src="logo.png">'
            '<script src="app.js"></script></body></html>')
        (site / "pages" / "about" / "about.html").write_text(
            '<html><head><style>img{width:40px}</style></head><body><h1>About</h1>'
            '<img src="../../assets/logo-copy.png"></body></html>')
        (site / "pages" / "home" / "app.js").write_text(
            "// we are here\nconst a = [1, 2;\ndocument.querySelector('h1').title = 'n' + a.length;\n")  # 2) syntax, 3) typo
        (site / "scripts").mkdir()
        (site / "scripts" / "util.py").write_text(
            "# we recieve the items here\nimport os\nimport sys\n\n\ndef total(items):\n    leftover = 5\n    result = 0\n    for i in items:\n        result += i\n"
            "    return result\n\n\ndef shared(x=[]):\n    return x\n\n\nprint(sys.argv)\n")      # 5) unused import + variable, and an ambiguity bug
        (site / "README.md").write_text("# Site\n\nTheir is a short note about this site. Built with javascript and hosted on github.\n")      # 6) a meaning breaker in a doc
        (site / "extra" / "extra").mkdir(parents=True)
        (site / "extra" / "note.txt").write_text("same\n")
        (site / "extra" / "extra" / "note.txt").write_text("same\n")                  # 4) nested clone
        before = tree_snapshot(site)
        print("Planted: 1 broken script, 1 spelling mistake, 1 unused import + 1 unused variable, 1 meaning breaker, 1 duplicate image, 1 nested duplicate folder.\n")
        root = str(site)
        rc = fix(root, gate_spelling=False, with_triage=False)
        after = tree_snapshot(site)
        problems = []
        js = (site / "pages" / "home" / "app.js").read_text()
        if "[1, 2];" not in js:
            problems.append("the broken script was not repaired")
        if "recieve" in (site / "scripts" / "util.py").read_text():
            problems.append("the spelling mistake was not fixed")
        logos = [k for k in after if k.endswith(".png")]
        if len(logos) != 1:
            problems.append("expected exactly one logo after the merge, found %d" % len(logos))
        util = (site / "scripts" / "util.py").read_text()
        if "import os" in util or "leftover" in util:
            problems.append("the unused import / unused variable were not removed")
        if "x=[]" not in util:
            problems.append("an ambiguity bug (shared list default) must be REPORTED but never changed")
        if "Their is" in (site / "README.md").read_text():
            problems.append("the meaning breaker (their is -> there is) was not fixed")
        if "javascript" in (site / "README.md").read_text() or "github" in (site / "README.md").read_text():
            problems.append("the terminology (javascript -> JavaScript, github -> GitHub) was not fixed")
        if (site / "extra" / "extra").exists():
            problems.append("the nested folder is still there")
        print("\n" + "-" * 78)
        for _ in range(9):
            undo()
        restored = tree_snapshot(site)
        if restored != before:
            problems.append("undo did not restore the project exactly (%d differing file(s))" %
                            len({k for k in set(before) | set(restored) if before.get(k) != restored.get(k)}))
        # ---- alarm test: a broken page and an unrepairable script must raise an ALARM (exit code 2)
        alarm = tmp / "alarm"
        alarm.mkdir()
        (alarm / "index.html").write_text('<html><body><img src="img/gone.png"><script src="x.js"></script></body></html>')
        (alarm / "x.js").write_text("function ( {{{ ;;; = = \n")
        (reports / "ledger.jsonl").unlink() if (reports / "ledger.jsonl").exists() else None
        subprocess.call([PY, str(HERE / "links.py"), str(alarm)], stdout=subprocess.DEVNULL)
        subprocess.call([PY, str(HERE / "syntaxcheck.py"), str(alarm), "--fix", "--preview"], stdout=subprocess.DEVNULL)
        tri = subprocess.run([PY, str(HERE / "triage.py")], capture_output=True, text=True)
        if tri.returncode != 2 or "gone.png" not in tri.stdout or "[ALARM]" not in tri.stdout:
            problems.append("the alarm test failed: a missing file and an unrepairable script should raise an ALARM (exit code 2)")
        # ---- approval test: the advisor proposes, NOTHING changes until a human approves, then the gate decides
        adv = tmp / "advice"
        (adv / "tests").mkdir(parents=True)
        (adv / "m.py").write_text('NAMES = ["a", "b", "c", "d"]\n\n\ndef ok(x):\n    return x in NAMES\n\n\ndef tot(r):\n    return sum([i for i in r])\n')
        (adv / "tests" / "test_m.py").write_text("import sys, os\nsys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))\nimport m\n\n\ndef test_m():\n    assert m.ok('a') and not m.ok('z') and m.tot([1, 2]) == 3\n")
        adv_before = tree_snapshot(adv)
        subprocess.call([PY, str(HERE / "advisor.py"), str(adv)], stdout=subprocess.DEVNULL)
        if tree_snapshot(adv) != adv_before:
            problems.append("the advisor changed files by itself - it must only propose")
        subprocess.call([PY, str(HERE / "proposals.py"), "apply", "--root", str(adv)], stdout=subprocess.DEVNULL)
        if tree_snapshot(adv) != adv_before:
            problems.append("something was applied WITHOUT a human approving it")
        subprocess.call([PY, str(HERE / "proposals.py"), "approve", "all"], stdout=subprocess.DEVNULL)
        subprocess.call([PY, str(HERE / "proposals.py"), "apply", "--root", str(adv)], stdout=subprocess.DEVNULL)
        m_now = (adv / "m.py").read_text()
        if "NAMES = {" not in m_now or "sum((i for i in r))" not in m_now:
            problems.append("approved advice (list -> set, list -> generator) was not applied through the gate")
        undo()
        if tree_snapshot(adv) != adv_before:
            problems.append("undo did not restore the approved advice exactly")
        print("\n" + "=" * 78)
        if problems:
            print("SELF-TEST FAILED:")
            for p in problems:
                print("  -", p)
            return 1
        print("SELF-TEST PASSED: the broken script was repaired, the typo fixed, unused code removed, the duplicate merged,\n"
              "the nested folder folded in, and a critical problem raised an ALARM - each behind the safety gate - and undo restored everything exactly.")
        return 0
    finally:
        try:
            if saved_reports.exists():
                shutil.rmtree(reports, ignore_errors=True)
                shutil.copytree(saved_reports, reports, dirs_exist_ok=True)   # (reports may be a Docker volume that cannot be removed)
            elif reports.exists():
                shutil.rmtree(reports, ignore_errors=True)
                reports.mkdir()
        except OSError:
            pass
        shutil.rmtree(tmp, ignore_errors=True)
        # the self-test must not leave its backups around
        b = HERE / "backups"
        if b.exists():
            for p in b.iterdir():
                if p.is_dir() and any(str(tmp) in (q.read_text(errors="ignore") if q.is_file() else "")
                                      for q in [p / "manifest.json", p / "manifest.undone.json", p / "dedupe-manifest.json",
                                                p / "dedupe-manifest.undone.json", p / "clones-manifest.json",
                                                p / "clones-manifest.undone.json", p / "logic-manifest.json", p / "logic-manifest.undone.json", p / "text-manifest.json", p / "text-manifest.undone.json", p / "terms-manifest.json", p / "terms-manifest.undone.json", p / "advice-manifest.json", p / "advice-manifest.undone.json"] if q.exists()):
                    shutil.rmtree(p, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description="Arachnid - find and safely fix problems in a project.")
    ap.add_argument("command", choices=["scan", "fix", "undo", "selftest", "crawl", "insights", "triage", "advise", "netcheck", "size", "leaks", "proposals", "approve", "reject", "apply-approved"])
    ap.add_argument("root", nargs="*", default=None, help="folder (or proposal ids for approve / reject)")
    ap.add_argument("--build", action="store_true", help="size / leaks: build the project first (in a temporary copy) and test the real output")
    ap.add_argument("--online", action="store_true", help="netcheck: also knock on each outside address")
    ap.add_argument("--webhook", default=None, help="send ALARMS (critical problems) to this Slack/Discord/Teams webhook URL")
    ap.add_argument("--gate-spelling", action="store_true", help="also put spelling fixes through the safety gate")
    a = ap.parse_args()
    args = a.root or []
    if a.command in ("approve", "reject"):
        return run("proposals.py", a.command, *args)
    root = str(Path(args[0] if args else HERE.parent).resolve())
    if a.command == "proposals":
        return run("proposals.py", "list")
    if a.command == "apply-approved":
        return run("proposals.py", "apply", *(["--root", root] if args else []))
    if a.command == "advise":
        return run("advisor.py", root)
    if a.command == "size":
        return run("bundlesize.py", root, *(["--build"] if a.build else []), *(["--online"] if a.online else []))
    if a.command == "leaks":
        return run("leakcheck.py", root, *(["--build"] if a.build else []))
    if a.command == "netcheck":
        return run("netcheck.py", root, *(["--online"] if a.online else []))
    if a.command == "scan":
        return scan(root, a.webhook)
    if a.command == "fix":
        return fix(root, a.gate_spelling, a.webhook)
    if a.command == "triage":
        return run("triage.py", *(["--webhook", a.webhook] if a.webhook else []))
    if a.command == "undo":
        return undo()
    if a.command == "selftest":
        return selftest()
    if a.command == "insights":
        return run("insights.py", root)
    return run("crawler.py", root)


if __name__ == "__main__":
    sys.exit(main())
