#!/usr/bin/env python3
"""
ARACHNID - CLAUDE CONNECTOR TEST
================================
    python qa_mcp.py

Starts the connector (arachnid_mcp.py) exactly the way Claude does - as a separate program spoken to over
stdin/stdout - and checks the whole conversation: the handshake, the tool list, a scan of a small project with
planted bugs, paging through findings, showing code, proposals, and a bad request.  Also checks that the
connector's channel carries ONLY protocol messages (one stray print would break Claude's connection) and that a
scan changes nothing in the project.
"""
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
OK, BAD = [], []


def check(name, cond, detail=""):
    (OK if cond else BAD).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (("   " + detail) if detail and not cond else ""))


def fingerprint(root):
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(root)).encode() + p.read_bytes())
    return h.hexdigest()


class Client:
    def __init__(self):
        self.p = subprocess.Popen([sys.executable, str(HERE / "arachnid_mcp.py")], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
        self.n = 0
        self.raw = []

    def send(self, method, params=None, notify=False):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        if not notify:
            self.n += 1
            msg["id"] = self.n
        self.p.stdin.write(json.dumps(msg) + "\n")
        self.p.stdin.flush()
        if notify:
            return None
        line = self.p.stdout.readline()
        self.raw.append(line)
        return json.loads(line)

    def call(self, name, args):
        r = self.send("tools/call", {"name": name, "arguments": args})
        res = r.get("result", {})
        return res.get("content", [{}])[0].get("text", ""), res.get("isError")

    def close(self):
        self.p.stdin.close()
        rest = self.p.stdout.read()
        self.p.wait(timeout=30)
        return rest


def main():
    tmp = Path(tempfile.mkdtemp(prefix="arachnid-mcp-"))
    proj = tmp / "shop"
    (proj / "js").mkdir(parents=True)
    (proj / "index.html").write_text('<!doctype html>\n<html lang="en">\n<head>\n  <meta charset="utf-8">\n  <title>Shop</title>\n'
                                     '</head>\n<body>\n  <p id="out">Hi</p>\n  <script src="js/app.js"></script>\n</body>\n</html>\n',
                                     encoding="utf-8")
    (proj / "js" / "app.js").write_text('function show(n) {\n  document.getElementById("#out").textContent = n;\n  debugger;\n}\n'
                                        'show(1);\n', encoding="utf-8")
    before = fingerprint(proj)
    c = Client()
    try:
        print("Handshake")
        r = c.send("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "qa", "version": "1"}})
        check("initialize answers with the same protocol version", r.get("result", {}).get("protocolVersion") == "2025-06-18", str(r))
        check("server says it offers tools", "tools" in r.get("result", {}).get("capabilities", {}))
        c.send("notifications/initialized", notify=True)
        r = c.send("ping")
        check("ping", r.get("result") == {})

        print("Tools")
        tools = {t["name"]: t for t in c.send("tools/list")["result"]["tools"]}
        want = {"scan", "findings", "code_context", "slim", "proposals", "approve", "reject", "apply_approved", "fix", "undo"}
        check("all 10 tools are listed", want <= set(tools), str(sorted(tools)))
        check("every tool has a description and an input schema", all(t.get("description") and t["inputSchema"]["type"] == "object" for t in tools.values()))
        check("look-only tools are marked read-only", all(tools[n]["annotations"]["readOnlyHint"] for n in ("scan", "findings", "code_context", "slim", "proposals")))
        check("changing tools are marked destructive", all(tools[n]["annotations"].get("destructiveHint") for n in ("fix", "apply_approved", "undo")))
        check("changing tools say: only when the person asked", all("person has clearly asked" in tools[n]["description"] for n in ("approve", "fix", "apply_approved", "undo")))

        print("Scan (this takes a moment)")
        text, err = c.call("scan", {"folder": str(proj)})
        check("scan succeeds", not err, text[:300])
        check("scan reports the planted bugs", "getElementById" in text and "debugger" in text, text[:800])
        check("scan changed nothing in the project", fingerprint(proj) == before)

        print("Findings and code")
        text, err = c.call("findings", {"pile": "all", "file_contains": "app.js", "limit": 50})
        check("findings can be filtered by file", not err and "js/app.js" in text, text[:500])
        text, err = c.call("findings", {"pile": "nonsense"})
        check("a wrong pile name is a clear error, not a crash", err and "pile must be" in text)
        text, err = c.call("code_context", {"file": "js/app.js", "line": 2, "radius": 2})
        check("code_context shows the line, marked", not err and ">    2 |" in text and "getElementById" in text, text)
        text, err = c.call("code_context", {"file": "../../etc/passwd", "line": 1})
        check("code_context refuses files outside the project", err)

        print("Proposals and safety")
        text, err = c.call("proposals", {"status": "all"})
        check("proposals answers", not err)
        text, err = c.call("approve", {"ids": []})
        check("approve without ids is refused", err)
        text, err = c.call("scan", {"folder": str(HERE)})
        check("scanning Arachnid's own folder is refused", err and "own folder" in text)
        r = c.send("tools/call", {"name": "no_such_tool", "arguments": {}})
        check("an unknown tool gets a protocol error", "error" in r)
        r = c.send("no/such/method")
        check("an unknown method gets 'method not found'", r.get("error", {}).get("code") == -32601)
        check("project still unchanged after everything", fingerprint(proj) == before)
    finally:
        rest = c.close()
        shutil.rmtree(tmp, ignore_errors=True)
    every = [ln for ln in c.raw + rest.splitlines() if ln.strip()]
    clean = True
    for ln in every:
        try:
            clean = clean and json.loads(ln).get("jsonrpc") == "2.0"
        except ValueError:
            clean = False
    check("the channel carried ONLY protocol messages (no stray printing)", clean)
    print("\nCONNECTOR TEST: %d passed, %d failed" % (len(OK), len(BAD)))
    return 1 if BAD else 0


if __name__ == "__main__":
    sys.exit(main())
