#!/usr/bin/env python3
"""
ARACHNID - SECURITY HUNTER  (looks only, never changes anything)
================================================================
    python seccheck.py [folder]
    python seccheck.py [folder] --rules        list every rule

Reads your code the way an attacker would, looking for the doors they try first:

  INJECTION       what a visitor types ends up INSIDE a database query, a shell command or eval() - so they can
                  run their own commands        cursor.execute(f"SELECT * FROM users WHERE name = '{name}'")
  XSS             visitor text is put into the page as HTML, so they can run their own script in other people's browsers
                  el.innerHTML = comment
  SECRETS         passwords, API keys and private keys written into the code (or a committed .env file)
  BROKEN TRUST    tokens accepted without checking their signature, passwords stored with a fast hash, guessable
                  "random" codes, files / redirects / requests chosen by the visitor
  OPEN API ROUTES routes that CHANGE data (POST, PUT, PATCH, DELETE) with no visible login or permission check
                  (Express, Next.js, Flask, FastAPI, Django REST framework)

How sure it is
  ALARM     the danger is clear: visitor input flows straight into a query / command, a real-looking key is in the code
  DECISION  a risky pattern - a person decides whether the value can ever come from a visitor
  IDEA      hardening that is good practice
Connections (certificate checks switched off, CORS for everyone, keys in URLs) are covered by netcheck.py, and
libraries with known holes by vulncheck.py - not repeated here.  Nothing is changed: a security fix changes behaviour.

Honest limits: it reads code, it does not attack a running app.  It cannot know your business rules (who may see whose
data), and authentication done somewhere it cannot see (a gateway, a proxy) looks like "no check" - mark those as intended.
"""
import argparse
import ast
import json
import posixpath
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import dedupe  # noqa: E402
import ledger  # noqa: E402
from bugcheck import TEST_PATH, _dotted, _line, _match_close, _split_top, js_lex  # noqa: E402

MAX_SIZE = 1_500_000

# kind: (pile, title, why, what to do)
RULES = {
    "sec-sql-injection": ("decision", "a database query built from text",
                          "if any part can come from a visitor, they can change the query - read or delete every row (SQL injection)",
                          "pass values separately: cursor.execute(\"... WHERE name = %s\", (name,)) / db.query(\"... = ?\", [name])"),
    "sec-command-injection": ("decision", "a shell command built from text",
                              "if any part can come from a visitor, they can add their own commands (`; rm -rf /`)",
                              "pass a list of arguments without shell=True: subprocess.run([\"convert\", path]) / execFile(cmd, [arg])"),
    "sec-code-injection": ("decision", "code built from text and run",
                           "eval / exec / new Function run whatever the text says - including a visitor's text",
                           "use json.loads / ast.literal_eval / JSON.parse, or a lookup table of allowed actions"),
    "sec-unsafe-deserialize": ("decision", "data loaded in a way that can run code",
                               "pickle, marshal and yaml.load (without SafeLoader) can run code hidden inside the data",
                               "use json, or yaml.safe_load; only unpickle data you created yourself"),
    "sec-secret-key": ("alarm", "a real-looking secret key in the code",
                       "anyone who can read the code (a public repo, a leaked laptop, the browser for front-end code) can use it",
                       "revoke this key now, create a new one, and read it from an environment variable"),
    "sec-hardcoded-password": ("decision", "a password or secret written in the code",
                               "secrets in code end up in git history, screenshots and every copy of the project",
                               "read it from an environment variable or a secrets manager"),
    "sec-env-committed": ("alarm", "a .env file with secrets is part of the project",
                          "it is shared with everyone who gets the code (and stays in git history even after deleting it)",
                          "add .env to .gitignore, remove it from git (git rm --cached .env), and change the secrets in it"),
    "sec-jwt-unverified": ("alarm", "login tokens accepted without checking their signature",
                           "anyone can write their own token saying \"I am the admin\"", "verify the signature and allow only the algorithms you use"),
    "sec-weak-password-hash": ("decision", "passwords stored with a fast hash",
                               "md5 / sha1 / sha256 can be guessed billions of times per second once the database leaks",
                               "use bcrypt, argon2 or scrypt (e.g. passlib, bcrypt, argon2-cffi / bcryptjs)"),
    "sec-insecure-random": ("decision", "a guessable security code",
                            "random / Math.random are predictable; tokens, OTPs and reset codes made with them can be guessed",
                            "use secrets.token_urlsafe() / secrets.randbelow() in Python, crypto.randomUUID() / crypto.getRandomValues() in JS"),
    "sec-path-traversal": ("decision", "a file chosen by the visitor",
                           "`../../etc/passwd` in the name walks out of the folder and reads (or deletes) any file",
                           "keep only the file name (os.path.basename / path.basename) and check it against an allowed folder"),
    "sec-open-redirect": ("decision", "a redirect to an address chosen by the visitor",
                          "attackers send links to YOUR site that bounce people to a fake login page", "only redirect to your own paths, or a list of allowed addresses"),
    "sec-ssrf": ("decision", "the server fetches an address chosen by the visitor",
                 "they can make your server read internal addresses (cloud keys at 169.254.169.254, admin panels)",
                 "allow only known hosts, and block private addresses"),
    "sec-xss": ("decision", "text put into the page as HTML",
                "if it can contain visitor text, they can run their own script in other people's browsers (XSS) and steal sessions",
                "use textContent / normal {value} rendering, or clean it with DOMPurify.sanitize(...)"),
    "sec-xxe": ("decision", "an XML reader that loads outside files",
                "a visitor's XML can say \"include /etc/passwd\" (or an internal address) and the server reads it out (XXE)",
                "switch off entity loading: defusedxml in Python, noent: false in libxmljs"),
    "sec-redos": ("decision", "a text pattern that can freeze the server",
                  "a nested repeat like ([0-9]+)+ takes forever on a crafted input - one request blocks everyone (ReDoS)",
                  "remove the outer repeat (e.g. /([0-9]+)#/), or limit the input length first"),
    "sec-template-injection": ("decision", "a page template built from text",
                               "template code inside visitor text gets run on the server", "pass values to render_template(...) instead of building the template"),
    "sec-nosql-injection": ("decision", "a database filter taken straight from the request",
                            "a visitor can send {\"$ne\": null} instead of a password and match every user", "pick the exact fields: { email: String(req.body.email) }"),
    "sec-mass-assignment": ("decision", "a record created from the whole request",
                            "a visitor can add fields you did not expect, like isAdmin: true or balance: 99999",
                            "copy only the allowed fields into the record"),
    "sec-debug-on": ("decision", "debug mode switched on in code",
                     "the debug page shows code and can even run commands for anyone who triggers an error",
                     "read it from an environment variable and keep it off in production"),
    "sec-csrf-off": ("decision", "form protection (CSRF) switched off",
                     "other websites can submit this form in the name of a logged-in visitor", "keep CSRF protection on, or protect the route with a token header"),
    "sec-token-storage": ("idea", "a login token kept in localStorage",
                          "any script on the page (including an injected one) can read localStorage", "prefer an HttpOnly cookie"),
    "sec-cookie-flags": ("idea", "a session cookie readable by scripts",
                         "without HttpOnly, an injected script can steal the session; without Secure it can travel unencrypted",
                         "set httpOnly: true, secure: true, sameSite"),
    "api-open-route": ("decision", "an API route that changes data, with no visible login check",
                       "anyone who finds the address can call it - create, change or delete data without logging in",
                       "add your login / permission check (middleware, decorator or dependency) - or mark it as intended if it is public"),
    "api-allow-any": ("decision", "an API view that explicitly allows everyone",
                      "permission_classes = [AllowAny] on a view that creates, changes or deletes data",
                      "use IsAuthenticated (or your own permission) unless the route is meant to be public"),
}

TAINT = re.compile(r"\brequest\.(args|form|values|json|data|files|get_json|GET|POST|body|query_params|params|headers|cookies|view_args|path_params|query)\b"
                   r"|\breq\.(query|params|body|headers|cookies|url|files?)\b|\binput\s*\(|\bsys\.argv\b|\bevent\s*\[\s*['\"](body|queryStringParameters|pathParameters)"
                   r"|\b(searchParams|URLSearchParams|location\.(search|hash|href))\b|\bctx\.(query|params|request)\b")
SQL_WORDS = re.compile(r"(?i)\b(select\b[\s\S]*\bfrom|insert\s+into|update\s+[\w\"`.]+\s+set|delete\s+from|drop\s+table|create\s+table|alter\s+table|\bwhere\b\s+\w)")
SECRET_NAME = re.compile(r"(?i)(passw(or)?d|passwd|pwd|secret|api_?key|apikey|access_?key|auth_?token|private_?key|client_?secret|token|credential|"
                         r"(secret|signing|encryption|jwt|session|app|master)_?key)s?$")
PLACEHOLDER = re.compile(r"(?i)^(x+|\*+|changeme|change_me|your[_-].*|<.*>|\{.*\}|\$\{.*\}|%\(.*|example.*|sample.*|dummy.*|test.*|fake.*|placeholder|"
                         r"password|secret|none|null|undefined|todo|xxx.*|\.\.\.|replace.*|insert.*|enter.*|my[_-]?(secret|password|key).*|"
                         r"admin|root|guest|demo.*|default|dev.*|local.*|[a-z_]+)$")
# known key formats - (name, pattern)
KEY_FORMATS = [
    ("AWS access key", r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"),
    ("GitHub token", r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b|\bgithub_pat_[A-Za-z0-9_]{60,}\b"),
    ("Stripe live secret key", r"\b(sk|rk)_live_[0-9a-zA-Z]{20,}\b"),
    ("Slack token", r"\bxox[abprs]-[0-9A-Za-z-]{20,}\b"),
    ("Google API key", r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    ("OpenAI key", r"\bsk-(proj-|svcacct-)?[A-Za-z0-9_\-]{40,}\b"),
    ("Anthropic key", r"\bsk-ant-(api|admin)\d{2}-[A-Za-z0-9_\-]{60,}\b"),
    ("SendGrid key", r"\bSG\.[A-Za-z0-9_\-]{22}\.[A-Za-z0-9_\-]{43}\b"),
    ("Twilio key", r"\bSK[0-9a-f]{32}\b"),
    ("private key", r"-----BEGIN (RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY( BLOCK)?-----(\r?\n|\\r?\\n)+[A-Za-z0-9+/=]{40,}"),
    ("Razorpay live key", r"\brzp_live_[A-Za-z0-9]{14,}\b"),
    ("Discord bot token", r"\b[MN][A-Za-z\d]{23,25}\.[\w-]{6}\.[\w-]{27,38}\b"),
    ("npm token", r"\bnpm_[A-Za-z0-9]{36}\b"),
    ("Hugging Face token", r"\bhf_[A-Za-z0-9]{34,}\b"),
]
KEY_RE = re.compile("|".join("(?P<k%d>%s)" % (i, p) for i, (_, p) in enumerate(KEY_FORMATS)))
AUTH_WORDS = re.compile(r"(?i)auth|login|logged|protect|verify|jwt|passport|session|guard|admin|permission|acl|\brole|token|api_?key|signed|"
                        r"ensure|require|restrict|clerk|firebase|cognito|oauth|bearer|secure|private|owner|current_?user|getuser|isuser|"
                        r"identity|principal|can[A-Z_]|allow|policy|scope")
PUBLIC_PATH = re.compile(r"(?i)(log-?in|sign-?in|sign-?up|register|log-?out|auth|oauth|callback|webhook|hooks?\b|contact|subscribe|newsletter|"
                         r"forgot|reset|password|verify|confirm|health|ping|status|public|track|analytics|csp|report|feedback|graphql|"
                         r"token|refresh|session|otp|captcha|invite|waitlist|unsubscribe|stripe|razorpay|paypal|ipn)")


def add(out, rel, line, kind, detail, alarm=False):
    out.append({"file": rel, "line": int(line or 0), "kind": kind, "detail": detail, "alarm": alarm})


def is_test(rel):
    return bool(TEST_PATH.search(rel)) or bool(re.search(r"(?i)(^|/)(examples?|samples?|demo|docs?)/", rel))


# =================================================================================================== secrets (every text file)
def check_secrets(rel, text):
    out = []
    low = rel.lower()
    if low.endswith((".lock", "-lock.json", ".min.js", ".map", ".svg")) or "example" in low or "sample" in low or "template" in low:
        return out
    for m in KEY_RE.finditer(text):
        before = text[text.rfind("\n", 0, m.start()) + 1:m.start()]
        if re.search(r"://\S*[?&][\w.-]+=$", before):
            continue                                   # inside a web address: netcheck reports it as "secret in URL"
        name = next(KEY_FORMATS[int(k[1:])][0] for k, v in m.groupdict().items() if v)
        val = m.group(0)
        if re.search(r"(?i)x{6,}|0{8,}|1234567|abcdef|example|dummy|your", val):
            continue
        add(out, rel, _line(text, m.start()), "sec-secret-key", "%s: %s...%s" % (name, val[:6], val[-3:] if len(val) > 12 else ""), alarm=True)
    return out


def _secret_value(name, value):
    if not SECRET_NAME.search(name) or re.search(r"(?i)(_?(name|field|header|url|path|file|env|var|key_?name|label|prompt|"
                                                 r"type|id|length|len|min|max|hint|regex|pattern|placeholder|message|error|policy|reset))$", name):
        return False
    v = value.strip()
    if re.search(r"[\[\]\\|(){}<>\s^,;'\"]", v):
        return False                                  # patterns, lists and sentences are not secrets
    if len(v) < 8 or " " in v or PLACEHOLDER.match(v) or v.startswith(("http://", "https://", "/", "./", "$", "{{", "%")):
        return False
    return bool(re.search(r"\d", v) and re.search(r"[A-Za-z]", v)) or len(v) >= 16


def check_env_file(rel, text, root):
    out = []
    base = posixpath.basename(rel)
    if not re.fullmatch(r"\.env(\.(local|production|prod|development|dev|staging))?", base):
        return out
    secrets = [(i, ln) for i, ln in enumerate(text.splitlines(), 1)
               if re.match(r"\s*(export\s+)?([A-Z0-9_]+)\s*=\s*\S", ln) and SECRET_NAME.search(re.match(r"\s*(export\s+)?([A-Z0-9_]+)", ln).group(2).lower().replace("_", ""))
               or re.search(r"(?i)(KEY|SECRET|TOKEN|PASSWORD|PASS|PWD)\w*\s*=\s*\S{6,}", ln)]
    if not secrets:
        return out
    tracked = None
    try:
        r = subprocess.run(["git", "-C", str(root), "ls-files", "--error-unmatch", rel], capture_output=True, text=True, timeout=10)
        tracked = True if r.returncode == 0 else (None if "not a git repository" in (r.stderr or "").lower() else False)
        if tracked is False:
            ign = subprocess.run(["git", "-C", str(root), "check-ignore", "-q", rel], capture_output=True, timeout=10)
            if ign.returncode == 0:
                return out                             # ignored by git: it stays on this computer - fine
    except (OSError, subprocess.SubprocessError):
        pass
    if tracked is None:
        gi = Path(root) / ".gitignore"
        try:
            if re.search(r"(?m)^\s*/?\.env(\*|\.\*|\s*$)", gi.read_text(encoding="utf-8")):
                return out
        except OSError:
            pass
    add(out, rel, secrets[0][0], "sec-env-committed", "%d secret value(s), e.g. %s" % (len(secrets), secrets[0][1].split("=")[0].strip()[:30]), alarm=True)
    return out


# =================================================================================================== Python
def _dynamic_str(node):
    """The parts of a text built at run time (f-string, %, +, .format), or None if it is a plain constant."""
    if isinstance(node, ast.JoinedStr):
        vals = [v.value for v in node.values if isinstance(v, ast.FormattedValue)]
        return vals or None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod) and isinstance(node.left, ast.Constant) and isinstance(node.left.value, str):
        return [node.right]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        parts, stack = [], [node]
        has_str = False
        while stack:
            n = stack.pop()
            if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add):
                stack += [n.left, n.right]
            elif isinstance(n, ast.Constant) and isinstance(n.value, str):
                has_str = True
            elif isinstance(n, ast.JoinedStr):
                has_str = True
                parts += _dynamic_str(n) or []
            else:
                parts.append(n)
        return parts if has_str and parts else None
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format" and \
            isinstance(node.func.value, ast.Constant) and isinstance(node.func.value.value, str):
        return list(node.args) + [k.value for k in node.keywords] or None
    return None


def _const_text(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else " x " for v in node.values)
    if isinstance(node, ast.BinOp):
        return _const_text(node.left) + " x " + _const_text(node.right)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format":
        return _const_text(node.func.value)
    return ""


def _harmless(parts, assigned=None, depth=0):
    """Every inserted value is a constant, a number cast, or an UPPER_CASE setting - nothing a visitor controls."""
    for p in parts:
        if isinstance(p, ast.Name) and assigned and depth < 2 and len(assigned.get(p.id, [])) == 1 and \
                _harmless([assigned[p.id][0]], assigned, depth + 1):
            continue
        if isinstance(p, ast.Constant):
            continue
        if isinstance(p, ast.Name) and (p.id.isupper() or p.id in ("placeholders", "qmarks", "columns", "cols", "table", "tbl", "fields")):
            continue
        if isinstance(p, ast.Call) and _dotted(p.func) in ("int", "float", "len", "bool", "str.join") or \
                isinstance(p, ast.Call) and isinstance(p.func, ast.Attribute) and p.func.attr == "join" and \
                isinstance(p.func.value, ast.Constant) and set(str(p.func.value.value)) <= set(", ?%s"):
            continue
        return False
    return True


def _source_getter(text):
    """Fast ast.get_source_segment: the file is split into lines ONCE (the standard one re-splits on every call)."""
    lines = [l.encode("utf-8") for l in text.splitlines(keepends=True)]

    def seg(node):
        try:
            l1, l2, c1, c2 = node.lineno - 1, node.end_lineno - 1, node.col_offset, node.end_col_offset
        except AttributeError:
            return ""
        if l1 < 0 or l2 >= len(lines) or c2 is None:
            return ""
        if l1 == l2:
            return lines[l1][c1:c2].decode("utf-8", "replace")
        return (lines[l1][c1:] + b"".join(lines[l1 + 1:l2]) + lines[l2][:c2]).decode("utf-8", "replace")
    return seg


def check_python(rel, text):
    out = []
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return out
    src = _source_getter(text)
    assigned = {}                                     # name -> value node, for one hop: q = f"..."; execute(q)
    parent = {}
    for n in ast.walk(tree):
        for ch in ast.iter_child_nodes(n):
            parent[ch] = n
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
            assigned.setdefault(n.targets[0].id, []).append(n.value)

    def scope_of(node):
        p = parent.get(node)
        while p is not None and not isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef)):
            p = parent.get(p)
        return p or tree
    scoped = {}                                       # (scope, name) -> [(line, kind, value)]  kind: "=" or "+="
    for n in ast.walk(tree):
        if isinstance(n, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = n.targets if isinstance(n, ast.Assign) else [n.target]
            if n.value is None:
                continue
            for t in targets:
                if isinstance(t, ast.Name):
                    k = "+=" if isinstance(n, ast.AugAssign) and isinstance(n.op, ast.Add) else "=" if not isinstance(n, ast.AugAssign) else None
                    if k:
                        scoped.setdefault((scope_of(n), t.id), []).append((n.lineno, k, n.value))
    imports_yaml_safe = "SafeLoader" in text or "CSafeLoader" in text
    md5_names = set(re.findall(r"from\s+hashlib\s+import\s+([\w, ]+)", text))
    md5_names = {x.strip() for grp in md5_names for x in grp.split(",")} & {"md5", "sha1", "sha256", "sha512", "sha224"}

    def code_of(node):
        """The code of an expression WITHOUT the text inside its quotes ("input(" written in a string is not input)."""
        parts = []
        for n in ast.walk(node):
            if isinstance(n, (ast.Attribute, ast.Subscript)):
                parts.append(src(n.value) + "." + (n.attr if isinstance(n, ast.Attribute) else "[]"))
            elif isinstance(n, ast.Call):
                parts.append(src(n.func) + "(")
            elif isinstance(n, ast.Name):
                parts.append(n.id)
        return " ".join(parts)

    def tainted_expr(node, depth=0, at=None):
        """Does this value come from the visitor - directly, or through variables set from request data?"""
        if TAINT.search(code_of(node)):
            return True
        if depth < 3:
            sc = scope_of(at if at is not None else node)
            for n in ast.walk(node):
                if isinstance(n, ast.Name):
                    vals = [v for _, _, v in scoped.get((sc, n.id), [])] or (assigned.get(n.id, []) if len(assigned.get(n.id, [])) == 1 else [])
                    if any(tainted_expr(v, depth + 1, at if at is not None else node) for v in vals):
                        return True
        return False

    def resolve(node, at=None):
        """The text a variable holds at this point: q = "SELECT ..." + name; q += " LIMIT 5"; execute(q)"""
        if not isinstance(node, ast.Name):
            return node
        hist = sorted(x for x in scoped.get((scope_of(at or node), node.id), []) if x[0] <= (at or node).lineno)
        if hist:
            start = max((i for i, h in enumerate(hist) if h[1] == "="), default=None)
            if start is not None:
                val = hist[start][2]
                for _, k, v in hist[start + 1:]:
                    val = ast.BinOp(left=val, op=ast.Add(), right=v)
                return val
        if node.id in assigned and len(assigned[node.id]) == 1:
            return assigned[node.id][0]
        return node

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = _dotted(node.func) or ""
            last = fn.rsplit(".", 1)[-1]
            line = node.lineno
            args = node.args
            kw = {k.arg: k.value for k in node.keywords if k.arg}
            tainted = bool(TAINT.search(code_of(node)))
            # --- SQL
            if last in ("execute", "executemany", "executescript", "raw", "text", "read_sql", "read_sql_query", "exec_driver_sql", "query") and args:
                q = resolve(args[0], node)
                parts = _dynamic_str(q)
                if parts and SQL_WORDS.search(_const_text(q)) and not _harmless(parts, assigned):
                    t = tainted or any(tainted_expr(p, at=node) for p in parts)
                    add(out, rel, line, "sec-sql-injection", "`%s(...)` with %s" % (last, src(args[0])[:50]), alarm=bool(t))
            # --- shell commands
            shell_call = fn in ("os.system", "os.popen", "commands.getoutput", "subprocess.getoutput", "subprocess.getstatusoutput")
            sp = fn.startswith("subprocess.") and last in ("run", "call", "check_call", "check_output", "Popen") and \
                isinstance(kw.get("shell"), ast.Constant) and kw["shell"].value is True
            if (shell_call or sp) and args:
                c = resolve(args[0], node)
                parts = _dynamic_str(c)
                if parts and not _harmless(parts, assigned) or (isinstance(args[0], ast.Name) and c is args[0]):
                    add(out, rel, line, "sec-command-injection", "`%s(%s)`" % (fn, src(args[0])[:40]),
                        alarm=tainted or bool(parts and any(tainted_expr(p, at=node) for p in parts)) or tainted_expr(args[0], at=node))
            # --- eval / exec
            if fn in ("eval", "exec") and args and not isinstance(args[0], ast.Constant):
                if not (isinstance(args[0], ast.Name) and args[0].id in ("code", "compiled", "source_code") and not tainted):
                    add(out, rel, line, "sec-code-injection", "`%s(%s)`" % (fn, src(args[0])[:40]), alarm=tainted or tainted_expr(args[0], at=node))
            # --- deserialisation
            if fn in ("pickle.loads", "pickle.load", "cPickle.loads", "cPickle.load", "marshal.loads", "dill.loads", "dill.load",
                      "jsonpickle.decode", "yaml.unsafe_load", "joblib.load") or \
                    fn == "yaml.load" and not imports_yaml_safe and "Loader" not in kw and len(args) < 2 or \
                    fn in ("yaml.load", "yaml.load_all") and re.search(r"\b(Unsafe|Full)?Loader\b", src(kw.get("Loader") or (args[1] if len(args) > 1 else ast.Constant("")))) \
                    and not re.search(r"Safe|Base", src(kw.get("Loader") or (args[1] if len(args) > 1 else ast.Constant("")))):
                add(out, rel, line, "sec-unsafe-deserialize", "`%s(...)`" % fn, alarm=tainted or any(tainted_expr(a, at=node) for a in args))
            # --- JWT
            if fn in ("jwt.decode",) or last == "decode" and fn.startswith("jwt"):
                opts = kw.get("options")
                s = src(node)
                if re.search(r"""verify_signature['"]?\s*:\s*False|\bverify\s*=\s*False|algorithms\s*=\s*\[\s*['"]none['"]""", s):
                    add(out, rel, line, "sec-jwt-unverified", "`%s`" % s[:60], alarm=True)
            # --- password hashing
            stored = isinstance(parent.get(node), ast.Attribute) and parent[node].attr == "hexdigest"   # text kept in a database
            if (re.fullmatch(r"hashlib\.(md5|sha1|sha224|sha256|sha512|new)", fn) or fn in md5_names) and stored and \
                    re.search(r"(?i)pass(word|wd)?|pwd", src(node)):
                add(out, rel, line, "sec-weak-password-hash", "`%s(...)` on a password" % fn)
            # --- text patterns that can freeze the server
            if fn in ("re.compile", "re.match", "re.search", "re.fullmatch", "re.findall", "re.finditer", "re.sub", "re.split") and args and \
                    isinstance(args[0], ast.Constant) and isinstance(args[0].value, str) and REDOS.search(args[0].value):
                add(out, rel, line, "sec-redos", "`%s(%r)`" % (fn, args[0].value[:40]))
            # --- XML that may load outside files (XXE)
            if last == "setFeature" and len(args) == 2 and re.search(r"external_(ges|pes)|external-general|external-parameter", src(args[0])) and \
                    isinstance(args[1], ast.Constant) and args[1].value is True:
                add(out, rel, line, "sec-xxe", "`%s`" % src(node)[:60])
            if last == "XMLParser" and any(k in kw and isinstance(kw[k], ast.Constant) and kw[k].value is v for k, v in
                                           (("resolve_entities", True), ("no_network", False), ("load_dtd", True))):
                add(out, rel, line, "sec-xxe", "`%s`" % src(node)[:60])
            # --- template engines with escaping switched off
            if isinstance(kw.get("autoescape"), ast.Constant) and kw["autoescape"].value is False:
                add(out, rel, line, "sec-xss", "`%s(... autoescape=False)` - every value is put into pages as HTML" % fn[:30])
            # --- files / redirects / fetches chosen by the visitor
            sink = fn in ("open", "send_file", "os.remove", "os.unlink", "shutil.rmtree", "os.rmdir", "io.open", "FileResponse", "requests.get",
                          "requests.post", "requests.request", "urllib.request.urlopen", "httpx.get", "httpx.post", "urlopen") or \
                last in ("send_file", "read_text", "read_bytes", "write_text", "unlink", "redirect", "HttpResponseRedirect", "RedirectResponse") or \
                fn.endswith(("session.get", "client.get"))
            tainted = tainted or (sink and any(tainted_expr(a, at=node) for a in args[:1]))
            if tainted and (fn in ("open", "send_file", "os.remove", "os.unlink", "shutil.rmtree", "os.rmdir", "io.open", "FileResponse")
                            or last in ("send_file", "read_text", "read_bytes", "write_text", "unlink")) and \
                    not re.search(r"basename|secure_filename|safe_join|resolve\(\)\.is_relative_to", src(node)):
                add(out, rel, line, "sec-path-traversal", "`%s`" % src(node)[:60])
            if tainted and last in ("redirect", "HttpResponseRedirect", "RedirectResponse") and not re.search(r"url_for|reverse\(|is_safe_url|url_has_allowed", src(node)):
                add(out, rel, line, "sec-open-redirect", "`%s`" % src(node)[:60])
            if tainted and (fn in ("requests.get", "requests.post", "requests.request", "urllib.request.urlopen", "httpx.get", "httpx.post", "urlopen")
                            or fn.endswith(("session.get", "client.get"))) and args and tainted_expr(args[0], at=node):
                add(out, rel, line, "sec-ssrf", "`%s`" % src(node)[:60])
            # --- templates, debug
            if fn in ("render_template_string", "Template", "jinja2.Template", "Markup", "mark_safe") and args and \
                    (_dynamic_str(resolve(args[0])) or tainted) and not isinstance(args[0], ast.Constant):
                add(out, rel, line, "sec-template-injection" if fn != "Markup" and fn != "mark_safe" else "sec-xss", "`%s(%s)`" % (fn, src(args[0])[:40]), alarm=tainted)
            if last == "run" and isinstance(kw.get("debug"), ast.Constant) and kw["debug"].value is True and \
                    isinstance(node.func, ast.Attribute) and re.search(r"(?i)app|server", src(node.func.value)):
                add(out, rel, line, "sec-debug-on", "`%s`" % src(node)[:50])
            # --- mass assignment: Model(**request.json)
            for k in node.keywords:
                if k.arg is None and TAINT.search(src(k.value)) and re.match(r"[A-Z]", last or "x") or \
                        k.arg is None and TAINT.search(src(k.value)) and last in ("create", "update", "insert", "update_or_create", "get_or_create"):
                    add(out, rel, line, "sec-mass-assignment", "`%s(**%s)`" % (fn[:30], src(k.value)[:30]))
        # --- secrets in assignments / keywords
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            v = node.value
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                for t in targets:
                    name = t.id if isinstance(t, ast.Name) else t.attr if isinstance(t, ast.Attribute) else None
                    if name and _secret_value(name, v.value):
                        add(out, rel, node.lineno, "sec-hardcoded-password", "%s = \"%s...\"" % (name, v.value[:3]))
            if isinstance(v, ast.Call):
                fn = _dotted(v.func) or ""
                for t in targets:
                    name = t.id if isinstance(t, ast.Name) else t.attr if isinstance(t, ast.Attribute) else ""
                    if re.match(r"random\.(random|randint|choice|choices|randrange|getrandbits|sample|uniform)$", fn) and \
                            re.search(r"(?i)token|otp|passw|secret|nonce|salt|reset|verif|session|api_?key|code$", name):
                        add(out, rel, node.lineno, "sec-insecure-random", "`%s = %s(...)`" % (name, fn))
            if isinstance(v, (ast.JoinedStr,)) and any(isinstance(t, ast.Name) and re.search(r"(?i)token|otp|passw|secret|code$", t.id) for t in targets) \
                    and re.search(r"random\.(random|randint|choice|choices)", src(v)):
                add(out, rel, node.lineno, "sec-insecure-random", "built with random")
        if isinstance(node, ast.Call):
            for k in node.keywords:
                if k.arg and isinstance(k.value, ast.Constant) and isinstance(k.value.value, str) and _secret_value(k.arg, k.value.value) \
                        and not re.search(r"(?i)default|help|example", k.arg):
                    add(out, rel, k.value.lineno, "sec-hardcoded-password", "%s=\"%s...\"" % (k.arg, k.value.value[:3]))
        # --- decorators: csrf_exempt
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for d in node.decorator_list:
                if (_dotted(d) or "").endswith("csrf_exempt"):
                    add(out, rel, d.lineno, "sec-csrf-off", "@csrf_exempt on %s" % node.name)
    # --- settings files
    base = posixpath.basename(rel)
    if base in ("settings.py", "config.py", "production.py", "prod.py") or rel.endswith(("settings/production.py", "settings/prod.py")):
        for m in re.finditer(r"(?m)^(DEBUG|WTF_CSRF_ENABLED)\s*=\s*(True|False)\b", text):
            if m.group(1) == "DEBUG" and m.group(2) == "True" and re.search(r"prod", rel):
                add(out, rel, _line(text, m.start()), "sec-debug-on", "DEBUG = True in a production settings file")
            if m.group(1) == "WTF_CSRF_ENABLED" and m.group(2) == "False":
                add(out, rel, _line(text, m.start()), "sec-csrf-off", "WTF_CSRF_ENABLED = False")
    out += check_python_routes(rel, text, tree)
    return out


def check_python_routes(rel, text, tree):
    """Flask / FastAPI routes that change data with no visible login check; DRF AllowAny."""
    out = []
    src = _source_getter(text)
    global_hook = bool(re.search(r"@\w+\.before_(app_)?request|APIRouter\([^)]*dependencies\s*=|FastAPI\([^)]*dependencies\s*=|"
                                 r"include_router\([^)]*dependencies\s*=|add_middleware\(\s*\w*(Auth|JWT|Session)", text))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.decorator_list:
            route, method_change, path = None, False, ""
            for d in node.decorator_list:
                if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute):
                    m = d.func.attr.lower()
                    if m in ("route", "api_route", "post", "put", "patch", "delete", "websocket"):
                        route = d
                        path = d.args[0].value if d.args and isinstance(d.args[0], ast.Constant) and isinstance(d.args[0].value, str) else ""
                        if m in ("post", "put", "patch", "delete"):
                            method_change = True
                        meths = next((k.value for k in d.keywords if k.arg == "methods"), None)
                        if meths is not None and re.search(r"(?i)['\"](POST|PUT|PATCH|DELETE)['\"]", src(meths)):
                            method_change = True
                        if any(k.arg == "dependencies" for k in d.keywords) and AUTH_WORDS.search(src(d)):
                            route = None
            if route is None or not method_change or global_hook or PUBLIC_PATH.search(path or node.name):
                continue
            decos = " ".join(src(d) for d in node.decorator_list if d is not route)
            params = " ".join(src(a) for a in node.args.defaults + node.args.kw_defaults if a is not None) + " " + \
                " ".join(src(a.annotation) for a in node.args.args + node.args.kwonlyargs if a.annotation)
            body = "\n".join(src(s) for s in node.body)
            if AUTH_WORDS.search(decos) or re.search(r"Depends|Security", params) and AUTH_WORDS.search(params) or \
                    re.search(r"current_user|g\.user|session\[|session\.get|request\.user|Authorization|verify_jwt|get_jwt_identity|"
                              r"jwt_required|login_required|is_authenticated|request\.auth|get_current_user|require_|check_permission|abort\(\s*40[13]", body):
                continue
            meth = d.func.attr.upper() if route.func.attr.lower() != "route" else "POST/PUT/DELETE"
            add(out, rel, node.lineno, "api-open-route", "%s %s (%s)" % (route.func.attr.upper() if route.func.attr.lower() != "route" else "route",
                                                                          path or "?", node.name))
        if isinstance(node, ast.ClassDef):
            body = src(node)
            if re.search(r"permission_classes\s*=\s*[\[(]\s*(permissions\.)?AllowAny\s*,?\s*[\])]", body) and \
                    (re.search(r"def\s+(post|put|patch|delete|create|update|destroy|perform_create|perform_update)\b", body) or
                     re.search(r"\((\w+\.)?(ModelViewSet|CreateAPIView|UpdateAPIView|DestroyAPIView|ListCreateAPIView|RetrieveUpdateDestroyAPIView|"
                               r"CreateModelMixin|GenericViewSet)\b", src(node).split("\n")[0])) and not PUBLIC_PATH.search(node.name):
                add(out, rel, node.lineno, "api-allow-any", "class %s" % node.name)
    for m in re.finditer(r"@permission_classes\(\s*\[\s*(permissions\.)?AllowAny\s*\]\s*\)\s*\n(\s*@[^\n]+\n)*\s*def\s+(\w+)", text):
        if re.search(r"@api_view\(\s*\[[^\]]*(POST|PUT|PATCH|DELETE)", text[max(0, m.start() - 200):m.end()]) and not PUBLIC_PATH.search(m.group(3)):
            add(out, rel, _line(text, m.start()), "api-allow-any", "def %s" % m.group(3))
    return out


# =================================================================================================== JavaScript / TypeScript
JS_AUTH_BODY = re.compile(r"req\.(user|auth|session|isAuthenticated)|request\.(user|auth)|getServerSession|getSession\(|auth\(\)|currentUser|"
                          r"getToken\(|verifyToken|jwt\.verify|authorization|clerk|getAuth\(|requireAuth|withAuth|supabase\.auth|"
                          r"firebase\.auth|admin\.auth|res\.status\(\s*40[13]\)|status:\s*40[13]|unauthori[sz]ed|forbidden", re.I)


LIB_NAMES = re.compile(r"(?i)(^|/)(jquery|bootstrap|materialize|three|lodash|underscore|d3|chart|moment|angular|react(-dom)?|vue|"
                       r"popper|semantic|foundation|backbone|handlebars|mustache|highlight|prism|codemirror|ace|tinymce|ckeditor|"
                       r"leaflet|mapbox-gl|pdf|swiper|slick|select2|datatables|socket\.io|axios|bluebird|zepto|knockout|ember|"
                       r"mathjax|katex|marked|showdown|dompurify|plotly|echarts|fabric|konva|p5|phaser|pixi|babylon)([.-][\w.-]*)?\.js$")
REDOS = re.compile(r"\((?:\?:)?(?:\\.|\[[^\]]+\]|[^()\\|?*+{])[+*](?:(?:\\.|\[[^\]]+\]|[^()\\|?*+{])\?)*\)(?:[+*]|\{\d+,\})")


def is_library(rel, text):
    """Someone else's bundled library (jquery.js, three.js, a file with a /*! licence banner) - not the project's own code."""
    return bool(LIB_NAMES.search(rel)) or ("/vendor/" in "/" + rel) or \
        (len(text) > 15000 and re.search(r"/\*!|@license|\(c\) \d{4}|Copyright \(c\)|MIT License", text[:800]))


def check_js(rel, text):
    out = []
    lines_ = text.split("\n")
    if max((len(x) for x in lines_), default=0) > 2000 or len(text) / max(1, len(lines_)) > 200 or is_library(rel, text):
        return out
    code, strings = js_lex(text)
    destructured = bool(re.search(r"\(\s*\{[^}]*\b(params|query|body)\b[^}]*\}\s*(:\s*\w+)?\s*,\s*res\b", code))
    taint_re = re.compile(TAINT.pattern + (r"|\b(params|query|body)\.\w+" if destructured else ""))

    def value_of(expr, pos, depth=0):
        """The expression plus what its variables were set to just before pos: q = "SELECT" + x; db.query(q)"""
        txt = expr
        if depth >= 2:
            return txt
        for name in list(dict.fromkeys(re.findall(r"(?<![\w$.'\"`])([A-Za-z_$][\w$]*)(?![\w$]*\s*\()", expr)))[:8]:
            if name in ("req", "res", "this", "true", "false", "null", "undefined", "await", "new"):
                continue
            window = code[max(0, pos - 6000):pos]
            hits = list(re.finditer(r"(?:\b(?:const|let|var)\s+|[;{}\n]\s*)%s\s*=(?![=>])\s*" % re.escape(name), window))
            hits += list(re.finditer(r"\b(?:const|let|var)\s*\{[^}]*\b%s\b[^}]*\}\s*=\s*" % re.escape(name), window))
            if hits:
                h = max(hits, key=lambda x: x.end())
                a = max(0, pos - 6000) + h.end()
                e = a
                depth_ = 0
                while e < len(code) and e < a + 600:
                    ch = code[e]
                    if ch in "([{":
                        depth_ += 1
                    elif ch in ")]}":
                        if depth_ == 0:
                            break
                        depth_ -= 1
                    elif ch in ";\n" and depth_ == 0:
                        break
                    e += 1
                sub = text[a:e]
                txt += " " + value_of(sub, a, depth + 1) if sub.strip() else ""
        return txt

    def raw(a, b):
        return text[a:b]

    def call_args(open_paren):
        end = _match_close(code, open_paren)
        return open_paren + 1, end
    # --- XSS sinks
    for m in re.finditer(r"\.(innerHTML|outerHTML)\s*(\+?=)(?!=)\s*", code):
        end = code.find(";", m.end())
        nl = code.find("\n", m.end())
        end = min(x for x in (end, nl if nl >= 0 else len(code), len(code)) if x >= 0)
        val = raw(m.end(), end).strip()
        vcode = code[m.end():end].strip()
        if not val or re.fullmatch(r"(['\"])[^'\"]*\1|``|''|\"\"|`[^`$]*`", val) or re.search(r"(?i)sanitize|purify|escape|encode", val):
            continue
        if "${" not in val and not re.search(r"[A-Za-z_$]", re.sub(r"(['\"`])\s*\1", "", vcode)):
            continue
        add(out, rel, _line(text, m.start()), "sec-xss", "`.%s %s %s`" % (m.group(1), m.group(2), val[:40]), alarm=bool(taint_re.search(value_of(val, m.end()))))
    for m in re.finditer(r"\b(insertAdjacentHTML|document\.write(ln)?)\s*\(", code):
        a, b = call_args(m.end() - 1)
        val = raw(a, b)
        last_arg = _split_top(code[a:b], ",")[-1][1].strip()
        if (re.search(r"[A-Za-z_$]", last_arg) or "${" in val) and not re.search(r"(?i)sanitize|purify|escape", val):
            add(out, rel, _line(text, m.start()), "sec-xss", "`%s(%s)`" % (m.group(1), val[:40]), alarm=bool(TAINT.search(val)))
    for m in re.finditer(r"dangerouslySetInnerHTML\s*=\s*\{\s*\{\s*__html\s*:\s*", code):
        end = _match_close(code, code.rfind("{", 0, m.end()))
        val = raw(m.end(), end).strip().rstrip("}").strip()
        if val and not re.fullmatch(r"(['\"`])[^'\"`$]*\1", val) and not re.search(r"(?i)sanitize|purify|escape|marked\.parse\([^)]*sanitize", val):
            add(out, rel, _line(text, m.start()), "sec-xss", "dangerouslySetInnerHTML with `%s`" % val[:40], alarm=bool(TAINT.search(val)))
    for m in re.finditer(r"\bv-html\s*=\s*(['\"])([^'\"]+)\1", text):
        if not re.search(r"(?i)sanitize|purify", m.group(2)):
            add(out, rel, _line(text, m.start()), "sec-xss", "v-html=\"%s\"" % m.group(2)[:40])
    # --- eval
    for m in re.finditer(r"(?<![\w$.])(eval|Function)\s*\(|\bnew\s+Function\s*\(|\b(setTimeout|setInterval)\s*\(\s*(?=['\"`])", code):
        a, b = call_args(code.find("(", m.start()))
        args_ = _split_top(code[a:b], ",")
        first = (args_[-1] if "Function" in m.group(0) else args_[0])[1].strip()     # new Function(a, b, BODY): the body is last
        val = raw(a, b)
        if m.group(1) == "Function" and not m.group(0).startswith("new"):
            continue
        if not first or re.fullmatch(r"(['\"])\s*\1", first) and "+" not in first:
            if m.group(2) and ("+" in code[a:b].split(",")[0] or "${" in raw(a, b).split(",")[0]):
                add(out, rel, _line(text, m.start()), "sec-code-injection", "`%s(...)` with text built at run time" % m.group(2))
            continue
        add(out, rel, _line(text, m.start()), "sec-code-injection", "`%s(%s)`" % ((m.group(1) or "new Function"), val[:40]), alarm=bool(TAINT.search(val)))
    # --- SQL
    for m in re.finditer(r"\.(query|execute|raw|unsafe|\$queryRawUnsafe|\$executeRawUnsafe|queryRaw|run|all|get|prepare)\s*\(", code):
        a, b = call_args(m.end() - 1)
        first_off, first = _split_top(code[a:b], ",")[0]
        first_raw = raw(a + first_off, a + first_off + len(first)).strip()
        if re.fullmatch(r"[A-Za-z_$][\w$]*", first_raw):
            first_raw = value_of(first_raw, m.start()).split(" ", 1)[-1].strip() or first_raw   # const query = "..." + x; db.query(query)
        dynamic = ("${" in first_raw and first_raw.startswith("`")) or re.search(r"['\"`]\s*\+\s*[A-Za-z_$]|[A-Za-z_$][\w$.\]\)]*\s*\+\s*['\"`]", first_raw)
        if dynamic and SQL_WORDS.search(first_raw) and m.group(1) != "get" or (m.group(1) in ("$queryRawUnsafe", "$executeRawUnsafe") and dynamic):
            if re.match(r"\s*sql`|Prisma\.sql`", text[max(0, a - 6):a + 6]):
                continue
            add(out, rel, _line(text, m.start()), "sec-sql-injection", "`.%s(%s)`" % (m.group(1), first_raw[:50]), alarm=bool(taint_re.search(value_of(first_raw, m.start()))))
    # --- shell commands
    for m in re.finditer(r"\b(exec|execSync|spawn|spawnSync)\s*\(", code):
        before = code[max(0, m.start() - 20):m.start()]
        if re.search(r"\.\s*$", before) and not re.search(r"(child_process|cp|childProcess|proc)\s*\.\s*$", before):
            continue
        if not re.search(r"child_process", text):
            continue
        a, b = call_args(m.end() - 1)
        first_raw = raw(a, a + len(_split_top(code[a:b], ",")[0][1])).strip()
        shell = m.group(1).startswith("exec") or re.search(r"shell\s*:\s*true", raw(a, b))
        if shell and ("${" in first_raw or re.search(r"\+\s*[A-Za-z_$]|[A-Za-z_$][\w$]*\s*\+", first_raw)):
            add(out, rel, _line(text, m.start()), "sec-command-injection", "`%s(%s)`" % (m.group(1), first_raw[:50]), alarm=bool(taint_re.search(value_of(first_raw, m.start()))))
    # --- NoSQL / mass assignment
    for m in re.finditer(r"\.(find|findOne|findOneAndUpdate|updateOne|updateMany|deleteOne|deleteMany|countDocuments|where)\s*\(\s*(req\.(body|query|params)|\{\s*\.\.\.req\.(body|query))\s*[,)]", code):
        add(out, rel, _line(text, m.start()), "sec-nosql-injection", "`.%s(%s)`" % (m.group(1), m.group(2)), alarm=True)
    for m in re.finditer(r"\$where\b\s*['\"]?\s*:\s*", code):
        e = m.end()
        stop = min([x for x in (code.find(",", e), code.find("}", e), code.find("\n", e)) if x > 0] or [len(code)])
        val = text[e:stop]
        if "${" in val or re.search(r"['\"`]\s*\+|\+\s*['\"`]|^\s*[A-Za-z_$]", val):
            add(out, rel, _line(text, m.start()), "sec-nosql-injection", "$where built from text: %s" % val.strip()[:40],
                alarm=bool(taint_re.search(value_of(val, m.start()))))
    for m in re.finditer(r"\.(find|findOne|update|updateOne|updateMany|deleteOne|deleteMany|remove|count|countDocuments|findOneAndUpdate|"
                         r"findOneAndDelete|findOneAndRemove)\s*\(\s*\{[^{}]*?:\s*(req\.(body|query)\.[\w$.]+)\s*[,}]", code):
        obj_start = code.find("{", m.start())
        obj_end = _match_close(code, obj_start)
        if m.group(1).startswith(("update", "findOneAndUpdate")) and not re.match(r"\s*,", code[obj_end + 1:obj_end + 4]):
            continue                                # user.update({ name: ... }) sets data - it is not a filter
        add(out, rel, _line(text, m.start()), "sec-nosql-injection", "filter value `%s` straight from the request" % m.group(2))
    for m in re.finditer(r"\b(?:new\s+[A-Z]\w*|[A-Z]\w*\.(?:create|insertMany|insert|build|update|findByIdAndUpdate|findOneAndUpdate|upsert)|"
                         r"Object\.assign\s*\(\s*[\w$.]+)\s*\(?\s*,?\s*(req\.body)\s*[,)]", code):
        add(out, rel, _line(text, m.start()), "sec-mass-assignment", "`%s`" % " ".join(raw(m.start(), m.end()).split())[:50])
    # --- files, redirects, fetches chosen by the visitor
    for m in re.finditer(r"\b(res\.sendFile|res\.download|fs\.(readFile|readFileSync|createReadStream|unlink|unlinkSync|rm|rmSync|writeFile|writeFileSync)|"
                         r"fsp?\.(readFile|unlink|rm))\s*\(", code):
        a, b = call_args(m.end() - 1)
        first = raw(a, a + len(_split_top(code[a:b], ",")[0][1]))
        if taint_re.search(value_of(first, m.start())) and not re.search(r"basename|sanitize|normalize\([^)]*\)\.startsWith|root\s*:", raw(a, b)):
            add(out, rel, _line(text, m.start()), "sec-path-traversal", "`%s(%s)`" % (m.group(1), first.strip()[:40]))
    for m in re.finditer(r"\bres\.redirect\s*\(", code):
        a, b = call_args(m.end() - 1)
        if taint_re.search(value_of(raw(a, b), m.start())) and not re.search(r"(?i)allow|safe|startsWith\(['\"]/", text[max(0, m.start() - 300):m.start()]):
            add(out, rel, _line(text, m.start()), "sec-open-redirect", "`res.redirect(%s)`" % raw(a, b).strip()[:40])
    for m in re.finditer(r"\b(fetch|axios(\.(get|post|request))?|got|request|http\.get|https\.get|needle(\.(get|post|request))?|"
                         r"superagent(\.(get|post))?|bent|undici\.request|nodeFetch)\s*\(", code):
        a, b = call_args(code.find("(", m.start()))
        first = raw(a, a + len(_split_top(code[a:b], ",")[0][1]))
        if re.search(r"\breq\.(query|body|params)|\b(params|query|body)\.\w+", value_of(first, m.start())) and \
                (taint_re.search(value_of(first, m.start()))) and not re.search(r"(?i)allow|whitelist|safe", text[max(0, m.start() - 300):m.start()]):
            add(out, rel, _line(text, m.start()), "sec-ssrf", "`%s(%s)`" % (m.group(1), first.strip()[:40]))
    # --- more ways to run text as code, load unsafe data, read XML with outside files
    for m in re.finditer(r"\b(math|mathjs)\s*\.\s*(eval|evaluate)\s*\(", code):
        a, b = call_args(m.end() - 1)
        if re.search(r"[A-Za-z_$]", code[a:b]):
            add(out, rel, _line(text, m.start()), "sec-code-injection", "`%s.%s(%s)`" % (m.group(1), m.group(2), raw(a, b)[:40]),
                alarm=bool(taint_re.search(value_of(raw(a, b), m.start()))))
    for m in re.finditer(r"\bvm\.(runInContext|runInNewContext|runInThisContext|compileFunction)\s*\(|\bnew\s+vm\.Script\s*\(", code):
        a, b = call_args(code.find("(", m.start()))
        first = _split_top(code[a:b], ",")[0][1].strip()
        if first and not re.fullmatch(r"(['\"`])\s*\1", first):
            add(out, rel, _line(text, m.start()), "sec-code-injection", "`vm` runs `%s`" % raw(a, b)[:40], alarm=bool(taint_re.search(value_of(raw(a, b), m.start()))))
    if re.search(r"""require\(\s*['"]node-serialize['"]|from\s+['"]node-serialize['"]""", text):
        for m in re.finditer(r"\.unserialize\s*\(", code):
            a, b = call_args(m.end() - 1)
            add(out, rel, _line(text, m.start()), "sec-unsafe-deserialize", "node-serialize `unserialize(%s)` can run code inside the data" % raw(a, b)[:30],
                alarm=True)
    for m in re.finditer(r"\b(parseXml|parseXmlString|parseXmlAsync)\s*\(", code):
        a, b = call_args(m.end() - 1)
        if re.search(r"noent\s*:\s*true", raw(a, b)):
            add(out, rel, _line(text, m.start()), "sec-xxe", "`%s(..., { noent: true })`" % m.group(1), alarm=bool(taint_re.search(value_of(raw(a, b), m.start()))))
    for m in re.finditer(r"\bXML_PARSE_(NOENT|DTDLOAD)\b|\bParseOption\.(NOENT|DTDLOAD)\b", code):
        add(out, rel, _line(text, m.start()), "sec-xxe", "`%s` - the XML reader loads outside files" % m.group(0))
        break
    for m in re.finditer(r"\bautoescape\s*:\s*false\b", code):
        add(out, rel, _line(text, m.start()), "sec-xss", "`autoescape: false` - every value is put into pages as HTML")
    for m in re.finditer(r"\.bypassSecurityTrust(Html|Script|Style|Url|ResourceUrl)\s*\(", code):
        a, b = call_args(m.end() - 1)
        v = raw(a, b).strip()
        if v and not re.fullmatch(r"(['\"])[^'\"]*\1", v):
            add(out, rel, _line(text, m.start()), "sec-xss", "`bypassSecurityTrust%s(%s)` switches Angular's protection off" % (m.group(1), v[:30]),
                alarm=bool(taint_re.search(value_of(v, m.start()))))
    for m in re.finditer(r"""\b(createHmac|createCipheriv|createDecipheriv)\s*\(\s*(['"])[^'"]+\2\s*,\s*(['"])([^'"]{8,})\3""", text):
        if code[m.start():m.start() + 4] == text[m.start():m.start() + 4]:
            add(out, rel, _line(text, m.start()), "sec-hardcoded-password", "%s(..., \"%s...\") - the key is in the code" % (m.group(1), m.group(4)[:3]))
    for m in re.finditer(r"(?<![\w$)\]])/((?:\\.|\[[^\]\n]*\]|[^/\n\[])+)/[gimsuyd]*", text):
        if code[m.start()] == "/" and REDOS.search(m.group(1)) and not code[m.start() + 1:m.start() + 2].strip():
            add(out, rel, _line(text, m.start()), "sec-redos", "`/%s/`" % m.group(1)[:40])
    for m in re.finditer(r"""\bnew\s+RegExp\s*\(\s*(['"])((?:\\.|(?!\1).)+)\1""", text):
        if REDOS.search(m.group(2).replace("\\\\", "\\")):
            add(out, rel, _line(text, m.start()), "sec-redos", "`new RegExp(\"%s\")`" % m.group(2)[:40])
    # --- tokens
    for m in re.finditer(r"\b(?:const|let|var)\s+(\w*(?:token|otp|password|secret|code|nonce|salt|resetCode|apiKey)\w*)\s*=\s*[^;\n]*Math\.random\(\)", code, re.I):
        add(out, rel, _line(text, m.start()), "sec-insecure-random", "`%s` made with Math.random()" % m.group(1))
    for m in re.finditer(r"\bjwt\.verify\s*\(", code):
        a, b = call_args(m.end() - 1)
        if re.search(r"""algorithms\s*:\s*\[\s*['"]none['"]""", raw(a, b), re.I):
            add(out, rel, _line(text, m.start()), "sec-jwt-unverified", "algorithms: ['none']", alarm=True)
    for m in re.finditer(r"""\b(?:verifyToken|authenticate|auth\w*|requireAuth|protect)\b[^\n]{0,80}\bjwt\.decode\s*\(""", code):
        add(out, rel, _line(text, m.start()), "sec-jwt-unverified", "jwt.decode() reads a token WITHOUT checking it - use jwt.verify()", alarm=True)
    for m in re.finditer(r"""createHash\s*\(\s*(['"])\s*\1\s*\)\s*\.\s*update\s*\(\s*([^)]*)\)""", code):
        algo = raw(m.start(1) + 1, m.start(1) + 1 + 10).split(m.group(1))[0] if False else text[m.start(1) + 1:text.find(m.group(1), m.start(1) + 1)]
        if algo.lower() in ("md5", "sha1", "sha256", "sha512") and re.search(r"(?i)pass(word)?|pwd", raw(m.start(2), m.end(2))):
            add(out, rel, _line(text, m.start()), "sec-weak-password-hash", "createHash('%s') on a password" % algo)
    for m in re.finditer(r"""localStorage\.setItem\s*\(\s*(['"])""", code):
        s = text[m.end():text.find(m.group(1), m.end())]
        if re.search(r"(?i)token|jwt|session|auth", s):
            add(out, rel, _line(text, m.start()), "sec-token-storage", "localStorage.setItem(\"%s\", ...)" % s[:30])
    for m in re.finditer(r"""\bres\.cookie\s*\(\s*(['"])""", code):
        name = text[m.end():text.find(m.group(1), m.end())]
        a, b = call_args(code.find("(", m.start()))
        if re.search(r"(?i)token|session|jwt|auth|sid", name) and not re.search(r"httpOnly\s*:\s*true", raw(a, b)):
            add(out, rel, _line(text, m.start()), "sec-cookie-flags", "res.cookie(\"%s\") without httpOnly" % name[:30])
    # --- secrets in code
    for m in re.finditer(r"""\b(?:const|let|var|export\s+const)\s+([A-Za-z_$][\w$]*)\s*(?::\s*\w+\s*)?=\s*(['"])([^'"\n]{8,200})\2""", text):
        if _secret_value(m.group(1), m.group(3)) and code[m.start():m.start() + 5] == text[m.start():m.start() + 5]:
            add(out, rel, _line(text, m.start()), "sec-hardcoded-password", "%s = \"%s...\"" % (m.group(1), m.group(3)[:3]))
    for m in re.finditer(r"""(?<![\w$.])([A-Za-z_$][\w$]*)\s*:\s*(['"])([^'"\n]{8,200})\2""", text):
        if _secret_value(m.group(1), m.group(3)) and code[m.start():m.start() + 3].strip() and not re.search(r"(?i)process\.env", m.group(3)):
            add(out, rel, _line(text, m.start()), "sec-hardcoded-password", "%s: \"%s...\"" % (m.group(1), m.group(3)[:3]))
    for m in re.finditer(r"""\bjwt\.sign\s*\([^,]+,\s*(['"])([^'"]{1,200})\1""", text):
        add(out, rel, _line(text, m.start()), "sec-hardcoded-password", "jwt.sign(..., \"%s...\") - the signing secret is in the code" % m.group(2)[:3])
    out += check_js_routes(rel, text, code)
    return out


def check_js_routes(rel, text, code):
    out = []
    # global protection: app.use(auth) / router.use(requireAuth) / middleware.ts with auth
    global_auth = bool(re.search(r"\b\w+\.use\s*\(\s*(['\"`][^'\"`]*['\"`]\s*,\s*)?[^)]*?(auth|jwt|passport|protect|session|verify|guard|clerk|requireUser|ensure)", code, re.I))
    for m in re.finditer(r"\b(app|router|[\w$]*[Rr]outer|api|server|r)\s*\.\s*(post|put|patch|delete|del)\s*\(\s*(['\"`])", code):
        start = code.find("(", m.start() + len(m.group(1)))
        end = _match_close(code, start)
        parts = _split_top(code[start + 1:end], ",")
        path_raw = text[start + 1 + parts[0][0]:start + 1 + parts[0][0] + len(parts[0][1])].strip().strip("'\"`")
        if len(parts) < 2 or PUBLIC_PATH.search(path_raw):
            continue
        middle = " ".join(text[start + 1 + o:start + 1 + o + len(p)] for o, p in parts[1:-1])
        handler = text[start + 1 + parts[-1][0]:end]
        if AUTH_WORDS.search(middle) or JS_AUTH_BODY.search(handler) or global_auth:
            continue
        add(out, rel, _line(text, m.start()), "api-open-route", "%s %s" % (m.group(2).upper().replace("DEL", "DELETE").replace("DELETEETE", "DELETE"), path_raw[:50]))
    # Next.js API routes: app/**/route.ts exporting POST/PUT/PATCH/DELETE, pages/api/*
    if re.search(r"(^|/)(app/.*route\.[jt]sx?|pages/api/.+\.[jt]sx?)$", rel):
        for m in re.finditer(r"export\s+(?:async\s+)?(?:function\s+|const\s+)(POST|PUT|PATCH|DELETE)\b", code):
            body = text[m.start():m.start() + 4000]
            if not JS_AUTH_BODY.search(body) and not PUBLIC_PATH.search(rel):
                add(out, rel, _line(text, m.start()), "api-open-route", "%s %s" % (m.group(1), "/" + re.sub(r"^.*?(app|pages)/|/route\.[jt]sx?$|\.[jt]sx?$", "", rel)))
        if "pages/api/" in rel and re.search(r"req\.method\s*===?\s*['\"](POST|PUT|PATCH|DELETE)", text) and not JS_AUTH_BODY.search(text) \
                and not PUBLIC_PATH.search(rel):
            mm = re.search(r"req\.method\s*===?\s*['\"](POST|PUT|PATCH|DELETE)", text)
            add(out, rel, _line(text, mm.start()), "api-open-route", "%s /api/%s" % (mm.group(1), rel.split("pages/api/")[-1].rsplit(".", 1)[0]))
    return out


# =================================================================================================== templates
TEMPLATE_EXT = ("ejs", "hbs", "handlebars", "mustache", "pug", "jade", "njk", "j2", "jinja", "jinja2", "twig", "html", "htm")


def check_template(rel, text, ext):
    """Places where a template prints a value WITHOUT escaping it - visitor text there becomes HTML/script."""
    out = []
    pats = []
    if ext == "ejs" or "<%-" in text:
        pats.append((r"<%-\s*(?!include\b|partial\b|body\b|block\b|-|\s*%>)([^%]{1,80})%>", "<%%- %s %%> prints it as HTML"))
    if ext in ("hbs", "handlebars", "mustache") or "{{{" in text:
        pats.append((r"\{\{\{\s*(?![>#/!])([^}]{1,60})\}\}\}", "{{{%s}}} prints it as HTML"))
    if ext in ("pug", "jade"):
        pats.append((r"!\{([^}]{1,60})\}|\w!=\s*([^\n]{1,60})", "!{%s} prints it as HTML"))
    if ext in ("html", "htm", "njk", "j2", "jinja", "jinja2", "twig"):
        pats.append((r"\{\{\s*([^{}|]{1,60}?)\s*\|\s*safe\s*\}\}", "{{ %s|safe }} prints it as HTML"))
        pats.append((r"\{%-?\s*autoescape\s+(off|false)\s*-?%\}", "{%% autoescape %s %%} switches escaping off"))
    for pat, msg in pats:
        for m in re.finditer(pat, text):
            val = next((g for g in m.groups() if g), "").strip()
            if re.search(r"(?i)csrf|tojson|json_script|\bjson\b|stylesheet|static|svg_icon|icon\b|^ ?include|^ ?body$|^ ?content_for", val):
                continue
            add(out, rel, _line(text, m.start()), "sec-xss", msg % val[:40])
    return out


# =================================================================================================== run
def check(root):
    project = dedupe.Project(root)
    found, n = [], 0
    next_mw = ""
    for cand in ("middleware.ts", "middleware.js", "src/middleware.ts", "src/middleware.js"):
        if cand in project.files:
            try:
                next_mw = (project.root / cand).read_text(encoding="utf-8")
            except OSError:
                pass
    for rel, size in project.files.items():
        if size > MAX_SIZE:
            continue
        ext = rel.rsplit(".", 1)[-1].lower() if "." in rel else ""
        base = posixpath.basename(rel)
        textual = ext in ("py", "js", "mjs", "cjs", "jsx", "ts", "tsx", "vue", "svelte", "json", "yml", "yaml", "toml", "ini", "cfg",
                          "conf", "env", "properties", "xml", "sh", "ps1", "rb", "php", "go", "java", "kt", "cs", "swift", "dart", "tf",
                          "html", "md", "txt") + TEMPLATE_EXT or base.startswith(".env") or base in ("Dockerfile", "docker-compose.yml")
        if not textual:
            continue
        try:
            text = (project.root / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        n += 1
        test = is_test(rel)
        try:
            if not test:
                found += check_secrets(rel, text)
            found += check_env_file(rel, text, project.root)
            if test or rel.endswith((".min.js", ".d.ts")):
                continue
            if ext == "py":
                found += check_python(rel, text)
            elif ext in TEMPLATE_EXT:
                found += check_template(rel, text, ext)
            elif ext in ("js", "mjs", "cjs", "jsx", "ts", "tsx", "vue", "svelte"):
                fs = check_js(rel, text)
                if next_mw and AUTH_WORDS.search(next_mw):
                    fs = [f for f in fs if f["kind"] != "api-open-route" or not re.search(r"(^|/)(app|pages)/", rel)]
                found += fs
        except Exception as e:                      # one odd file must never stop the whole check
            print("  (skipped %s: %s)" % (rel, type(e).__name__))
            continue
    keyed = {(f["file"], f["line"]) for f in found if f["kind"] == "sec-secret-key"}
    found = [f for f in found if not (f["kind"] == "sec-hardcoded-password" and (f["file"], f["line"]) in keyed)]
    uniq, seen = [], set()
    for f in sorted(found, key=lambda f: (f["file"], f["line"], f["kind"])):
        k = (f["file"], f["line"], f["kind"])
        if k not in seen:
            seen.add(k)
            uniq.append(f)
    return n, uniq


def main():
    ap = argparse.ArgumentParser(description="Arachnid security hunter (looks only)")
    ap.add_argument("root", nargs="?", default=str(HERE.parent))
    ap.add_argument("--out", default=str(HERE / "reports" / "security-report.json"))
    ap.add_argument("--rules", action="store_true")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    if a.rules:
        for k, (pile, title, why, todo) in RULES.items():
            print("%-24s [%s] %s\n%26s%s\n" % (k, pile, title, "", why))
        return 0
    print("Arachnid security hunter - looking at:", Path(a.root).resolve())
    n, found = check(a.root)
    ledger.reset("security")
    for f in found:
        pile, title, why, todo = RULES[f["kind"]]
        level = "alarm" if f["alarm"] or pile == "alarm" else pile
        what = title + (": " + f["detail"] if f["detail"] else "")
        print("\n  %s  line %d   [%s]  %s" % (f["file"], f["line"], level.upper(), f["kind"]))
        print("     %s" % what)
        if level == "alarm":
            why = why + " - and here the value comes straight from the visitor" if f["alarm"] and pile != "alarm" else why
        ledger.record("security", "idea" if level == "idea" else "found", {"alarm": "critical", "decision": "warning", "idea": "info"}[level],
                      f["file"], f["line"], what, why, todo, f["kind"])
    al = sum(1 for f in found if f["alarm"] or RULES[f["kind"]][0] == "alarm")
    print("\n---------------- Summary ----------------")
    print("Files read: %d    Security findings: %d   (alarms %d)" % (n, len(found), al))
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps({"root": str(Path(a.root).resolve()), "findings": found}, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
