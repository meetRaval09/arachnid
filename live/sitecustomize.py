"""
ARACHNID - LIVE TRAIL  (records which file the crawler is reading, right now)
=============================================================================
This file is loaded automatically by Python ONLY when Arachnid is started from the dashboard (or the GitHub
automation) with these two settings:
    PYTHONPATH        = <arachnid>/live            (so Python finds this file)
    ARACHNID_EVENTS   = <a .jsonl file to write to>
    ARACHNID_LIVE_ROOT= <the project folder>

How it works, in plain words
    Python can tell a program every time it opens a file (an "audit hook" - a built-in listening point).
    We listen for that, and when a crawler tool opens a file inside your project, we write ONE short line:
        {"t": 1730800000.12, "tool": "spellcheck", "file": "docs/guide.md"}
    The dashboard reads those lines and moves the spider to that file.

It never changes how the tools work: it does not read your files, does not block anything, writes only file NAMES
(never contents) to the events file, and silently switches itself off if anything goes wrong.
"""
import json
import os
import sys
import threading
import time

_EVENTS = os.environ.get("ARACHNID_EVENTS")
_ROOT = os.environ.get("ARACHNID_LIVE_ROOT")

if _EVENTS and _ROOT:
    try:
        _root = os.path.realpath(_ROOT)
        _root_n = os.path.normcase(_root)
        _sep = os.sep
        _tool = os.path.splitext(os.path.basename(sys.argv[0] if sys.argv and sys.argv[0] else "python"))[0] or "python"
        _seen = set()
        _guard = threading.local()
        _skip_parts = {"node_modules", ".git", "__pycache__"}
        _self = os.environ.get("ARACHNID_SELF")          # Arachnid's own folder (its reports, its word lists) is not "your project"
        _self_n = os.path.normcase(os.path.realpath(_self)) if _self else None
        _state = {"fd": None, "off": False}

        def _write(line):
            if _state["fd"] is None:
                _state["fd"] = os.open(_EVENTS, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
            os.write(_state["fd"], line.encode("utf-8", "replace"))

        def _hook(event, args):
            if event != "open" or _state["off"] or getattr(_guard, "busy", False):
                return
            try:
                path = args[0]
                if not isinstance(path, str):
                    if isinstance(path, bytes):
                        path = path.decode("utf-8", "replace")
                    elif hasattr(path, "__fspath__"):
                        path = os.fspath(path)
                    else:
                        return                     # an already-open file number - nothing to show
                mode = args[1] if len(args) > 1 else "r"
                if isinstance(mode, str) and any(c in mode for c in "wax+"):
                    return                         # only READS are shown (writes are backups / reports)
                full_abs = os.path.abspath(path)
                full = os.path.normcase(full_abs)          # compare without caring about upper/lower case (Windows)...
                if not (full == _root_n or full.startswith(_root_n + _sep)):
                    return
                if _self_n and (full == _self_n or full.startswith(_self_n + _sep)):
                    return
                rel = os.path.relpath(full_abs, _root).replace("\\", "/")   # ...but report the name as it is really written
                if rel in _seen or any(p in _skip_parts for p in rel.split("/")):
                    return
                _seen.add(rel)
                _guard.busy = True
                try:
                    _write('{"t": %.3f, "tool": "%s", "file": %s}\n' % (time.time(), _tool, _json_str(rel)))
                finally:
                    _guard.busy = False
            except Exception:
                _state["off"] = True               # never disturb the real work

        def _json_str(s):
            return json.dumps(s, ensure_ascii=False)

        _guard.busy = True
        try:
            _write('{"t": %.3f, "tool": "%s", "start": true}\n' % (time.time(), _tool))
        finally:
            _guard.busy = False
        sys.addaudithook(_hook)
    except Exception:
        pass
