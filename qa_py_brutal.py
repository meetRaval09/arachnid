#!/usr/bin/env python3
"""
ARACHNID - BRUTAL PYTHON TEST: debugging leftovers
=================================================
    python qa_py_brutal.py

A small shop (payments, a web server, a command-line tool, a worker) with 15 things left behind while someone was
debugging by hand: breakpoint() two ifs deep inside a loop, pdb.set_trace() renamed to stop() on import, ipdb and an
IPython shell, a remote debugger open to the whole network, `except Exception: pass` (one hidden in a tuple, one written
with ...), a login check and a permission check written with assert (both disappear with python -O), logging left at
DEBUG, print(f"{x=}") ... and its CLEAN TWIN: correct code that LOOKS like the leftovers (a pause behind
`if os.environ.get(...)`, a debug shell opened on purpose, our OWN function called breakpoint, an except that explains
itself, asserts in tests, a level chosen from a setting, `f"{a == b}"`).

Passes only if a full scan finds EVERY leftover on its exact line, and finds NOTHING AT ALL on the clean twin.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
LEDGER = HERE / "reports" / "ledger.jsonl"

SHOP = {
 "store/__init__.py": "",
 "store/payments.py": "\"\"\"Payments for the shop.\"\"\"\nimport logging\n\nlog = logging.getLogger(__name__)\n\n\ndef charge(user, amount, cards):\n    assert user.is_authenticated, \"please log in first\"\n    assert user.has_perm(\"shop.charge\"), \"not allowed to charge\"\n    for card in cards:\n        if card.expired:\n            if card.retries > 2:\n                breakpoint()\n            continue\n        return card.charge(amount)\n    return None\n\n\ndef refund(order):\n    try:\n        order.refund()\n    except Exception:\n        pass\n    try:\n        order.notify()\n    except (ValueError, Exception):\n        pass\n    return order\n",
 "store/server.py": "\"\"\"The web server.\"\"\"\nimport logging\nfrom pdb import set_trace as stop\n\nimport debugpy\n\nlogging.basicConfig(level=\"DEBUG\")\ndebugpy.listen((\"0.0.0.0\", 5678))\n\n\ndef handle(order_id, orders):\n    order = orders.get(order_id)\n    print(f\"{order_id=} {order=}\")\n    if order is None:\n        stop()\n    return order\n",
 "store/cli.py": "\"\"\"Command line tool.\"\"\"\nimport logging\n\nimport ipdb\n\n\ndef main(argv):\n    logging.getLogger().setLevel(logging.DEBUG)\n    if len(argv) < 2:\n        ipdb.set_trace()\n    print(\"got here!!\")\n    return argv[1]\n",
 "store/worker.py": "\"\"\"Background worker.\"\"\"\nfrom IPython import embed\nfrom remote_pdb import RemotePdb\n\n\ndef run(jobs):\n    done = 0\n    for job in jobs:\n        try:\n            job.run()\n            done += 1\n        except BaseException:\n            ...\n    if done == 0:\n        embed()\n    RemotePdb(\"0.0.0.0\", 4444).set_trace()\n    return done\n"
}

CLEAN = {
 "store/__init__.py": "",
 "store/payments.py": "\"\"\"Payments for the shop.\"\"\"\nimport logging\nimport os\n\nlog = logging.getLogger(__name__)\n\n\nclass PaymentError(Exception):\n    \"\"\"A payment that cannot be made.\"\"\"\n\n\ndef charge(user, amount, cards):\n    if not user.is_authenticated:\n        raise PermissionError(\"please log in first\")\n    if amount <= 0:\n        raise PaymentError(\"amount must be positive\")\n    assert isinstance(cards, list)\n    for card in cards:\n        if card.expired:\n            if os.environ.get(\"PAY_DEBUG\"):\n                breakpoint()\n            continue\n        return card.charge(amount)\n    return None\n\n\ndef refund(order):\n    try:\n        order.refund()\n    except Exception:\n        log.exception(\"refund failed for %s\", order)\n    try:\n        order.notify()\n    except Exception:  # a failed e-mail must never block the refund\n        pass\n    try:\n        order.archive()\n    except ValueError:\n        pass\n    return order\n\n\nclass Receipt:\n    \"\"\"A receipt file that closes itself.\"\"\"\n\n    def __init__(self, f):\n        self.f = f\n\n    def __del__(self):\n        try:\n            self.f.close()\n        except Exception:\n            pass\n",
 "store/server.py": "\"\"\"The web server.\"\"\"\nimport logging\nimport os\n\nimport debugpy\n\nlevel = logging.DEBUG if os.environ.get(\"VERBOSE\") else logging.INFO\nlogging.basicConfig(level=level)\nlogging.basicConfig(level=logging.DEBUG if os.environ.get(\"V\") else logging.INFO)\nif os.environ.get(\"ATTACH_DEBUGGER\"):\n    debugpy.listen((\"127.0.0.1\", 5678))\n\n\ndef handle(order_id, orders):\n    order = orders.get(order_id)\n    print(f\"Order {order_id}: {order}\")\n    print(f\"{order_id == 0} {order_id:=^10}\")\n    return order\n",
 "store/cli.py": "\"\"\"Command line tool.\"\"\"\nimport pdb\nimport sys\n\n\ndef breakpoint_here(argv):\n    \"\"\"Our own helper - not the built-in pause.\"\"\"\n    return argv[:1]\n\n\ndef debug_shell():\n    \"\"\"Opened on purpose with --shell.\"\"\"\n    pdb.set_trace()\n\n\ndef main(argv):\n    if \"--shell\" in argv:\n        debug_shell()\n    print(\"Starting the shop tool\")\n    print(\"-\" * 20)\n    return breakpoint_here(argv)\n\n\nif __name__ == \"__main__\":\n    sys.exit(main(sys.argv))\n",
 "store/worker.py": "\"\"\"Background worker.\"\"\"\n\n\ndef run(jobs, queue):\n    done = 0\n    for job in jobs:\n        if job.wait:\n            queue.breakpoint(job)\n        done += job.run()\n    return done\n",
 "main.py": "\"\"\"Starts the shop: see README.md.\"\"\"\nimport sys\n\nfrom store import cli, payments, server, worker\n\n\ndef start(argv):\n    receipt = payments.Receipt(open(\"receipt.txt\", \"w\", encoding=\"utf-8\"))\n    server.handle(1, {1: receipt})\n    worker.run([], None)\n    return cli.main(argv)\n\n\nif __name__ == \"__main__\":\n    sys.exit(start(sys.argv))\n",
 "README.md": "# Shop\n\nRun `python main.py`. The code is in `store/`: `cli.py`, `payments.py`, `server.py`, `worker.py`.\n",
 "tests/__init__.py": "",
 "tests/test_payments.py": "from store.payments import charge, refund\n\n\nclass User:\n    is_authenticated = True\n    is_admin = True\n\n\ndef test_charge():\n    user = User()\n    assert user.is_admin\n    assert charge(user, 10, []) is None\n    print(\"here\")\n\n\ndef test_refund():\n    class Order:\n        def refund(self):\n            return 1\n\n        def notify(self):\n            return 1\n\n        def archive(self):\n            return 1\n    assert refund(Order()) is not None\n"
}

TRUTH = [
 ('store/payments.py', 'assert user.is_authenticated', 'a login check written with assert (gone with python -O)'),
 ('store/payments.py', 'assert user.has_perm', 'a permission check written with assert (gone with python -O)'),
 ('store/payments.py', 'breakpoint()', 'a breakpoint() hidden two ifs deep inside a loop'),
 ('store/payments.py', '    except Exception:', 'a failed refund swallowed in silence'),
 ('store/payments.py', 'except (ValueError, Exception):', 'every error swallowed, hidden inside a tuple'),
 ('store/server.py', 'logging.basicConfig(level="DEBUG")', 'the whole server logging at DEBUG level'),
 ('store/server.py', 'debugpy.listen(', 'a remote debugger open to the whole network'),
 ('store/server.py', 'print(f"{order_id=}', 'a print(f"{x=}") debugging line'),
 ('store/server.py', '        stop()', 'pdb.set_trace() renamed to stop() on import'),
 ('store/cli.py', 'logging.getLogger().setLevel(logging.DEBUG)', 'the root logger set to DEBUG'),
 ('store/cli.py', 'ipdb.set_trace()', 'an ipdb pause'),
 ('store/cli.py', 'print("got here!!")', 'a print("got here!!") leftover'),
 ('store/worker.py', 'except BaseException:', 'every error (even Ctrl+C) swallowed with ...'),
 ('store/worker.py', '        embed()', 'an IPython shell that opens when nothing was done'),
 ('store/worker.py', 'RemotePdb(', 'a remote pdb listening on the network'),
]


def write(root, files):
    for rel, text in files.items():
        p = Path(root) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


def scan(root):
    subprocess.run([sys.executable, str(HERE / "arachnid.py"), "scan", str(root)], capture_output=True, text=True, errors="replace", timeout=900)
    ents = []
    for ln in (LEDGER.read_text("utf-8", "replace").splitlines() if LEDGER.exists() else []):
        try:
            ents.append(json.loads(ln))
        except ValueError:
            pass
    return ents


def main():
    tmp = Path(tempfile.mkdtemp(prefix="arachnid-brutal-py-"))
    keep = tmp / "reports-backup"
    if (HERE / "reports").exists():
        shutil.copytree(HERE / "reports", keep)
    try:
        write(tmp / "shop", SHOP)
        write(tmp / "clean", CLEAN)
        ents = scan(tmp / "shop")
        caught = 0
        print("HIDDEN LEFTOVERS (each must be found on its exact line)")
        for f, needle, what in TRUTH:
            line = next(i for i, l in enumerate(SHOP[f].splitlines(), 1) if needle in l)
            hits = [e for e in ents if e["file"].replace(chr(92), "/").endswith(f) and int(e.get("line") or 0) == line]
            caught += bool(hits)
            print("  %-6s %-14s %3d  %s" % ("FOUND" if hits else "MISSED", f, line, what))
        # shorter-code suggestions (slim) are not bug reports: they have their own brutal test (qa_slim_brutal.py)
        noise = [e for e in scan(tmp / "clean") if e.get("tool") != "slim"]
        print("\nCLEAN TWIN (must stay completely quiet)")
        for e in noise:
            print("  FALSE ALARM  [%s] %s:%s %s" % (e["tool"], e["file"], e.get("line"), e["what"][:100]))
        print("  %d finding(s)" % len(noise))
        print("\nBRUTAL PYTHON SCORE: %d of %d hidden leftovers found, %d false alarm(s) on the clean twin" % (caught, len(TRUTH), len(noise)))
        return 0 if caught == len(TRUTH) and not noise else 1
    finally:
        if keep.exists():
            shutil.rmtree(HERE / "reports", ignore_errors=True)
            shutil.copytree(keep, HERE / "reports", dirs_exist_ok=True)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
