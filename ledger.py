#!/usr/bin/env python3
"""
ARACHNID LEDGER - one shared notebook where every tool writes down what it found and what happened to it.
=========================================================================================================
triage.py reads this notebook and sorts everything into: ALARM / NEEDS YOU / HANDLED / IDEAS.

Each entry:
  tool      who wrote it (syntax, logic, spelling, dedupe, clones, links, insights)
  status    found    detected, nobody has tried to fix it
            fixable  the crawler CAN fix it by itself (nothing changed yet)
            fixed    the crawler fixed it (a backup exists)
            held     the crawler could fix it but the safety gate/policy held it back -> the owner decides
            decision it can only be solved by a human choice
            idea     a suggestion (not an error)
  severity  critical  the product is probably broken right now -> ALARM
            warning   looks like a real bug or risk, not an emergency
            info      housekeeping
A tool never lets a ledger problem break its real work: every function here swallows its own errors.
"""
import json
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PATH = HERE / "reports" / "ledger.jsonl"


def reset(tool):
    """Forget this tool's earlier entries (it is about to write fresh ones)."""
    try:
        if not PATH.exists():
            return
        keep = [ln for ln in PATH.read_text(encoding="utf-8").splitlines() if ln.strip() and json.loads(ln).get("tool") != tool]
        PATH.write_text("\n".join(keep) + ("\n" if keep else ""), encoding="utf-8")
    except Exception:
        pass


def record(tool, status, severity, file, line, what, why="", todo="", kind=""):
    try:
        PATH.parent.mkdir(parents=True, exist_ok=True)
        entry = {"tool": tool, "status": status, "severity": severity, "file": file, "line": line or 0,
                 "what": str(what)[:300], "why": str(why)[:300], "todo": str(todo)[:300], "kind": kind, "at": int(time.time())}
        with open(PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def read():
    try:
        return [json.loads(ln) for ln in PATH.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except Exception:
        return []


def clear():
    try:
        PATH.unlink()
    except Exception:
        pass
