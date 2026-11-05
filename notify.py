#!/usr/bin/env python3
"""
ARACHNID - E-MAIL SENDER  (each person / team gets only what concerns them)
===========================================================================
    python notify.py email [folder] [--dry-run]      send the latest report by e-mail
    python notify.py preview [folder]                show who would get what - sends nothing

How it decides who gets which e-mail
  * It reads the standard report (reports/arachnid-report.json - made by report.py / ci_summary.py).
  * For every ALARM and every DECISION it already knows the receivers (routing.py): the owners from CODEOWNERS,
    the person who changed the code, and any "always" receivers from arachnid.config.json.
  * Handles like @firm/payments-team are turned into addresses with the "teams" / "people" lists in
    arachnid.config.json - normally the team's GROUP address (payments@firm.com), so the firm's own mail system
    delivers it to everyone in that team.
  * Every receiver gets ONE e-mail containing only their findings, alarms first.
  * Subject lines always look the same, so mail rules can sort them into folders, even in one shared inbox:
        [Arachnid] [ALARM] [payments-team] api.py:5 — a key/token is written inside an address

The mail server login is NEVER stored by Arachnid.  It is read at run time from these settings
(on GitHub: Settings -> Secrets and variables -> Actions):
    ARACHNID_SMTP_HOST   e.g. smtp.office365.com / smtp.gmail.com   (or "mail": {"host": ...} in arachnid.config.json)
    ARACHNID_SMTP_PORT   587 (STARTTLS, default) or 465 (SSL)
    ARACHNID_SMTP_USER   the mailbox Arachnid sends from
    ARACHNID_SMTP_PASSWORD   its password / app password  (secret)
    ARACHNID_MAIL_FROM   shown as sender (default: the SMTP user)
Without a mail server it does a dry run: the e-mails are written to reports/outbox/*.eml so you can open them.
"""
import argparse
import os
import re
import smtplib
import ssl
import sys
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import report  # noqa: E402
import routing  # noqa: E402

OUTBOX = HERE / "reports" / "outbox"
MAX_MAILS = 40            # safety: never flood a firm's mail system from one run


def groups(rep, router):
    """{(address, team): (label, [findings])} - who gets which findings.  Normally one e-mail per address; in
    'one inbox for everyone' mode one e-mail per team, all to that inbox."""
    out = {}
    inbox = router.one_inbox()
    for f in rep["findings"]:
        if f["tier"] not in ("alarm", "attention"):
            continue
        handles = f.get("notify") or []
        author_email = None
        if f.get("author") is None and "author" in router.send.get(f["tier"], []):
            author_email = routing.author_of(router.root, f["file"], f["line"], router.cfg)[1]
        if inbox:                     # one shared inbox: one e-mail per TEAM (people are already covered by their team)
            teams_only = [h for h in handles if "/" in h]
            handles = teams_only or handles[:1]
        for h in handles:
            for addr in router.emails_for([h]):
                lab, lst = out.setdefault((addr, h if inbox else ""), (h, []))
                if f not in lst:
                    lst.append(f)
        if not inbox:
            for addr in router.emails_for([], author_email):
                lab, lst = out.setdefault((addr, ""), ("", []))
                if f not in lst:
                    lst.append(f)
    for addr, (lab, lst) in out.items():
        lst.sort(key=lambda f: (["alarm", "attention"].index(f["tier"]), f["file"], f["line"]))
    return out


def build_message(rep, addr, label, items, sender):
    m = EmailMessage()
    m["Subject"] = report.subject(rep, items, label)
    m["From"] = sender
    m["To"] = addr
    m["Date"] = formatdate(localtime=True)
    m["Message-ID"] = make_msgid(domain=(sender.rsplit("@", 1)[-1] if "@" in sender else "arachnid.local"))
    m["X-Arachnid-Project"] = rep["project"]
    m["X-Arachnid-Level"] = "alarm" if any(f["tier"] == "alarm" for f in items) else "decision"
    intro = "You receive this because you own or recently changed these files%s." % ((" (" + label + ")") if label else "")
    m.set_content(report.text(rep, items, intro))
    m.add_alternative(report.html(rep, items, intro), subtype="html")
    return m


def settings(router):
    mail = router.cfg.get("mail") or {}
    host = os.environ.get("ARACHNID_SMTP_HOST") or mail.get("host")
    port = int(os.environ.get("ARACHNID_SMTP_PORT") or mail.get("port") or 587)
    user = os.environ.get("ARACHNID_SMTP_USER") or ""
    pwd = os.environ.get("ARACHNID_SMTP_PASSWORD") or ""
    sender = os.environ.get("ARACHNID_MAIL_FROM") or mail.get("from") or user or "arachnid@localhost"
    tls = (os.environ.get("ARACHNID_SMTP_TLS") or mail.get("tls") or ("ssl" if port == 465 else "starttls")).lower()
    return host, port, user, pwd, sender, tls


def send_all(msgs, host, port, user, pwd, tls):
    ctx = ssl.create_default_context()
    if tls == "ssl":
        s = smtplib.SMTP_SSL(host, port, timeout=30, context=ctx)
    else:
        s = smtplib.SMTP(host, port, timeout=30)
        if tls == "starttls":
            s.starttls(context=ctx)
        elif tls != "none":
            raise ValueError("unknown ARACHNID_SMTP_TLS value: %s" % tls)
    sent, failed = 0, []
    try:
        if user:
            s.login(user, pwd)
        for m in msgs:
            try:
                s.send_message(m)
                sent += 1
            except smtplib.SMTPException as e:
                failed.append((m["To"], str(e)[:120]))
    finally:
        try:
            s.quit()
        except Exception:
            pass
    return sent, failed


def main():
    ap = argparse.ArgumentParser(description="Arachnid e-mail sender")
    ap.add_argument("action", choices=["email", "preview"])
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--already", default=None, help="file with the previous GitHub comment/issue: findings listed there were already announced")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    router = routing.Router(a.root)
    try:
        rep = report.load()
        if report.REPORT.stat().st_mtime < (HERE / "reports" / "ledger.jsonl").stat().st_mtime:
            raise ValueError("the report is older than the latest results")
    except (OSError, ValueError):
        rep = report.build(a.root)            # always e-mail the LATEST results
        report.save(rep)
    if any(f.get("in_change") is not None for f in rep["findings"]):     # pull request: only what this change touches
        rep = dict(rep, findings=[f for f in rep["findings"] if f.get("in_change")])
    if a.already and Path(a.already).is_file():
        told = report.ids_from_text(Path(a.already).read_text(encoding="utf-8", errors="replace"))
        before = len(rep["findings"])
        rep = dict(rep, findings=[f for f in rep["findings"] if f["id"] not in told])
        if told:
            print("Already announced earlier, not e-mailed again: %d finding(s)." % (before - len(rep["findings"])))
    g = groups(rep, router)
    if not g:
        print("Nobody needs an e-mail: no alarms or decisions with a known receiver."
              + ("" if router.cfg.get("teams") or router.cfg.get("people") else "  (Add \"teams\"/\"people\" addresses to arachnid.config.json to turn e-mails on.)"))
        return 0
    host, port, user, pwd, sender, tls = settings(router)
    msgs = [build_message(rep, key[0], lab, items, sender) for key, (lab, items) in sorted(g.items())][:MAX_MAILS]
    print("E-mails to send: %d" % len(msgs))
    for m in msgs:
        print("  to %-32s %s" % (m["To"], m["Subject"]))
    if a.action == "preview":
        return 0
    if a.dry_run or not host:
        OUTBOX.mkdir(parents=True, exist_ok=True)
        for old in OUTBOX.glob("*.eml"):
            old.unlink()
        for i, m in enumerate(msgs, 1):
            name = "%02d-%s.eml" % (i, re.sub(r"[^A-Za-z0-9._-]+", "_", m["To"]))
            (OUTBOX / name).write_bytes(bytes(m))
        print(("No mail server set (ARACHNID_SMTP_HOST) - " if not host else "Dry run - ") + "the e-mails were saved to %s instead of being sent." % OUTBOX)
        return 0
    try:
        sent, failed = send_all(msgs, host, port, user, pwd, tls)
    except (OSError, smtplib.SMTPException, ValueError) as e:
        print("Could not send the e-mails: %s" % str(e)[:200])
        return 1
    print("Sent %d e-mail(s)." % sent)
    for to, why in failed:
        print("  could not send to %s: %s" % (to, why))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
