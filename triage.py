#!/usr/bin/env python3
"""
ARACHNID TRIAGE - sorts everything the tools found into four piles
==================================================================
    python triage.py [--webhook URL] [--all] [--quiet]

  [ALARM]      CRITICAL - the product is probably broken right now and the crawler cannot repair it by itself
               (a file that is badly broken, a page that points to a missing file ...).
               An alarm is: shown loudly, saved to reports/alarms.json, sent to a webhook if you give one
               (--webhook URL or the ARACHNID_WEBHOOK setting - Slack/Discord/Teams style, only file names, line
               numbers and one-line descriptions are sent, never code or secrets), and the program exits with
               code 2 so an automatic pipeline (GitHub Action) turns RED.
  [ATTENTION]  needs a human decision, but it is not an emergency (the crawler held a change back because it
               might alter behaviour; a likely bug; a choice only the owner can make). Nothing was touched.
  [HANDLED]    the crawler fixed it, or can fix it by itself. Reported only - no action needed.
  [IDEAS]      suggestions (memory, dead code, lighter libraries).
"""
import argparse
import json
import os
import sys
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ledger  # noqa: E402

SEV = {"critical": 0, "warning": 1, "info": 2}
KIND_WORDS = {
    "syntax": "syntax errors", "logic": "unused code / ambiguity", "spelling": "spelling mistakes", "dedupe": "duplicate files",
    "clones": "nested folders", "links": "missing files", "text": "meaning breakers", "terms": "terminology", "net": "external connections", "bundle": "page weight", "leak": "memory growth (measured)", "advisor": "efficiency ideas awaiting approval", "insights": "suggestions", "money": "money safety", "bugs": "hidden bugs", "security": "security", "vulns": "known security holes", "pages": "page health (real browser)",
}


def tier(e):
    if e["severity"] == "critical":
        return "alarm"
    if e["status"] in ("fixed", "fixable"):
        return "handled"
    if e["status"] == "idea":
        return "ideas"
    return "attention"


def loc(e):
    return e["file"] + (":%d" % e["line"] if e.get("line") else "")


def build():
    entries = ledger.read()
    piles = defaultdict(list)
    for e in entries:
        piles[tier(e)].append(e)
    for k in piles:
        piles[k].sort(key=lambda e: (SEV.get(e["severity"], 3), e["tool"], e["file"], e.get("line", 0)))
    return piles, len(entries)


def send_webhook(url, alarms, root_hint):
    lines = ["Arachnid ALARM - %d critical problem%s need attention%s" % (len(alarms), "" if len(alarms) == 1 else "s", root_hint)]
    for e in alarms[:10]:
        lines.append("- %s: %s" % (loc(e), e["what"][:160]))
    if len(alarms) > 10:
        lines.append("... and %d more (see reports/alarms.json)" % (len(alarms) - 10))
    payload = json.dumps({"text": "\n".join(lines), "content": "\n".join(lines)}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=15).read()
        return True, ""
    except Exception as ex:
        return False, str(ex)[:120]


def report(piles, total, show_all=False, quiet=False, webhook=None):
    A, T, H, I = piles["alarm"], piles["attention"], piles["handled"], piles["ideas"]
    print("\n" + "=" * 78)
    print("  ARACHNID TRIAGE  -  %d things recorded" % total)
    print("=" * 78)
    if not total:
        print("Nothing recorded yet. Run:  python arachnid.py scan")
    if A:
        print("\n[ALARM]  %d CRITICAL problem%s - the product is probably broken right now:" % (len(A), "" if len(A) == 1 else "s"))
        for i, e in enumerate(A, 1):
            print("  %d. %s" % (i, loc(e)))
            print("       what    : %s" % e["what"])
            if e.get("why"):
                print("       why     : %s" % e["why"])
            if e.get("todo"):
                print("       what to do: %s" % e["todo"])
    else:
        print("\n[ALARM]  none - nothing critical was found.")
    if T:
        print("\n[ATTENTION]  %d item%s need a decision from you (nothing was touched):" % (len(T), "" if len(T) == 1 else "s"))
        lim = len(T) if show_all else 10
        for e in T[:lim]:
            print("   - %s  [%s]  %s" % (loc(e), e["tool"], e["what"][:150]))
            if e.get("todo") and e["severity"] == "warning":
                print("        -> %s" % e["todo"])
        if len(T) > lim:
            print("   ... and %d more (python triage.py --all)" % (len(T) - lim))
    if H and not quiet:
        c = Counter((e["tool"], e["status"]) for e in H)
        print("\n[HANDLED]  %d item%s the crawler fixed or can fix by itself (just reporting, no action needed):" % (len(H), "" if len(H) == 1 else "s"))
        for (tool, status), n in sorted(c.items()):
            print("   - %-18s %3d   %s" % (KIND_WORDS.get(tool, tool), n, "fixed" if status == "fixed" else "ready to fix: python arachnid.py fix"))
    if I and not quiet:
        c = Counter(e.get("kind") or "other" for e in I)
        print("\n[IDEAS]  %d suggestion%s (optional):  %s" % (len(I), "" if len(I) == 1 else "s", ", ".join("%s %d" % (k, v) for k, v in c.most_common(5))))
        print("          details: python arachnid.py insights")
    print("\n" + "-" * 78)
    if A:
        sys.stdout.write("\a")
        print("RESULT: %d ALARM%s - please look at the [ALARM] section first." % (len(A), "" if len(A) == 1 else "S"))
    else:
        print("RESULT: no alarms.%s" % ("  %d item%s wait for your decision." % (len(T), "" if len(T) == 1 else "s") if T else ""))
    if A:
        out = HERE / "reports" / "alarms.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(A, indent=1, ensure_ascii=False), encoding="utf-8")
        print("Alarm list saved to: %s" % out)
        url = webhook or os.environ.get("ARACHNID_WEBHOOK")
        if url:
            ok, err = send_webhook(url, A, "")
            print("Alarm sent to your webhook." if ok else "Could not send the alarm to the webhook: %s" % err)
        else:
            print("(To get alarms in Slack/Discord/Teams, add --webhook URL or set ARACHNID_WEBHOOK.)")
    else:
        try:
            (HERE / "reports" / "alarms.json").unlink()
        except OSError:
            pass
    return 2 if A else 0


def main():
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Arachnid triage - alarms vs things the crawler handles")
    ap.add_argument("--webhook", default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--quiet", action="store_true", help="only alarms and items that need you")
    a = ap.parse_args()
    piles, total = build()
    sys.exit(report(piles, total, a.all, a.quiet, a.webhook))


if __name__ == "__main__":
    main()
