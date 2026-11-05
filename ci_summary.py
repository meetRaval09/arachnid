#!/usr/bin/env python3
"""
ARACHNID - GITHUB SUMMARY
=========================
    python ci_summary.py [--root FOLDER] [--mode scan|fix|issue] [--out FILE]

Writes the Markdown version of the standard report (see report.py) for the pull-request comment, the fix pull
request or the alarm issue.  It also saves reports/arachnid-report.json, which the e-mail sender reads.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import report  # noqa: E402

MARK = report.MARK


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--mode", choices=["scan", "fix", "issue"], default="scan")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    rep = report.build(a.root)
    report.save(rep)
    body = report.markdown(rep, a.mode)
    import os
    if os.environ.get("GITHUB_OUTPUT"):            # tells the workflow how many alarms should turn the check red
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
            fh.write("blocking=%d\n" % rep["blocking"])
    if a.out:
        Path(a.out).write_text(body + "\n", encoding="utf-8")
    else:
        try:
            sys.stdout.reconfigure(errors="replace")
        except Exception:
            pass
        print(body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
