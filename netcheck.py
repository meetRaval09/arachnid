#!/usr/bin/env python3
"""
ARACHNID - Step 11: NETCHECK  (every connection your project makes to the outside world)
=========================================================================================
    python netcheck.py [folder]            read the code: map every outside connection and find the risky ones
    python netcheck.py [folder] --online   ALSO knock on each outside address (needs internet; read-only requests)

An "external connection" is any time your project talks to something that is not your own files: a script loaded from a
CDN, an API call, a web socket, a download in a build file, a tracking pixel ...  Each one is a place where things can
break (the other side disappears), leak (a secret travels in the address) or be attacked (someone swaps what you load).

It builds a MAP of every outside host (what kind, which files, how many places) and then checks:
  SECRETS      passwords / API keys / tokens written inside an address          -> critical (ALARM)
  TLS OFF      code that switches off certificate checking                        -> critical (ALARM)
  BLOCKED      http:// scripts or stylesheets (browsers refuse them on secure pages) -> critical (ALARM)
  SUPPLY CHAIN scripts/styles from a CDN with no integrity hash, or with no fixed version
  UNSAFE       http:// or ws:// for other things, bare IP addresses, localhost left in the code, "allow everyone" CORS
  FRAGILE      network calls with no timeout (a stalled server freezes you) or no error handling, very fast polling,
               downloads piped straight into a shell in build files, unpinned dependencies
  TRACKERS     analytics / ads: they add weight and share visitor data (information)
  --online     dead addresses (404/DNS), broken or expiring certificates, slow answers, redirects

It never changes a file.  Fixable things become PROPOSALS (proposals.py): you approve, the safety gate proves the
product behaves the same, and only then is anything edited.  Secrets are never printed in full.
"""
import argparse
import ast
import json
import re
import socket
import ssl
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import insights  # noqa: E402
import proposals  # noqa: E402
import deps  # noqa: E402
from advisor import abs_offset, starts_of  # noqa: E402

CODE_EXT = set(insights.CODE_EXT) | {"csproj", "html", "htm", "css", "scss", "less", "json", "yml", "yaml", "toml", "xml", "cfg", "ini", "conf", "env", "gradle", "sql", "svelte", "vue"}
SKIP_NAMES = ("package-lock.json", "yarn.lock")
CDN_HOSTS = {"cdn.jsdelivr.net", "cdnjs.cloudflare.com", "unpkg.com", "ajax.googleapis.com", "code.jquery.com", "stackpath.bootstrapcdn.com",
             "maxcdn.bootstrapcdn.com", "cdn.tailwindcss.com", "esm.sh", "cdn.skypack.dev", "fonts.googleapis.com", "fonts.gstatic.com",
             "kit.fontawesome.com", "use.fontawesome.com", "cdn.plot.ly", "d3js.org", "ajax.aspnetcdn.com", "polyfill.io", "cdn.polyfill.io"}
NO_SRI = {"fonts.googleapis.com", "fonts.gstatic.com", "kit.fontawesome.com", "use.fontawesome.com", "cdn.tailwindcss.com", "polyfill.io", "cdn.polyfill.io"}
TRACKERS = ("google-analytics.com", "googletagmanager.com", "connect.facebook.net", "facebook.com/tr", "hotjar.com", "clarity.ms", "mixpanel.com",
            "cdn.segment.com", "doubleclick.net", "analytics.tiktok.com", "snap.licdn.com", "googlesyndication.com", "amplitude.com", "fullstory.com")
IGNORE_HOSTS = ("www.w3.org", "w3.org", "schema.org", "purl.org", "xmlns.com", "json-schema.org", "example.com", "example.org", "www.example.com",
                "localhost", "127.0.0.1", "0.0.0.0", "github.com", "raw.githubusercontent.com", "creativecommons.org", "opensource.org", "apache.org",
                "www.apache.org", "gnu.org", "www.gnu.org", "mozilla.org", "developer.mozilla.org", "stackoverflow.com", "npmjs.com", "www.npmjs.com", "docs.python.org")
URL_RX = re.compile(r"""(?:https?|wss?)://[^\s'"`<>()\[\]{}\\,;|^]+""")
TEMPLATED = re.compile(r"\$\{|\{\{|%s|%d|<\?|\{[a-zA-Z_]+\}|\+\s*$")
SECRET_Q = re.compile(r"""[?&](api[_-]?key|apikey|key|token|access[_-]?token|auth|secret|password|passwd|pwd|client[_-]?secret)=([^&\s'"`<>]{8,})""", re.I)
CRED_URL = re.compile(r"""[a-z][a-z0-9+.\-]*://[^/\s:@'"`]+:([^/\s@'"`]{3,})@[^/\s'"`]+""", re.I)
PLACEHOLDER = re.compile(r"your|example|xxx|\*{3}|<|>|\$|%|\{|changeme|placeholder|dummy|test|sample|token_here|\.\.\.", re.I)
TLS_OFF = [
    (r"\bverify\s*=\s*False\b", "Python `verify=False`"), (r"rejectUnauthorized\s*:\s*false", "Node `rejectUnauthorized: false`"),
    (r"NODE_TLS_REJECT_UNAUTHORIZED['\"]?\s*[=:]\s*['\"]?0", "`NODE_TLS_REJECT_UNAUTHORIZED=0`"), (r"strictSSL\s*:\s*false", "`strictSSL: false`"),
    (r"_create_unverified_context|ssl\.CERT_NONE|check_hostname\s*=\s*False", "Python SSL checks disabled"), (r"InsecureSkipVerify\s*:\s*true", "Go `InsecureSkipVerify: true`"),
    (r"ALLOW_ALL_HOSTNAME_VERIFIER|TrustAllCerts|trustAllCerts|X509TrustManager\(\)\s*\{\s*\}", "Java trust-everything certificate code"),
    (r"\bcurl\b[^\n|;]*\s(?:-k|--insecure)\b", "`curl -k / --insecure`"), (r"\bwget\b[^\n|;]*--no-check-certificate", "`wget --no-check-certificate`"),
    (r"CURLOPT_SSL_VERIFYPEER['\"]?\s*,?\s*(?:false|0)\b", "PHP cURL peer verification off"),
]
LOCAL_OK = re.compile(r"(^|/)(tests?|spec|specs|__tests__|mocks?|fixtures?|docs?|examples?|samples?|e2e|cypress)(/|$)|\.(test|spec|stories)\.|\.md$|\.dev\.|\.local\.|\.example$|\.sample$|docker-compose|README|vite\.config|webpack\.config|setupProxy|\.env", re.I)
HTTP_PY = re.compile(r"^(?:requests|httpx)\.(get|post|put|patch|delete|head|options|request)$")


def finding(sev, kind, rel, line, what, why, todo, extra=None):
    d = {"severity": sev, "kind": kind, "file": rel, "line": line, "what": what, "why": why, "todo": todo}
    d.update(extra or {})
    return d


def mask(s):
    return s[:2] + "*" * max(3, min(8, len(s) - 2))


def host_of(u):
    try:
        return (urlparse(u).hostname or "").lower()
    except ValueError:
        return ""


def classify(u, host, tag=None):
    if any(t in u for t in TRACKERS):
        return "tracker"
    if host in CDN_HOSTS:
        return "cdn"
    if u.startswith(("ws://", "wss://")):
        return "websocket"
    if tag in ("script", "link", "img", "source", "iframe", "video", "audio"):
        return "asset"
    return "api/other"


# ---------------------------------------------------------------------------------------------------------------
def is_unpinned(u, host):
    if re.search(r"@latest|/latest/", u):
        return True
    path = urlparse(u).path
    if host == "cdn.jsdelivr.net" and path.startswith("/npm/"):
        seg = path[5:].split("/")
    elif host == "unpkg.com":
        seg = path[1:].split("/")
    else:
        return False
    first = "/".join(seg[:2]) if seg and seg[0].startswith("@") else (seg[0] if seg else "")
    return bool(first) and not re.search(r"@[\dvx~^]", first.lstrip("@"))


def scan_html(rel, text, fs, conns):
    for m in re.finditer(r"<(script|link|img|iframe|source|video|audio|embed|object)\b([^>]*)>", text, re.I | re.S):
        tag, attrs = m.group(1).lower(), m.group(2)
        am = re.search(r"""\b(?:src|href|data)\s*=\s*(["'])(.*?)\1""", attrs, re.S | re.I)
        if not am:
            continue
        u = am.group(2).strip()
        if u.startswith("//"):
            u = "https:" + u
        if not re.match(r"(?:https?|wss?)://", u, re.I):
            continue
        host = host_of(u)
        if host in ("localhost", "127.0.0.1", "0.0.0.0"):
            continue
        if tag == "link" and not re.search(r"rel\s*=\s*[\"']?(?:stylesheet|preload|modulepreload|icon|prefetch)", attrs, re.I):
            continue
        line = insights.line_of(text, m.start())
        conns.append((u, host, classify(u, host, tag), rel, line, tag))
        blocking = tag in ("script", "link") or tag == "iframe"
        if u.lower().startswith("http://"):
            fs.append(finding("critical" if blocking else "warning", "blocked-http", rel, line,
                              "<%s> loads %s over plain http://" % (tag, u[:90]),
                              "browsers REFUSE to load plain-http %s on a secure (https) page, and anyone on the network can alter it - so it is probably broken for real visitors" % ("scripts and styles" if blocking else "content"),
                              "use the https:// address of the same file", {"url": u}))
        if tag in ("script", "link") and host not in NO_SRI and host and not any(t in u for t in TRACKERS):
            has_int = re.search(r"\bintegrity\s*=", attrs, re.I)
            if not has_int:
                fs.append(finding("warning", "no-integrity", rel, line, "<%s> from %s has no integrity hash" % (tag, host),
                                  "if that CDN is hacked or the file changes, every visitor runs/receives the changed file", "add integrity=\"sha384-...\" crossorigin=\"anonymous\" (the hash comes from the CDN or `openssl dgst -sha384 -binary file | openssl base64 -A`; run with --online to get the finished tag)", {"url": u, "tag": m.group(0)}))
            elif not re.search(r"\bcrossorigin\b", attrs, re.I):
                fs.append(finding("warning", "integrity-needs-crossorigin", rel, line, "<%s> has an integrity hash but no crossorigin attribute" % tag,
                                  "for files from another site, browsers silently skip or fail the hash check without crossorigin=\"anonymous\"", "add crossorigin=\"anonymous\"", {"url": u, "tag": m.group(0)}))
        if host in CDN_HOSTS and host not in NO_SRI and is_unpinned(u, host):
            fs.append(finding("warning", "unpinned-cdn", rel, line, "%s has no fixed version" % u[:90],
                              "the file at that address can change tomorrow and change or break your page without you touching anything", "put an exact version in the address, e.g. .../pkg@1.2.3/... (run with --online to get the finished tag)", {"url": u, "tag": m.group(0)}))


def py_calls(rel, text, fs):
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return
    starts = starts_of(text)

    def dotted(n):
        parts = []
        while isinstance(n, ast.Attribute):
            parts.append(n.attr)
            n = n.value
        if isinstance(n, ast.Name):
            parts.append(n.id)
            return ".".join(reversed(parts))
        return ""
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        name = dotted(n.func)
        is_req = bool(re.match(r"^requests\.(get|post|put|patch|delete|head|options|request)$", name))
        is_url = name in ("urllib.request.urlopen", "urlopen", "request.urlopen")
        if not (is_req or is_url):
            continue
        has_kw = {k.arg for k in n.keywords}
        if "timeout" in has_kw or None in has_kw or any(isinstance(a, ast.Starred) for a in n.args):
            continue
        if is_url and len(n.args) >= 3:
            continue
        last = None
        for node in list(n.args) + [k.value for k in n.keywords]:
            if last is None or (node.end_lineno, node.end_col_offset) > (last.end_lineno, last.end_col_offset):
                last = node
        patches = []
        if last is not None:
            a = abs_offset(text, starts, last.lineno, last.col_offset)
            b = abs_offset(text, starts, last.end_lineno, last.end_col_offset)
            src = text[a:b]
            patches = [{"at": a, "old": src, "new": src + ", timeout=30"}]
        fs.append(finding("warning", "no-timeout", rel, n.lineno, "`%s(...)` has no timeout" % name,
                          "if the other server stops answering, your program waits forever (it freezes, and so does anything waiting for it)",
                          "add a timeout, e.g. timeout=30 (seconds)", {"patches": patches, "func": name}))


JS_NET = re.compile(r"\b(fetch|axios(?:\.(?:get|post|put|delete|patch|request))?|\$\.(?:ajax|get|post)|new XMLHttpRequest|new WebSocket|got\.(?:get|post)|superagent\.\w+)\s*\(")


def js_calls(rel, text, fs):
    code = insights.code_only(text)
    for m in JS_NET.finditer(code):
        before = code[max(0, m.start() - 700): m.start()]
        after = code[m.end(): m.end() + 700]
        if m.group(1) in ("new WebSocket", "new XMLHttpRequest"):
            continue
        in_try = len(re.findall(r"\btry\s*\{", before)) > len(re.findall(r"\bcatch\b|\bfinally\b", before))
        handled = in_try or re.search(r"\.catch\s*\(", after) or re.search(r"\.then\s*\([^()]*,\s*(?:function|\(|\w+\s*=>)", after) or re.search(r"\bon(?:error|fail)\b|\berror\s*:", after[:300])
        if not handled:
            fs.append(finding("warning", "no-error-handling", rel, insights.line_of(text, m.start()), "`%s(...)` has no visible error handling" % m.group(1),
                              "when the network drops or the server errors, the user may see a blank screen or the app may crash with an unhandled error",
                              "wrap in try/catch (or add .catch) and show a friendly message / retry", {}))
    for m in re.finditer(r"setInterval\s*\(([\s\S]{0,600}?)[,\s]+(\d+)\s*\)", code):
        if int(m.group(2)) < 5000 and JS_NET.search(m.group(1)):
            fs.append(finding("info", "fast-polling", rel, insights.line_of(text, m.start()), "a network call repeats every %s ms" % m.group(2),
                              "constant polling drains battery and data, and hammers the server (and any API limits) as visitors grow",
                              "poll less often, pause when the tab is hidden, or push updates (WebSocket / Server-Sent Events)", {}))


def generic(rel, text, fs, conns, is_html):
    local_ok = bool(LOCAL_OK.search(rel))
    for rx, label in TLS_OFF:
        for m in re.finditer(rx, text):
            fs.append(finding("critical", "tls-off", rel, insights.line_of(text, m.start()), "certificate checking is switched off (%s)" % label,
                              "anyone on the network path can read or change the traffic, and your program cannot tell it is talking to an impostor",
                              "remove it and fix the real certificate problem instead", {}))
    for m in CRED_URL.finditer(text):
        if PLACEHOLDER.search(m.group(1)):
            continue
        fs.append(finding("critical", "secret-in-url", rel, insights.line_of(text, m.start()), "a password is written inside an address (%s)" % re.sub(r":[^/\s@:]{3,}@", ":" + mask(m.group(1)) + "@", m.group(0))[:80],
                          "addresses end up in logs, browser history and Git history - this password must be treated as leaked", "rotate (change) the password, then read it from an environment variable", {}))
    for m in SECRET_Q.finditer(text):
        v = m.group(2)
        if PLACEHOLDER.search(v) or re.match(r"^[A-Za-z_.]+$", v) and not re.search(r"\d", v):
            continue
        public = v.startswith("AIza")
        fs.append(finding("warning" if public else "critical", "secret-in-url", rel, insights.line_of(text, m.start()),
                          "a %s is written inside an address (%s=%s)" % ("browser key" if public else "key/token", m.group(1), mask(v)),
                          "browser keys are visible to every visitor anyway - they must be restricted by website in the provider's console" if public else
                          "addresses end up in logs, browser history and Git history - treat this key as leaked",
                          "restrict this key to your website's address in the provider's console" if public else "rotate (change) the key, then keep it on a server / in an environment variable and call the service from there", {}))
    for m in re.finditer(r"""Access-Control-Allow-Origin['"]?\s*[:,=]\s*['"]?\*|allow_origins\s*=\s*\[\s*['"]\*|\borigin\s*:\s*['"]\*['"]|\bcors\(\s*\)""", text):
        if not local_ok:
            fs.append(finding("warning", "cors-any", rel, insights.line_of(text, m.start()), "the server accepts requests from ANY website (CORS *)",
                              "any other site a visitor opens can call this API from their browser; harmful if the API uses cookies or private data", "list your own site(s) instead of *", {}))
    for m in re.finditer(r"\b(?:curl|wget)\b[^\n|]*\|\s*(?:sudo\s+)?(?:ba)?sh\b|ADD\s+https?://", text):
        fs.append(finding("warning", "pipe-to-shell", rel, insights.line_of(text, m.start()), "a build/setup step downloads something and runs it unchecked",
                          "if that address is hacked or changes, your build runs the attacker's commands", "download a pinned version and verify its checksum (sha256) before running", {}))
    for m in URL_RX.finditer(text):
        u = m.group(0).rstrip(".:!?)'\"")
        if TEMPLATED.search(text[m.start(): m.end() + 2]) or "$" in u:
            continue
        host = host_of(u)
        if not host:
            continue
        line = insights.line_of(text, m.start())
        low = u.lower()
        if re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", host) and host not in ("127.0.0.1", "0.0.0.0") and not local_ok:
            private = host.startswith(("10.", "192.168.", "169.254.")) or re.match(r"172\.(1[6-9]|2\d|3[01])\.", host)
            fs.append(finding("warning", "bare-ip", rel, line, "address uses a bare IP number: %s" % u[:70],
                              "only works on your own network" if private else "breaks if the server ever moves, and a bare IP cannot have a normal https certificate",
                              "use a domain name (and https)", {"url": u}))
        elif host in ("localhost", "127.0.0.1", "0.0.0.0") and not local_ok and not is_html:
            fs.append(finding("warning", "localhost-left", rel, line, "address points at your own computer: %s" % u[:70],
                              "works on your machine only - for real visitors it fails or (worse) calls their own computer", "read the address from configuration (a base URL setting)", {"url": u}))
        elif low.startswith(("http://", "ws://")) and host not in IGNORE_HOSTS and not local_ok and not is_html and not deps.is_manifest(rel) and not host.endswith((".local", ".internal")):
            fs.append(finding("warning", "insecure-scheme", rel, line, "%s uses an unencrypted address: %s" % ("web socket" if low.startswith("ws") else "request", u[:70]),
                              "traffic can be read and changed by anyone on the path; modern browsers also block it from secure pages",
                              "use %s" % ("wss://" if low.startswith("ws") else "https://"), {"url": u}))
        if host in IGNORE_HOSTS or local_ok and not is_html:
            continue
        if not is_html:
            conns.append((u, host, classify(u, host), rel, line, "code"))


def packages(rel, text, fs):
    name = rel.split("/")[-1]
    if name == "package.json":
        try:
            data = json.loads(text)
        except Exception:
            return
        for sect in ("dependencies", "devDependencies"):
            for dep, ver in (data.get(sect) or {}).items():
                v = str(ver)
                ln = next((i for i, l in enumerate(text.split("\n"), 1) if '"%s"' % dep in l), 1)
                if v in ("*", "latest", "x") or v == "":
                    fs.append(finding("warning", "unpinned-dep", rel, ln, "`%s` is set to \"%s\"" % (dep, v), "every install can pull a different (maybe broken or hacked) version", "use a ^version range and commit the lock file", {}))
                elif v.startswith(("http://", "git://", "git+http://")):
                    fs.append(finding("warning", "insecure-dep", rel, ln, "`%s` is downloaded over an unencrypted address" % dep, "the package can be swapped in transit", "use https:// or the registry", {}))
    elif name == "requirements.txt":
        lines = [l.strip() for l in text.split("\n") if l.strip() and not l.strip().startswith("#")]
        pk = [l for l in lines if not l.startswith("-")]
        unp = [l for l in pk if not re.search(r"==|~=|@|>=", l)]
        if unp:
            fs.append(finding("info", "unpinned-dep", rel, 1, "%d of %d packages have no version (%s ...)" % (len(unp), len(pk), ", ".join(unp[:3])),
                              "each install may pull a different release, so the same code can behave differently tomorrow", "pin with == (pip freeze shows the current versions)", {}))
        for l in lines:
            if re.match(r"(?:--index-url|-i|--extra-index-url|-e\s+git\+)\s*http://", l):
                fs.append(finding("warning", "insecure-dep", rel, 1, "packages are fetched over an unencrypted address: %s" % l[:60], "packages can be swapped in transit", "use https://", {}))


# ---------------------------------------------------------------------------------------------------------------
def probe(u, timeout=8):
    """Read-only look at one address. Returns dict(status, ms, final, error, tls_error)."""
    import urllib.request
    import urllib.error
    t0 = time.time()
    res = {"url": u, "status": None, "ms": None, "final": u, "error": None, "tls_error": False}
    if u.startswith(("ws://", "wss://")):
        u = "http" + u[2:]
    ctx = ssl.create_default_context()
    for method in ("HEAD", "GET"):
        req = urllib.request.Request(u, method=method, headers={"User-Agent": "Arachnid-netcheck/1.0 (link check)", "Range": "bytes=0-0"} if method == "GET" else {"User-Agent": "Arachnid-netcheck/1.0 (link check)"})
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
                res.update(status=r.status, final=r.geturl(), ms=int((time.time() - t0) * 1000))
                return res
        except urllib.error.HTTPError as e:
            res.update(status=e.code, final=e.geturl() or u, ms=int((time.time() - t0) * 1000))
            if e.code in (403, 405, 501) and method == "HEAD":
                continue
            return res
        except urllib.error.URLError as e:
            reason = e.reason
            if isinstance(reason, ssl.SSLError):
                res.update(error="certificate problem: %s" % str(reason)[:80], tls_error=True)
            else:
                res.update(error=str(reason)[:80])
            return res
        except Exception as e:
            res.update(error=str(e)[:80])
            return res
    return res


def cert_days(host, port=443, timeout=6):
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=timeout) as s:
            with ctx.wrap_socket(s, server_hostname=host) as ss:
                exp = ss.getpeercert()["notAfter"]
        return (datetime.fromtimestamp(ssl.cert_time_to_seconds(exp), timezone.utc) - datetime.now(timezone.utc)).days
    except Exception:
        return None


def _get(u, timeout=10, head=False, limit=6_000_000):
    import urllib.request
    req = urllib.request.Request(u, method="HEAD" if head else "GET", headers={"User-Agent": "Arachnid-netcheck/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.geturl() if head else r.read(limit)


def pinned_url(u):
    """Find the exact-version address of an unpinned CDN file (read-only lookups)."""
    p = urlparse(u)
    try:
        if p.hostname == "cdn.jsdelivr.net" and p.path.startswith("/npm/"):
            segs = p.path[5:].split("/")
            pkg = "/".join(segs[:2]) if segs[0].startswith("@") else segs[0]
            rest = segs[2:] if segs[0].startswith("@") else segs[1:]
            pkg = re.sub(r"@latest$", "", pkg)
            info = json.loads(_get("https://data.jsdelivr.com/v1/packages/npm/%s/resolved?specifier=latest" % pkg))
            return "https://cdn.jsdelivr.net/npm/%s@%s/%s" % (pkg, info["version"], "/".join(rest))
        if p.hostname == "unpkg.com":
            return _get(u, head=True)                  # unpkg redirects to the exact version
    except Exception:
        return None
    return None


def paste_ready(fs):
    """For CDN tags that need an integrity hash / a fixed version: build the finished tag, ready to paste."""
    import base64
    import hashlib
    groups = {}
    for f in fs:
        if f["kind"] in ("no-integrity", "unpinned-cdn", "integrity-needs-crossorigin") and f.get("tag"):
            groups.setdefault((f["file"], f["tag"]), []).append(f)
    for (rel, tag), items in list(groups.items())[:30]:
        new, url = tag, items[0]["url"]
        if any(i["kind"] == "unpinned-cdn" for i in items):
            pu = pinned_url(url)
            if pu:
                new, url = new.replace(url.replace("https:", "") if url.startswith("https:") and ('"//' in tag) else url, pu), pu
        if "integrity=" not in new:
            try:
                digest = base64.b64encode(hashlib.sha384(_get(url)).digest()).decode()
                new = re.sub(r"\s*/?>$", lambda m: ' integrity="sha384-%s"%s' % (digest, m.group(0)), new, count=1)
            except Exception:
                pass
        if "crossorigin" not in new:
            new = re.sub(r"\s*/?>$", lambda m: ' crossorigin="anonymous"' + m.group(0), new, count=1)
        if new != tag:
            for i in items:
                i["paste"] = new


def online_checks(conns, fs):
    urls = {}
    for u, host, kind, rel, line, tag in conns:
        if kind == "api/other" and tag == "code" and not u.lower().startswith(("http://", "https://")):
            continue
        urls.setdefault(u, []).append((rel, line, tag, kind))
    items = sorted(urls)[:60]
    print("\nONLINE CHECK - knocking (read-only) on %d address%s ..." % (len(items), "" if len(items) == 1 else "es"))
    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(probe, items))
    hosts = sorted({host_of(u) for u in items if u.startswith("https://")})
    with ThreadPoolExecutor(8) as ex:
        days = dict(zip(hosts, ex.map(cert_days, hosts)))
    rows = []
    for r in results:
        refs = urls[r["url"]]
        rel, line, tag, kind = refs[0]
        resource = tag in ("script", "link", "img", "source", "iframe", "video", "audio")
        st = r["status"]
        if r["tls_error"]:
            fs.append(finding("critical", "bad-certificate", rel, line, "%s has a certificate problem (%s)" % (r["url"][:80], r["error"]), "browsers show a full-page warning and block it", "fix the address or ask the owner to renew the certificate", {"url": r["url"]}))
        elif r["error"]:
            fs.append(finding("critical" if resource else "warning", "unreachable", rel, line, "%s cannot be reached (%s)" % (r["url"][:80], r["error"]),
                              "your project loads this and it does not answer - %s" % ("part of the page is missing for visitors" if resource else "the feature that calls it fails"), "fix or replace the address", {"url": r["url"]}))
        elif st in (404, 410):
            fs.append(finding("critical" if resource else "warning", "dead-link", rel, line, "%s answers %d (gone)" % (r["url"][:80], st),
                              "your project depends on something that no longer exists", "replace it with a working address / a copy you host yourself", {"url": r["url"]}))
        elif st and st >= 500:
            fs.append(finding("warning", "server-error", rel, line, "%s answers %d (server error)" % (r["url"][:80], st), "the other side is failing right now (maybe temporary)", "check again later; add a fallback", {"url": r["url"]}))
        elif r["ms"] and r["ms"] > 3000:
            fs.append(finding("info", "slow", rel, line, "%s took %.1f s to answer" % (r["url"][:80], r["ms"] / 1000.0), "slow outside services slow your own page/app", "self-host the file, or load it late / asynchronously", {"url": r["url"]}))
        if st and st < 400 and r["final"] and r["final"] != r["url"] and urlparse(r["final"]).hostname != urlparse(r["url"]).hostname:
            fs.append(finding("info", "moved", rel, line, "%s now redirects to another host: %s" % (r["url"][:60], r["final"][:70]), "the owner moved it; the old address may stop working", "update to the new address", {"url": r["url"]}))
        rows.append((r["url"], st if st else r["error"], r["ms"]))
    for h, dleft in days.items():
        if dleft is not None and dleft < 14:
            fs.append(finding("critical" if dleft < 3 else "warning", "cert-expiring", "(outside)", 0, "the certificate of %s expires in %d day%s" % (h, max(dleft, 0), "" if dleft == 1 else "s"),
                              "when it expires, browsers block %s and everything of yours that loads from it" % h, "ask the owner to renew, or stop depending on it", {"url": h}))
    return rows


# ---------------------------------------------------------------------------------------------------------------
def analyse(root, online=False):
    proj = insights.Project(root)
    fs, conns = [], []
    for rel, text in proj.texts.items():
        ext = insights.ext_of(Path(rel))
        name = rel.split("/")[-1]
        if name in SKIP_NAMES or "node_modules" in rel or (ext not in CODE_EXT and name not in ("Dockerfile", "Makefile", "requirements.txt", "go.mod", "Gemfile", "Pipfile")):
            continue
        is_html = ext in ("html", "htm", "svelte", "vue")
        if is_html:
            scan_html(rel, text, fs, conns)
        generic(rel, text, fs, conns, is_html)
        if ext == "py":
            py_calls(rel, text, fs)
        elif ext in ("js", "jsx", "mjs", "cjs", "ts", "tsx", "vue", "svelte") and not LOCAL_OK.search(rel):
            js_calls(rel, text, fs)
        if name in ("package.json", "requirements.txt"):
            packages(rel, text, fs)
        elif deps.is_manifest(rel):
            for d in deps.parse(rel, text):
                pr = deps.pin_problems(d)
                if pr:
                    fs.append(finding(pr[0], pr[1], rel, d["line"], pr[2], pr[3], pr[4], {}))
    seen_tr = set()
    for u, host, kind, rel, line, tag in conns:
        if kind == "tracker" and host not in seen_tr:
            seen_tr.add(host)
            fs.append(finding("info", "tracker", rel, line, "analytics / ad service: %s" % host, "adds weight and loading time, and shares visitor data with a third party (privacy notices / consent rules may apply)",
                              "keep only what you really use; load it after the page is ready", {"url": u}))
    rows = online_checks(conns, fs) if online else []
    if online:
        paste_ready(fs)
    seen, uniq = set(), []
    for f in fs:
        k = (f["kind"], f["file"], f["line"], f["what"])
        if k not in seen:
            seen.add(k)
            uniq.append(f)
    order = {"critical": 0, "warning": 1, "info": 2}
    uniq.sort(key=lambda f: (order[f["severity"]], f["file"], f["line"]))
    return proj, uniq, conns, rows


def build_map(conns):
    m = defaultdict(lambda: {"kind": "", "files": set(), "uses": 0, "secure": True})
    for u, host, kind, rel, line, tag in conns:
        e = m[host]
        e["kind"] = e["kind"] or kind
        e["files"].add(rel)
        e["uses"] += 1
        if u.lower().startswith(("http://", "ws://")):
            e["secure"] = False
    return m


def main():
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Arachnid netcheck - every outside connection, mapped and checked (read-only)")
    ap.add_argument("root", nargs="?", default=str(HERE.parent))
    ap.add_argument("--online", action="store_true", help="also knock (read-only) on each outside address: dead links, certificates, speed")
    ap.add_argument("--out", default=str(HERE / "reports" / "net-report.json"))
    a = ap.parse_args()
    proj, fs, conns, rows = analyse(a.root, a.online)
    print("Arachnid netcheck - looking at: %s" % proj.root)
    m = build_map(conns)
    print("\nMAP OF OUTSIDE CONNECTIONS (%d host%s)" % (len(m), "" if len(m) == 1 else "s"))
    if not m:
        print("   none found in code, pages or styles.")
    for host, e in sorted(m.items(), key=lambda kv: (-kv[1]["uses"], kv[0]))[:25]:
        print("   %-34s %-10s %s  used %dx in %d file%s" % (host[:34], e["kind"], "https " if e["secure"] else "HTTP! ", e["uses"], len(e["files"]), "" if len(e["files"]) == 1 else "s"))
    if not a.online:
        print("   (add --online to check that each one still answers and its certificate is healthy)")
    labels = {"critical": "CRITICAL - fix these first", "warning": "NEEDS ATTENTION", "info": "GOOD TO KNOW"}
    for sev in ("critical", "warning", "info"):
        items = [f for f in fs if f["severity"] == sev]
        if not items:
            continue
        print("\n%s (%d)" % (labels[sev], len(items)))
        for f in items[:15]:
            print("  %s:%s  %s" % (f["file"], f["line"], f["what"]))
            print("      why it matters: %s" % f["why"])
            print("      what to do:     %s" % f["todo"])
            if f.get("paste"):
                print("      paste-ready:    %s" % f["paste"])
        if len(items) > 15:
            print("  ... and %d more (all in %s)" % (len(items) - 15, a.out))
    if not fs:
        print("\nNo problems found in the outside connections.")
    # proposals for the ones with an exact edit
    found = []
    for f in fs:
        if f.get("patches"):
            found.append({"id": proposals.make_id("net", f["kind"], f["file"], f["what"]), "kind": f["kind"], "file": f["file"], "line": f["line"],
                          "title": f["what"], "benefit": f["why"], "risk": "a slow reply (over 30 s) now raises an error instead of waiting forever - make sure the code around it handles that",
                          "confidence": "high", "patches": f["patches"]})
    cur = proposals.sync("net", found, proj.root)
    if found:
        print("\n%d fix%s ready as a proposal (an exact edit, applied only after you approve and the safety gate agrees):" % (len(found), "" if len(found) == 1 else "es"))
        for p in found[:8]:
            print("   [%s] %s:%s  %s" % (p["id"], p["file"], p["line"], p["title"]))
        print("   python arachnid.py approve <id>   then   python arachnid.py apply-approved")
    try:
        import ledger
        ledger.reset("net")
        pid = {(p["file"], p["title"]): i for i, p in cur.items() if p.get("tool") == "net"}
        for f in fs:
            tag = ""
            status = "found"
            if f.get("patches"):
                i = pid.get((f["file"], f["what"]))
                if i and cur[i]["status"] in ("rejected", "applied"):
                    continue
                status = "decision"
                tag = "[%s] " % i if i else ""
            ledger.record("net", status, f["severity"], f["file"], f["line"], tag + f["what"], f["why"], f["todo"], f["kind"])
    except Exception:
        pass
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"root": str(proj.root), "online": a.online,
                               "map": {h: {"kind": e["kind"], "files": sorted(e["files"]), "uses": e["uses"], "secure": e["secure"]} for h, e in m.items()},
                               "findings": [{k: v for k, v in f.items() if k != "patches"} for f in fs], "online_results": rows}, indent=1, ensure_ascii=False), encoding="utf-8")
    print("\nFull report saved to: %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
