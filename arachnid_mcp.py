#!/usr/bin/env python3
"""
ARACHNID - CLAUDE CONNECTOR (MCP server)
========================================
Lets Claude (Claude Desktop, Claude Code, or any app that speaks MCP) use Arachnid through a set of tools:

    scan            look at a project and sort what was found (nothing is changed)
    findings        page through the results: alarms, decisions, handled, ideas
    code_context    show the lines around one finding
    slim            find proven-identical shorter code (proposals only)
    proposals       list what waits for a person's yes
    approve/reject  record the person's answer for specific proposals
    apply_approved  apply the approved proposals - each through the safety gate
    fix             apply what is safe - every code or file change through the safety gate
    undo            put back the most recent fix

Nothing in here weakens Arachnid's rules: the safety gate decides every change, and `approve`, `apply_approved`,
`fix` and `undo` are only meant to run when the person asks for them in the conversation.

Plain Python, no packages to install.  It talks MCP over stdin/stdout (one JSON message per line).

Claude Desktop - add this to claude_desktop_config.json (Settings > Developer > Edit Config), then restart Claude:
    {"mcpServers": {"arachnid": {"command": "python", "args": ["C:\\\\path\\\\to\\\\arachnid\\\\arachnid_mcp.py"]}}}
Claude Code:
    claude mcp add arachnid -- python C:\\path\\to\\arachnid\\arachnid_mcp.py
"""
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
STATE = HERE / "reports" / "mcp-state.json"
VERSION = "1.0.0"
PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
PILES = {"alarm": "ALARM - critical, the product is probably broken right now",
         "attention": "needs a decision from a person",
         "handled": "Arachnid fixed it or can fix it safely by itself",
         "ideas": "optional suggestions"}


# ---------------------------------------------------------------- helpers
def _state():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(**kw):
    s = _state()
    s.update(kw)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, indent=2), encoding="utf-8")


def _folder(arg):
    """The project folder: the one given, or the last one scanned."""
    f = (arg or "").strip() or _state().get("root", "")
    if not f:
        raise ValueError("Give the project folder (for example C:\\Users\\me\\projects\\shop).")
    p = Path(os.path.expanduser(f)).resolve()
    if not p.is_dir():
        raise ValueError("Folder not found: %s" % p)
    if p == HERE or HERE in p.parents:
        raise ValueError("That is Arachnid's own folder - give the project you want checked.")
    return p


def _run(*args, timeout=3600):
    """Run `arachnid.py <args>` in its own process (its printing must never reach the MCP channel)."""
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    r = subprocess.run([sys.executable, str(HERE / "arachnid.py"), *args], cwd=str(HERE), capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=timeout, env=env)
    return r.returncode, (r.stdout or "") + (("\n" + r.stderr) if r.stderr.strip() else "")


def _tail(text, n=60):
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return "\n".join(lines[-n:])


def _piles():
    import importlib
    import triage
    importlib.reload(triage)
    piles, total = triage.build()
    return piles, total


def _item(e, i):
    loc = e.get("file", "") + (":%s" % e["line"] if e.get("line") else "")
    out = {"n": i, "where": loc, "tool": e.get("tool"), "kind": e.get("kind"), "what": e.get("what", "")}
    if e.get("why"):
        out["why"] = e["why"]
    if e.get("todo"):
        out["what_to_do"] = e["todo"]
    return out


def _summary(piles, total):
    lines = ["%d findings in total:" % total]
    for k in ("alarm", "attention", "handled", "ideas"):
        lines.append("  %-9s %4d   (%s)" % (k, len(piles.get(k, [])), PILES[k]))
    return "\n".join(lines)


# ---------------------------------------------------------------- tools
def t_scan(a):
    root = _folder(a.get("folder"))
    code, out = _run("scan", str(root))
    _save_state(root=str(root))
    piles, total = _piles()
    show = []
    for k in ("alarm", "attention"):
        for i, e in enumerate(piles.get(k, [])[:10], 1):
            show.append("[%s %d] %s" % (k, i, json.dumps(_item(e, i), ensure_ascii=False)))
    return ("Scanned %s - nothing was changed.\n\n%s\n\n%s\n\nUse `findings` to see more (pile, offset)."
            % (root, _summary(piles, total), "\n".join(show) or "No alarms and nothing that needs a decision."))


def t_findings(a):
    piles, total = _piles()
    pile = (a.get("pile") or "attention").lower()
    if pile not in PILES and pile != "all":
        raise ValueError("pile must be one of: alarm, attention, handled, ideas, all")
    items = []
    for k in (PILES if pile == "all" else [pile]):
        items += [(k, e) for e in piles.get(k, [])]
    needle = (a.get("file_contains") or "").lower()
    if needle:
        items = [(k, e) for k, e in items if needle in e.get("file", "").lower()]
    tool = (a.get("tool") or "").lower()
    if tool:
        items = [(k, e) for k, e in items if (e.get("tool") or "").lower() == tool]
    off, lim = max(0, int(a.get("offset", 0))), max(1, min(100, int(a.get("limit", 25))))
    page = items[off:off + lim]
    body = [json.dumps(dict(_item(e, off + i + 1), pile=k), ensure_ascii=False) for i, (k, e) in enumerate(page)]
    return "%s\n\nShowing %d-%d of %d in '%s'%s:\n%s" % (
        _summary(piles, total), off + 1 if page else 0, off + len(page), len(items), pile,
        " (file contains %r)" % needle if needle else "", "\n".join(body) or "(none)")


def t_code_context(a):
    root = _folder(a.get("folder"))
    rel = (a.get("file") or "").replace("\\", "/").lstrip("/")
    p = (root / rel).resolve()
    if root not in p.parents or not p.is_file():
        raise ValueError("File not found inside the project: %s" % rel)
    line, radius = int(a.get("line", 1)), max(1, min(40, int(a.get("radius", 8))))
    text = p.read_text(encoding="utf-8", errors="replace").split("\n")
    lo, hi = max(1, line - radius), min(len(text), line + radius)
    return "%s (lines %d-%d)\n%s" % (rel, lo, hi, "\n".join(
        "%s%5d | %s" % (">" if n == line else " ", n, text[n - 1]) for n in range(lo, hi + 1)))


def t_slim(a):
    root = _folder(a.get("folder"))
    code, out = _run("slim", str(root))
    _save_state(root=str(root))
    return "Shorter-code search on %s finished - nothing was changed. The results are proposals:\n\n%s\n\n%s" % (
        root, _tail(out, 40), t_proposals({"status": "pending"}))


def t_proposals(a):
    import proposals as P
    data = P.load()
    status = (a.get("status") or "pending").lower()
    rows = [p for p in data.values() if status == "all" or p.get("status") == status]
    if not rows:
        return "No proposals." if status == "all" else "No %s proposals." % status
    out = ["%d %s proposal%s:" % (len(rows), status, "" if len(rows) == 1 else "s")]
    for p in rows[:int(a.get("limit", 40))]:
        out.append(json.dumps({"id": p.get("id"), "status": p.get("status"), "where": "%s:%s" % (p.get("file"), p.get("line")),
                               "title": p.get("title"), "benefit": p.get("benefit"), "risk": p.get("risk"),
                               "kind": "patch" if p.get("patches") else "manual",
                               "check": p.get("verify", "safety gate")}, ensure_ascii=False))
    return "\n".join(out)


def _ids(a):
    ids = a.get("ids") or []
    if isinstance(ids, str):
        ids = ids.replace(",", " ").split()
    ids = [i.strip() for i in ids if i.strip()]
    if not ids:
        raise ValueError("Give the proposal ids (for example [\"P-1a2b3c\"]).")
    return ids


def t_approve(a):
    code, out = _run("approve", *_ids(a))
    return _tail(out, 30) + "\n\nApproved only - nothing changed yet. `apply_approved` applies them through the safety gate."


def t_reject(a):
    code, out = _run("reject", *_ids(a))
    return _tail(out, 30)


def t_apply_approved(a):
    root = _folder(a.get("folder"))
    code, out = _run("apply-approved", str(root))
    return "Applied through the safety gate (whatever it could not prove was held back). Undo: `undo`.\n\n" + _tail(out, 60)


def t_fix(a):
    root = _folder(a.get("folder"))
    code, out = _run("fix", str(root))
    return "Safe fixes done on %s - every code or file change passed the safety gate, and a backup was made first. Undo: `undo`.\n\n%s" % (
        root, _tail(out, 60))


def t_undo(a):
    code, out = _run("undo")
    return _tail(out, 30)


ASK = " Only call this when the person has clearly asked for it in this conversation."
TOOLS = [
    ("scan", "Scan a project folder with Arachnid and sort what it finds into alarms (critical), decisions (needs a person), "
             "handled (safe fixes) and ideas. Looks only - changes nothing. Can take a few minutes on big projects.",
     {"folder": ("string", "Full path of the project folder to scan.")}, ["folder"], {"readOnlyHint": True}),
    ("findings", "Page through the results of the last scan. pile = alarm | attention | handled | ideas | all.",
     {"pile": ("string", "Which pile (default: attention)."), "file_contains": ("string", "Only findings whose file path contains this text."),
      "tool": ("string", "Only findings from this checker (e.g. bugs, security, spelling, pages)."),
      "offset": ("integer", "Skip this many (for paging)."), "limit": ("integer", "How many to show (max 100, default 25).")},
     [], {"readOnlyHint": True}),
    ("code_context", "Show the lines around a finding, so it can be explained or checked.",
     {"file": ("string", "File path as shown in the finding."), "line": ("integer", "Line number."),
      "radius": ("integer", "Lines to show before and after (default 8)."), "folder": ("string", "Project folder (default: the last one scanned).")},
     ["file", "line"], {"readOnlyHint": True}),
    ("slim", "Look for shorter versions of code that are PROVEN to be the same program. Results are proposals - changes nothing.",
     {"folder": ("string", "Project folder (default: the last one scanned).")}, [], {"readOnlyHint": True}),
    ("proposals", "List proposals waiting for a person's yes (status: pending | approved | rejected | applied | all).",
     {"status": ("string", "Default: pending."), "limit": ("integer", "Default 40.")}, [], {"readOnlyHint": True}),
    ("approve", "Record the person's YES for specific proposals. Does not change code by itself." + ASK,
     {"ids": ("array", "Proposal ids, e.g. [\"P-1a2b3c\"].")}, ["ids"], {"readOnlyHint": False, "destructiveHint": False}),
    ("reject", "Record the person's NO for specific proposals (they are not proposed again)." + ASK,
     {"ids": ("array", "Proposal ids.")}, ["ids"], {"readOnlyHint": False, "destructiveHint": False}),
    ("apply_approved", "Apply the approved proposals to the project - each one through the safety gate (anything it cannot prove "
                       "identical is held back). Changes files; a backup is made first and `undo` puts it back." + ASK,
     {"folder": ("string", "Project folder (default: the last one scanned).")}, [], {"readOnlyHint": False, "destructiveHint": True}),
    ("fix", "Apply Arachnid's safe fixes to the project (syntax, unused code, duplicates, spelling ...). Every code or file change "
            "passes the safety gate; a backup is made first and `undo` puts it back." + ASK,
     {"folder": ("string", "Project folder (default: the last one scanned).")}, [], {"readOnlyHint": False, "destructiveHint": True}),
    ("undo", "Put back the most recent fix, byte for byte (call again to go further back)." + ASK,
     {}, [], {"readOnlyHint": False, "destructiveHint": True}),
]
HANDLERS = {"scan": t_scan, "findings": t_findings, "code_context": t_code_context, "slim": t_slim, "proposals": t_proposals,
            "approve": t_approve, "reject": t_reject, "apply_approved": t_apply_approved, "fix": t_fix, "undo": t_undo}


def tool_list():
    out = []
    for name, desc, props, req, ann in TOOLS:
        schema = {"type": "object", "properties": {}, "required": req}
        for k, (typ, d) in props.items():
            schema["properties"][k] = {"type": typ, "description": d}
            if typ == "array":
                schema["properties"][k]["items"] = {"type": "string"}
        out.append({"name": name, "description": desc, "inputSchema": schema, "annotations": dict(ann, title="Arachnid: " + name)})
    return out


# ---------------------------------------------------------------- MCP over stdio
def handle(msg):
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:
        return None                                     # a notification (initialized, cancelled ...) - nothing to answer
    if method == "initialize":
        asked = (msg.get("params") or {}).get("protocolVersion")
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "arachnid", "title": "Arachnid", "version": VERSION},
            "instructions": "Arachnid finds bugs and fixes only what it can prove leaves the product unchanged. Start with `scan`. "
                            "Never call approve, reject, apply_approved, fix or undo unless the person asked for it."}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": tool_list()}}
    if method == "tools/call":
        p = msg.get("params") or {}
        fn = HANDLERS.get(p.get("name"))
        if not fn:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": "Unknown tool: %s" % p.get("name")}}
        try:
            text, err = fn(p.get("arguments") or {}), False
        except Exception as e:                           # a tool problem is reported to Claude, not a crash
            text, err = "%s: %s" % (type(e).__name__, e), True
        return {"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": text}], "isError": err}}
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "Method not found: %s" % method}}


def main():
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    real_out, sys.stdout = sys.stdout, sys.stderr      # anything a module prints goes to the log, never the channel
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            reply = handle(msg) if isinstance(msg, dict) else None
        if reply is not None:
            real_out.write(json.dumps(reply, ensure_ascii=False) + "\n")
            real_out.flush()


if __name__ == "__main__":
    main()
