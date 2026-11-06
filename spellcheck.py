"""Arachnid - Step 2: spelling checker and fixer.

What it does, in plain words:
  1. Walks through your project (like the crawler in Step 1).
  2. In every file it finds the places where a HUMAN wrote words:
        - comments                      // like this   # like this   /* like this */
        - messages and text in strings  console.log("Sucess!")   alt="a pictur"
        - page text in HTML, and all the words in README / .md / .txt files
     Code itself (names of variables, functions, keywords) is ignored.
  3. Checks each word against a big English dictionary and lists the mistakes
     with a suggested correction and a confidence level:
        high    - almost certainly a typo and the fix is obvious  -> safe to auto-fix
        medium  - looks like a typo but there are several possible fixes -> you decide
  4. With --fix it corrects the "high" ones for you, after saving a backup copy.

Run it (from the folder that contains this file):
    python spellcheck.py                 look only, change nothing
    python spellcheck.py --fix           fix the high-confidence typos (backup first)
    python spellcheck.py --undo          put the last fix back exactly as it was
    python spellcheck.py --add Acme Zyloq   teach it words that are NOT mistakes
    python spellcheck.py "C:\\other\\folder"  check a different folder
"""
import argparse
import bisect
import io
import json
import os
import pickle
import re
import shutil
import subprocess
import sys
import tempfile
import tokenize
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.append(str(HERE / "vendor"))  # bundled dictionary library (a pip-installed one wins)
try:
    from spellchecker import SpellChecker
except ImportError:
    sys.exit("The spelling library is missing. Run once:  pip install pyspellchecker")

try:  # older Windows consoles can choke on odd characters
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

# ----------------------------------------------------------------------------
# Which files and folders to look at
# ----------------------------------------------------------------------------
SKIP_DIRS = {
    "node_modules", ".git", "dist", "build", "__pycache__", ".wrangler", ".vite", ".next",
    ".cache", ".claude", "arachnid", ".venv", "venv", "spider-demo", ".cms-trash", ".cms-tmp",
    "perf-reports", "vendor",
}
SKIP_FILES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml"}
MAX_BYTES = 1_500_000

EXT_KIND = {}
for _e in "js mjs cjs jsx ts tsx frag vert glsl".split():
    EXT_KIND[_e] = "js"
for _e in "json jsonc".split():
    EXT_KIND[_e] = "json"
for _e in "css scss less".split():
    EXT_KIND[_e] = "css"
EXT_KIND["sql"] = "sql"
EXT_KIND["py"] = "py"
for _e in "html htm".split():
    EXT_KIND[_e] = "html"
for _e in "md txt".split():
    EXT_KIND[_e] = "prose"

# Words that are normal in code but missing from an everyday English dictionary.
TECH_WORDS = set("""
chiptune chiptunes synthwave lofi vaporwave 8bit 16bit
ok okay todo fixme etc eg ie vs aka faq asap info config configs repo repos demo demos docs doc util utils
init param params args arg func impl obj objs prev idx btn btns nav img imgs src dest tmp temp env dev prod
vars var const enum bool int str num msg msgs err errs req res resp cb ctx ref refs props prop opts opt sync
async regex regexp wasm webgl webgpu glsl gsap lottie threejs three tween tweens lerp easing parallax preload
prefetch debounce throttle viewport hover tooltip tooltips navbar sidebar dropdown modal modals popup popups
checkbox textarea iframe favicon seo sitemap ssr ssg spa pwa cms crud orm jwt oauth cors csrf xss ssl tls http
https url urls uri uuid api apis sdk cli gui ui ux ide css scss html json yaml toml svg png jpg jpeg gif webp
mp4 webm pdf csv xml sql nosql postgres postgresql mysql sqlite mongodb redis graphql supabase firebase
cloudflare wrangler netlify vercel github gitlab git npm npx pnpm yarn node nodejs deno vite webpack rollup
esbuild babel eslint prettier typescript javascript jsx tsx react vue svelte angular nuxt astro tailwind
bootstrap jquery lodash axios express docker kubernetes nginx linux ubuntu macos windows ios android chrome
firefox safari webkit chromium iphone ipad pixel samsung whatsapp linkedin instagram youtube twitter facebook
google gmail microsoft apple amazon aws gcp azure openai anthropic claude llm llms gpt ai ml rag gpu cpu ram
ssd fps dpi ppi px rem vh vw ms rgb rgba hsl hsla hex wifi bluetooth iot uart spi gpio pwm adc esp arduino
backend frontend fullstack webapp website websites webpage homepage login logout signup signin username
usernames email emails admin admins metadata timestamp timestamps filename filenames pathname dirname hostname
runtime codebase boilerplate refactor refactored refactoring deprecated polyfill polyfills transpile bundler
minify minified sourcemap hotfix rollback uptime workflow workflows roadmap changelog autocomplete autoplay
autofocus onclick onload keydown keyup keypress mousemove mouseenter mouseleave mousedown mouseup pointerdown
pointerup pointermove touchstart touchend touchmove scrollbar fullscreen offscreen canvas webcam gamepad shader
shaders uniform uniforms vertex fragment raycast raycaster quaternion vec2 vec3 vec4 mat4 bloom gizmo skybox
mipmap mipmaps wireframe esports esport leaderboard matchmaking namespace namespaces middleware callback
callbacks boolean enums lockfile monorepo semver localhost dom cdn dns tcp udp ip ssh vpn lan wan ftp smtp
stdin stdout stderr nostdin gzip mailto www cafe cafes predev postdev rasterize rasterise rasterization advect
advection shader glsl icosphere raycasting compositing blocklist allowlist devtools sourcemaps spritesheet spritesheets trackpad trackpads respawn upscale
upscaling downscale tileable overscan unstyled walkthrough starfield lifecycle chatbot chatbots inbox winget
unicode ascii utf base64 markdown readme gitignore dotenv sku seo og analytics lighthouse screenshot screenshots
thumbnail thumbnails tooltip lightbox carousel accordion tabs tab navbar footer header hero subtitle
""".split())

# ----------------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------------
def line_starts(text):
    starts = [0]
    for m in re.finditer("\n", text):
        starts.append(m.end())
    return starts


def osa(a, b):
    """Edit distance: how many single-letter changes turn a into b (a swap of 2 letters counts as 1)."""
    la, lb = len(a), len(b)
    d = [[0] * (lb + 1) for _ in range(la + 1)]
    for i in range(la + 1):
        d[i][0] = i
    for j in range(lb + 1):
        d[0][j] = j
    for i in range(1, la + 1):
        for j in range(1, lb + 1):
            c = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + c)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[la][lb]


def match_case(orig, new):
    if len(orig) > 1 and orig.isupper():
        return new.upper()
    if orig[:1].isupper():
        return new[:1].upper() + new[1:]
    return new


def blank_spans(text, spans):
    """Return text with the given (start, end) spans replaced by spaces (same length)."""
    out = list(text)
    for a, b in spans:
        out[a:b] = [" " if ch != "\n" else "\n" for ch in text[a:b]]
    return "".join(out)


# ----------------------------------------------------------------------------
# Finding the human-written parts of a file ("regions")
#   region = (kind, start, end, flag, source_text)
#   kind: comment | string | prose      flag: True if it is printed/logged as a message
# ----------------------------------------------------------------------------
MSG_CTX = re.compile(r"(?:console\.\w+|alert|confirm|prompt|print|\blog(?:ger)?\.\w+|\w*Error|warn|toast\.\w+|notify)\s*\(\s*$")
REGEX_KW = ("return", "typeof", "case", "in", "of", "void", "delete", "throw", "else", "do", "yield", "await")


def _template(s, i):
    """A `template string`: return the text chunks outside ${...} and the index after it."""
    n = len(s)
    j = i + 1
    start = j
    chunks = []
    while j < n:
        ch = s[j]
        if ch == "\\":
            j += 2
            continue
        if ch == "`":
            chunks.append((start, j))
            return chunks, j + 1
        if ch == "$" and s[j + 1:j + 2] == "{":
            chunks.append((start, j))
            depth = 1
            j += 2
            while j < n and depth:
                if s[j] == "{":
                    depth += 1
                elif s[j] == "}":
                    depth -= 1
                elif s[j] in "\"'":
                    q = s[j]
                    j += 1
                    while j < n and s[j] != q:
                        j += 2 if s[j] == "\\" else 1
                j += 1
            start = j
            continue
        j += 1
    chunks.append((start, n))
    return chunks, n


def scan_clike(s, lang, base=0):
    """Comments and strings in JS / JSON / CSS / SQL code. Offsets are shifted by `base`."""
    regs = []
    n = len(s)
    i = 0
    prev = ""
    while i < n:
        c = s[i]
        two = s[i:i + 2]
        if lang == "sql" and two == "--":
            j = s.find("\n", i)
            j = n if j < 0 else j
            regs.append(("comment", i + 2 + base, j + base, False))
            i = j
            continue
        if two == "//" and lang in ("js", "json"):
            j = s.find("\n", i)
            j = n if j < 0 else j
            regs.append(("comment", i + 2 + base, j + base, False))
            i = j
            continue
        if two == "/*":
            j = s.find("*/", i + 2)
            e = n if j < 0 else j
            regs.append(("comment", i + 2 + base, e + base, False))
            i = n if j < 0 else j + 2
            continue
        if lang == "css":
            i += 1
            continue
        if lang == "js" and c == "/":
            tail = s[max(0, i - 12):i].rstrip()
            is_re = (prev == "" or prev in "(,=:[!&|?{};+-*%<>~^" or any(tail.endswith(k) for k in REGEX_KW))
            if is_re:
                j = i + 1
                in_cls = False
                while j < n and s[j] != "\n":
                    ch = s[j]
                    if ch == "\\":
                        j += 2
                        continue
                    if ch == "[":
                        in_cls = True
                    elif ch == "]":
                        in_cls = False
                    elif ch == "/" and not in_cls:
                        break
                    j += 1
                i = j + 1
                while i < n and s[i].isalpha():
                    i += 1
                prev = ")"
                continue
        if (c in "\"'") or (c == "`" and lang == "js") or (lang == "sql" and c == "'"):
            if lang == "sql" and c == '"':
                i += 1
                continue
            if c == "`":
                chunks, j = _template(s, i)
                msg = bool(MSG_CTX.search(s[max(0, i - 60):i]))
                for a, b in chunks:
                    regs.append(("string", a + base, b + base, msg))
                i = j
                prev = "`"
                continue
            q = c
            j = i + 1
            if lang == "sql":
                while j < n:
                    if s[j] == "'":
                        if s[j + 1:j + 2] == "'":
                            j += 2
                            continue
                        break
                    j += 1
            else:
                while j < n and s[j] != q and s[j] != "\n":
                    j += 2 if s[j] == "\\" else 1
            end = min(j, n)
            k = j + 1
            while k < n and s[k] in " \t":
                k += 1
            is_key = lang in ("js", "json") and k < n and s[k] == ":" and prev in ("{", ",", "")
            if not is_key:
                regs.append(("string", i + 1 + base, end + base, bool(MSG_CTX.search(s[max(0, i - 60):i]))))
            i = j + 1 if (j < n and s[j] == q) else j
            prev = q
            continue
        if not c.isspace():
            prev = c
        i += 1
    return regs


def scan_py(text):
    regs = []
    starts = line_starts(text)
    fmid = getattr(tokenize, "FSTRING_MIDDLE", None)
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            a = starts[tok.start[0] - 1] + tok.start[1]
            if tok.type == tokenize.COMMENT:
                regs.append(("comment", a + 1, a + len(tok.string), False))
            elif tok.type == tokenize.STRING:
                m = re.match(r"([rRbBuUfF]*)('''|\"\"\"|'|\")", tok.string)
                if not m or "b" in m.group(1).lower():
                    continue
                q = m.group(2)
                msg = bool(re.search(r"(?:print|input|warn|\w*Error|Exception|logger?\.\w+|logging\.\w+)\s*\(\s*$", text[max(0, a - 50):a]))
                regs.append(("string", a + m.end(), a + len(tok.string) - len(q), msg))
            elif fmid is not None and tok.type == fmid:
                regs.append(("string", a, a + len(tok.string), True))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        pass
    return regs


HTML_ATTRS = re.compile(r"""\b(alt|title|placeholder|aria-label|aria-description|aria-placeholder|label|summary)\s*=\s*("([^"]*)"|'([^']*)')""", re.I)
META_NAME = re.compile(r"""\b(?:name|property)\s*=\s*["'](?:description|og:title|og:description|twitter:title|twitter:description)["']""", re.I)
META_CONTENT = re.compile(r"""\bcontent\s*=\s*("([^"]*)"|'([^']*)')""", re.I)


def scan_html(text):
    regs = []
    spans = []
    for m in re.finditer(r"<script\b([^>]*)>(.*?)</script\s*>", text, re.S | re.I):
        inner_start = m.start(2)
        if m.group(2).strip():
            lang = "json" if "json" in m.group(1).lower() else "js"
            for k, a, b, f in scan_clike(m.group(2), lang, inner_start):
                regs.append((k, a, b, f, text))
        spans.append((m.start(), m.end()))
    for m in re.finditer(r"<style\b[^>]*>(.*?)</style\s*>", text, re.S | re.I):
        for k, a, b, f in scan_clike(m.group(1), "css", m.start(1)):
            regs.append((k, a, b, f, text))
        spans.append((m.start(), m.end()))
    for m in re.finditer(r"<!--(.*?)-->", text, re.S):
        regs.append(("comment", m.start(1), m.end(1), False, text))
        spans.append((m.start(), m.end()))
    for m in re.finditer(r"<(?:pre|code)\b.*?</(?:pre|code)\s*>", text, re.S | re.I):
        spans.append((m.start(), m.end()))
    for m in re.finditer(r"<[a-zA-Z/][^>]*>", text):
        tag = m.group(0)
        for am in HTML_ATTRS.finditer(tag):
            g = 3 if am.group(3) is not None else 4
            regs.append(("string", m.start() + am.start(g), m.start() + am.end(g), True, text))
        if tag[:5].lower() == "<meta" and META_NAME.search(tag):
            cm = META_CONTENT.search(tag)
            if cm:
                g = 2 if cm.group(2) is not None else 3
                regs.append(("string", m.start() + cm.start(g), m.start() + cm.end(g), True, text))
        spans.append((m.start(), m.end()))
    masked = blank_spans(text, spans)
    regs.append(("prose", 0, len(text), False, masked))
    return regs


def scan_prose(text):
    spans = []
    for m in re.finditer(r"^(```|~~~).*?^\1[^\n]*$", text, re.M | re.S):
        spans.append((m.start(), m.end()))
    for m in re.finditer(r"\]\([^)\n]*\)", text):
        spans.append((m.start(), m.end()))
    for m in re.finditer(r"^\[[^\]\n]+\]:\s*\S+", text, re.M):
        spans.append((m.start(), m.end()))
    return [("prose", 0, len(text), False, blank_spans(text, spans))]


def get_regions(text, kind):
    if kind == "py":
        return [(k, a, b, f, text) for k, a, b, f in scan_py(text)]
    if kind in ("js", "json", "css", "sql"):
        return [(k, a, b, f, text) for k, a, b, f in scan_clike(text, kind)]
    if kind == "html":
        return scan_html(text)
    if kind == "prose":
        return scan_prose(text)
    return []


# ----------------------------------------------------------------------------
# From a region to a list of words worth checking
# ----------------------------------------------------------------------------
NOISE = re.compile(r"""
    https?://\S+ | www\.\S+ | [\w.+-]+@[\w-]+\.[\w.-]+
  | (?:[\w.-]+[/\\])+[\w.-]*
  | \b[\w-]+\.(?:com|org|net|io|dev|in|co|app|js|mjs|css|html?|json|md|png|jpe?g|svg|webp|gif|mp4|webm|txt|sql|py)\b
  | \#[0-9a-fA-F]{3,8}\b
  | \$\{[^}]*\} | \{\{.*?\}\} | %[sdif]\b | \{[^}\n]{0,40}\}
  | <[^>\n]{1,200}>
  | &\#?\w+;
  | `[^`\n]*`
  | @\w+
""", re.X)
CODE_LINE = re.compile(r"""(;\s*$)|(^\s*[{}]\s*$)|(=>)|(===|!==|&&|\|\|)|(\w\s*=\s*[\w'"\[({])|(^\s*(const|let|var|function|import|export)\s+\w+)""")
DIRECTIVE = re.compile(r"^\s*[*#/!-]*\s*(eslint|prettier|istanbul|jshint|tslint|global\b|@ts-|c8\b|#?region|#?endregion|sourceMappingURL|webpackChunkName|vite-ignore|noinspection)", re.I)
WORD = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
CLASSY = re.compile(r"[-:_./\\0-9()=#\[\]]")


def mask_noise(s):
    s = s.replace("\u2019", "'").replace("\u2018", "'").replace("\u02bc", "'")  # curly apostrophes
    return NOISE.sub(lambda m: " " * len(m.group()), s)


def prose_like(masked, allow_single):
    words = re.findall(r"[A-Za-z][A-Za-z']*", masked)
    if not words:
        return False
    toks = masked.split()
    if len(words) == 1 and len(toks) == 1:
        return allow_single
    classy = sum(1 for t in toks if CLASSY.search(t.strip(".,;:!?")))
    return len(words) >= 2 and classy * 2 <= len(toks)


def iter_words(src, start, end, kind, flag):
    seg = mask_noise(src[start:end])
    if kind == "comment":
        parts = []
        for line in seg.split("\n"):
            parts.append(" " * len(line) if (CODE_LINE.search(line) or DIRECTIVE.match(line)) else line)
        seg = "\n".join(parts)
    elif kind == "string":
        if not prose_like(seg, flag):
            return
    for m in WORD.finditer(seg):
        w = m.group()
        a, b = m.start(), m.end()
        prev = seg[a - 1] if a > 0 else ""
        nxt = seg[b] if b < len(seg) else ""
        if prev and (prev in "._/\\@#$&=%" or prev.isdigit()):
            continue
        if nxt and (nxt in "_/\\@=(" or nxt.isdigit()):
            continue
        if nxt == "." and b + 1 < len(seg) and seg[b + 1].isalnum():
            continue
        if re.search(r"[a-z][A-Z]", w) or (len(w) > 1 and w.isupper()):
            continue
        base = w
        if "'" in w:
            head, tail = w.split("'", 1)
            tl = tail.lower()
            if tl not in ("s", "t", "re", "ve", "ll", "d", "m"):
                continue
            base = head[:-1] if (tl == "t" and head.lower().endswith("n")) else head
        if len(base) < 3:
            continue
        j = a - 1
        while j >= 0 and seg[j] in " \t":
            j -= 1
        sentence_start = j < 0 or seg[j] in "\n.!?:*#>-(\"'["
        yield base, start + a, sentence_start


# ----------------------------------------------------------------------------
# The dictionary brain
# ----------------------------------------------------------------------------
class Engine:
    def __init__(self, allow):
        self.sp = SpellChecker()
        self.allow = set(allow)
        self.cache = {}
        self._dict = None

    BRITISH = (("our", "or"), ("ise", "ize"), ("isa", "iza"), ("ys", "yz"), ("tre", "ter"), ("ogue", "og"),
               ("ence", "ense"), ("lling", "ling"), ("lled", "led"), ("ller", "ler"), ("ae", "e"), ("oe", "e"),
               ("amme", "am"), ("ntring", "ntering"), ("grey", "gray"), ("ageing", "aging"), ("judgement", "judgment"))

    PREFIXES = ("", "un", "re", "pre", "over", "under", "non", "sub", "auto", "multi", "anti", "de", "dis", "mis",
                "co", "inter", "out", "up", "down", "semi", "micro", "macro", "mini", "meta", "hyper", "cross", "self")
    SUFFIXES = ("", "s", "es", "ed", "d", "ing", "er", "ers", "ly", "able", "ible", "ness", "less", "ful", "ment",
                "ments", "ize", "ise", "ized", "ised", "izing", "ising", "ization", "isation", "ity", "ery", "ings",
                "est", "y", "ish", "ist", "ists", "ism")
    VOWELS = "aeiou"

    def _in(self, w):
        return w in self.allow or w in self.sp

    def known(self, lw):
        if self._in(lw):
            return True
        for a, b in self.BRITISH:  # British spelling of a word we know (colour, organise, centre ...)
            if a in lw and self._in(lw.replace(a, b)):
                return True
        return self._morph(lw)

    def _morph(self, lw):
        """Word = known word + a common prefix and/or ending (upscaled, navigations, lossless, uncached ...)."""
        for p in self.PREFIXES:
            if p and not lw.startswith(p):
                continue
            rest = lw[len(p):]
            if len(rest) < 3:
                continue
            for suf in self.SUFFIXES:
                if suf and not rest.endswith(suf):
                    continue
                if not p and not suf:
                    continue
                stem = rest[:len(rest) - len(suf)] if suf else rest
                if len(stem) < 3:
                    continue
                # 'occur' + 'ed' must be 'occurred': a short consonant-vowel-consonant stem needs a doubled letter
                if suf[:1] in ("e", "i") and len(stem) <= 6 and len(stem) >= 3 and stem[-1] not in self.VOWELS + "wxy" \
                        and stem[-2] in self.VOWELS and stem[-3] not in self.VOWELS:
                    continue
                options = [stem, stem + "e"]
                if (len(stem) > 3 and stem[-1] == stem[-2] and stem[-1] not in self.VOWELS
                        and stem[-3] in self.VOWELS and stem[-4] not in self.VOWELS):
                    options.append(stem[:-1])        # gzipped -> gzip (stops learnning -> learn)
                if stem.endswith("i"):
                    options.append(stem[:-1] + "y")  # queries -> query
                if any(self._in(o) for o in options):
                    return True
        return False

        for i in range(3, len(lw) - 2):
            if self._in(lw[:i]) and self._in(lw[i:]):
                return True
        return False

    @staticmethod
    def _vowel_slip(a, b):
        """Same word except one vowel typed as another (avarage / average, definately / definitely)."""
        diff = [(x, y) for x, y in zip(a, b) if x != y]
        return len(a) == len(b) and len(diff) == 1 and diff[0][0] in "aeiou" and diff[0][1] in "aeiou"

    @staticmethod
    def _shape(a, b, d):
        """What kind of slip turns the typo `a` into the real word `b`?"""
        if d == 1 and len(a) == len(b):
            for i in range(len(a) - 1):
                if a[:i] + a[i + 1] + a[i] + a[i + 2:] == b:
                    return "swap"      # two neighbouring letters swapped (teh -> the)
            return "sub"               # one wrong letter
        if d == 1:
            return "missing"           # a letter missing or added
        return "other"

    _LETTERS = "abcdefghijklmnopqrstuvwxyz"

    @classmethod
    def _edits1(cls, w):
        sp = [(w[:i], w[i:]) for i in range(len(w) + 1)]
        out = {a + b[1:] for a, b in sp if b}
        out |= {a + b[1] + b[0] + b[2:] for a, b in sp if len(b) > 1}
        out |= {a + c + b[1:] for a, b in sp if b for c in cls._LETTERS}
        out |= {a + c + b for a, b in sp for c in cls._LETTERS}
        return out

    def _fast_candidates(self, lw):
        """Same idea as the library's candidates() but ~30x faster: plain set lookups, no per-word cleanup."""
        if self._dict is None:
            self._dict = self.sp.word_frequency.dictionary
        D = self._dict
        e1 = self._edits1(lw)
        found = {w for w in e1 if w in D}
        if found:
            return found
        if len(lw) > 12:
            return None
        return {w for e in e1 for w in self._edits1(e) if w in D} or None

    def suggest(self, lw):
        if lw in self.cache:
            return self.cache[lw]
        out = []
        cands = None
        if len(lw) <= 24:
            cands = self._fast_candidates(lw)
        if cands and lw not in cands:
            limit = 1 if len(lw) <= 4 else 2
            scored = []
            for c in cands:
                if not c.isalpha():
                    continue
                d = osa(lw, c)
                if d <= limit:
                    kind = self._shape(lw, c, d)
                    weight = {"swap": 6.0, "missing": 3.0, "sub": 1.0}.get(kind, 0.4)
                    scored.append((c, d, self.sp.word_usage_frequency(c) * weight, kind))
            scored.sort(key=lambda t: (t[1], -t[2]))
            if scored:
                top = scored[0]
                if len(lw) <= 3 and top[3] != "swap":
                    scored = []        # very short words: only the classic swap (teh -> the) is trustworthy
            if scored:
                top = scored[0]
                common = self.sp.word_usage_frequency(top[0]) >= 3e-7   # the fix must be an everyday word
                shape_ok = top[3] in ("swap", "missing") or (
                    top[3] == "sub" and len(lw) >= 5 and self._vowel_slip(lw, top[0]))
                ratio = 1e9 if len(scored) == 1 else top[2] / max(scored[1][2], 1e-12)
                conf = "high" if (top[1] == 1 and shape_ok and common and ratio >= 8) else "medium"
                out = [conf, [c[0] for c in scored[:4]]]
        self.cache[lw] = out
        return out


def load_custom(root=None):
    """Words that are NOT mistakes.  Arachnid's own list (custom_words.txt) plus, for a project, its own words:
    a file .arachnid-words.txt in the project folder, or "words": [...] in arachnid.config.json.  The project's
    words travel with the project, so they also work in the GitHub check."""
    words = set()
    files = [HERE / "custom_words.txt"] + ([Path(root) / ".arachnid-words.txt"] if root else [])
    for f in files:
        try:
            for line in f.read_text(encoding="utf-8-sig").splitlines():
                line = line.split("#", 1)[0].strip().lower()
                if line:
                    words.add(line)
        except OSError:
            pass
    if root:
        try:
            import json as _json
            cfg = _json.loads((Path(root) / "arachnid.config.json").read_text(encoding="utf-8-sig"))
            words.update(str(w).strip().lower() for w in cfg.get("words", []) if str(w).strip())
        except (OSError, ValueError, AttributeError):
            pass
    return words


# ----------------------------------------------------------------------------
# Scanning the project
# ----------------------------------------------------------------------------
def collect_files(root):
    """Fast walk (os.scandir reuses the folder listing, so no extra stat call per file).
    Yields (path, mtime_ns, size)."""
    stack = [str(root)]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError:
            continue
        for e in entries:
            name = e.name
            try:
                if e.is_dir(follow_symlinks=False):
                    if name not in SKIP_DIRS:
                        stack.append(e.path)
                    continue
                low = name.lower()
                if low in SKIP_FILES or low.startswith(".env") or low.endswith((".min.js", ".min.css", ".map")):
                    continue
                ext = low.rsplit(".", 1)[-1] if "." in low else ""
                if ext not in EXT_KIND:
                    continue
                st = e.stat()
            except OSError:
                continue
            if st.st_size > MAX_BYTES:
                continue
            yield Path(e.path), st.st_mtime_ns, st.st_size


def ident_words(text, regs, kind):
    """Words used as names in the code. Used to learn the project's own vocabulary."""
    if kind not in ("js", "css", "py", "sql"):
        return Counter()
    spans = [(a, b) for k, a, b, f, s in regs]
    code = blank_spans(text, spans)
    c = Counter()
    for ident in re.findall(r"[A-Za-z_$][A-Za-z0-9_$]*", code):
        for part in re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+", ident):
            if len(part) >= 3:
                c[part.lower()] += 1
    return c


def scan_file(path, root):
    ext = path.name.lower().rsplit(".", 1)[-1]
    kind = EXT_KIND[ext]
    try:
        with open(path, encoding="utf-8", newline="") as fh:
            text = fh.read()
    except (UnicodeDecodeError, OSError):
        return None
    regs = get_regions(text, kind)
    tokens = []
    seen = set()
    for k, a, b, flag, src in regs:
        for w, off, ss in iter_words(src, a, b, k, flag):
            if off in seen:
                continue
            seen.add(off)
            tokens.append((w, off, ss, k))
    return {"rel": path.relative_to(root).as_posix(), "path": str(path), "tokens": tokens,
            "idents": ident_words(text, regs, kind), "regions": len(regs)}


CACHE_VERSION = "2"


def _cache_path():
    return Path(__file__).resolve().parent / "reports" / ".spell-cache.pickle"


def _load_cache():
    try:
        with open(_cache_path(), "rb") as fh:
            c = pickle.load(fh)
        if c.get("version") == CACHE_VERSION and c.get("script") == _script_stamp():
            return c["files"]
    except Exception:
        pass
    return {}


def _script_stamp():
    try:
        st = os.stat(__file__)
        return (st.st_size, st.st_mtime_ns)
    except OSError:
        return None


def _read_text(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read()


def run_check(root, extra_allow=()):
    """Files that did not change since the last run are not re-read (cache), and the rest are read in parallel."""
    root = Path(root)
    listing = list(collect_files(root))
    files = [t[0] for t in listing]
    old = _load_cache()
    new_cache = {}
    scans = []
    todo = []
    for p, mt, sz in listing:
        key = str(p)
        hit = old.get(key)
        if hit and hit[0] == mt and hit[1] == sz:
            new_cache[key] = hit
            if hit[2]:
                scans.append(hit[2])
        else:
            todo.append((p, mt, sz))

    def work(t):
        return t, scan_file(t[0], root)
    if todo:
        with ThreadPoolExecutor(max_workers=min(32, (os.cpu_count() or 4) * 4)) as ex:
            for (p, mt, sz), res in ex.map(work, todo):
                new_cache[str(p)] = (mt, sz, res)
                if res:
                    scans.append(res)
    scans.sort(key=lambda s: s["rel"])
    try:
        cp = _cache_path()
        cp.parent.mkdir(parents=True, exist_ok=True)
        with open(cp, "wb") as fh:
            pickle.dump({"version": CACHE_VERSION, "script": _script_stamp(), "files": new_cache}, fh, protocol=4)
    except Exception:
        pass
    ident_total = Counter()
    for s in scans:
        ident_total.update(s["idents"])
    vocab = {w for w, c in ident_total.items() if c >= 3}
    eng = Engine(TECH_WORDS | load_custom(root) | vocab | set(extra_allow))

    unknown_counts = Counter()
    unknown_files = defaultdict(set)
    for s in scans:
        for w, off, ss, k in s["tokens"]:
            lw = w.lower()
            if not eng.known(lw):
                unknown_counts[lw] += 1
                unknown_files[lw].add(s["rel"])

    findings = []
    words_checked = 0
    for s in scans:
        text = None
        starts = None
        for w, off, ss, k in s["tokens"]:
            words_checked += 1
            lw = w.lower()
            if eng.known(lw):
                continue
            if unknown_counts[lw] >= 4:
                continue  # used many times: probably a real word of this project
            sug = eng.suggest(lw)
            if not sug:
                continue
            conf, alts = sug
            if w[0].isupper():
                # Capitalised words are often names of people or companies - only mention a very clear typo.
                if conf != "high":
                    continue
                if not ss:
                    conf = "medium"   # mid-sentence capital: report it, but never auto-fix
            if text is None:
                try:
                    text = _read_text(s["path"])
                except OSError:
                    break
                starts = line_starts(text)
            ln = bisect.bisect_right(starts, off)
            col = off - starts[ln - 1]
            line_text = text[starts[ln - 1]:starts[ln] - 1 if ln < len(starts) else len(text)].rstrip("\r\n")
            lo = max(0, col - 35)
            snippet = line_text[lo:col + len(w) + 35].strip()
            findings.append({
                "file": s["rel"], "line": ln, "col": col + 1, "word": w,
                "suggestion": match_case(w, alts[0]), "alternatives": [match_case(w, a) for a in alts],
                "confidence": conf, "where": k, "context": snippet, "offset": off,
            })
    unknown = [{"word": w, "count": c, "files": sorted(unknown_files[w])[:3]}
               for w, c in unknown_counts.most_common()
               if not eng.suggest(w) or unknown_counts[w] >= 4]
    return files, scans, findings, unknown, words_checked


# ----------------------------------------------------------------------------
# Fixing, backups, undo
# ----------------------------------------------------------------------------
def syntax_ok(path):
    """After a fix, make sure the file still 'parses'. Returns True/False, or None if we cannot tell."""
    ext = path.name.lower().rsplit(".", 1)[-1]
    try:
        if ext == "json":
            with open(path, encoding="utf-8") as fh:
                json.load(fh)
            return True
        if ext == "py":
            with open(path, encoding="utf-8") as fh:
                compile(fh.read(), str(path), "exec")
            return True
        if ext in ("js", "mjs", "cjs") and shutil.which("node"):
            tmp = Path(tempfile.mkdtemp()) / "check.mjs"
            shutil.copyfile(path, tmp)
            r = subprocess.run(["node", "--check", str(tmp)], capture_output=True, timeout=60)
            shutil.rmtree(tmp.parent, ignore_errors=True)
            return r.returncode == 0
    except (ValueError, SyntaxError, subprocess.SubprocessError, OSError):
        return False
    return None


LAST_APPLY = {"changed": [], "held": []}


def apply_fixes(root, findings, level, gate=False):
    LAST_APPLY["changed"], LAST_APPLY["held"] = [], []
    want = {"high": ("high",), "medium": ("high", "medium")}[level]
    by_file = defaultdict(list)
    for f in findings:
        if f["confidence"] in want:
            by_file[f["file"]].append(f)
    if not by_file:
        print("Nothing to fix at this confidence level.")
        return
    # CODE IS CRITICAL: every file that is not plain documentation (.md/.txt/.rst) must pass the safety gate.
    # Only documentation-only files may skip it (unless --gate asks for everything to be proven).
    DOC_ONLY = (".md", ".txt", ".rst")
    gated = {rel: items for rel, items in by_file.items() if gate or not rel.lower().endswith(DOC_ONLY)}
    if gated:
        from gate import guard_units
        texts_ = {}
        for rel, items in gated.items():
            with open(root / rel, encoding="utf-8", newline="") as fh:
                t = fh.read()
            for f in sorted(items, key=lambda f: -f["offset"]):
                a_, w_ = f["offset"], f["word"]
                if t[a_:a_ + len(w_)] == w_:
                    t = t[:a_] + f["suggestion"] + t[a_ + len(w_):]
            texts_[rel] = t
        def _apply_units(copy, units):
            for rel_ in units:
                with open(Path(copy) / rel_, "w", encoding="utf-8", newline="") as fh:
                    fh.write(texts_[rel_])
        from gate import proven_same_program
        originals = {}
        for rel in gated:
            with open(root / rel, encoding="utf-8", newline="") as fh:
                originals[rel] = fh.read()
        proven = proven_same_program(root, [(rel, originals[rel], texts_[rel]) for rel in gated])
        if proven:
            print("Proved identical (only comments/spacing changed - the program itself is the same): %s" % ", ".join(sorted(proven)))
        to_gate = [rel for rel in gated if rel not in proven]
        passed, failed, notes = list(proven), [], []
        if to_gate:
            print("Safety gate: checking that the spelling fixes change nothing in the product (all code files) ...")
            p2, failed, notes = guard_units(root, to_gate, _apply_units)
            passed += p2
        for n in notes:
            print("   " + n)
        for u, why in failed:
            print(f"   HELD BACK for the owner: {u} - {why}")
        LAST_APPLY["held"] = [u for u, _w in failed]
        by_file = {k: v for k, v in by_file.items() if k in passed or k not in gated}
        if not by_file:
            return
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    bdir = HERE / "backups" / stamp
    changed, reverted, count = [], [], 0
    log = []
    for rel, items in by_file.items():
        path = root / rel
        with open(path, encoding="utf-8", newline="") as fh:
            orig = fh.read()
        ok_before = syntax_ok(path)
        new = orig
        applied = []
        for f in sorted(items, key=lambda f: -f["offset"]):
            a, w = f["offset"], f["word"]
            if new[a:a + len(w)] != w:
                continue
            new = new[:a] + f["suggestion"] + new[a + len(w):]
            applied.append(f)
        if new == orig:
            continue
        if not os.access(path, os.W_OK):           # read-only: left exactly as it is (permissions never changed)
            print("   SKIPPED %s - the file is read-only, so it is left as it is" % rel)
            continue
        dest = bdir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(new)
        if ok_before is not False and syntax_ok(path) is False:
            shutil.copy2(dest, path)
            reverted.append(rel)
            continue
        changed.append(rel)
        count += len(applied)
        for f in sorted(applied, key=lambda f: f["offset"]):
            log.append({"file": rel, "line": f["line"], "from": f["word"], "to": f["suggestion"]})
    if changed:
        (bdir / "manifest.json").write_text(json.dumps({"root": str(root), "files": changed, "fixes": log}, indent=2), encoding="utf-8")
    LAST_APPLY["changed"] = list(changed)
    print(f"\nFixed {count} spelling mistakes in {len(changed)} files.")
    for rel in changed:
        n = sum(1 for x in log if x["file"] == rel)
        print(f"  {rel}  ({n})")
    if reverted:
        print("\nUndone automatically (the fix broke the file's syntax, so I put it back):")
        for rel in reverted:
            print("  " + rel)
    if changed:
        print(f"\nBackup of the originals: {bdir}\nTo put everything back:  python spellcheck.py --undo")


def undo():
    base = HERE / "backups"
    sessions = sorted(p for p in base.glob("*") if (p / "manifest.json").exists()) if base.exists() else []
    if not sessions:
        sys.exit("No backups found - nothing to undo.")
    last = sessions[-1]
    man = json.loads((last / "manifest.json").read_text(encoding="utf-8"))
    root = Path(man["root"])
    for rel in man["files"]:
        shutil.copy2(last / rel, root / rel)
    (last / "manifest.json").rename(last / "manifest.undone.json")
    print(f"Restored {len(man['files'])} files from backup {last.name}.")


# ----------------------------------------------------------------------------
# Command line
# ----------------------------------------------------------------------------
def add_words(words):
    f = HERE / "custom_words.txt"
    if not f.exists():
        f.write_text("# Words that are NOT mistakes (names, slang, product terms). One per line.\n", encoding="utf-8")
    have = load_custom()
    new = [w.lower() for w in words if w.lower() not in have]
    with open(f, "a", encoding="utf-8") as fh:
        for w in new:
            fh.write(w + "\n")
    print(f"Added {len(new)} word(s) to {f.name}: {', '.join(new) if new else '(already there)'}")


def main():
    ap = argparse.ArgumentParser(description="Arachnid spelling checker - Step 2")
    ap.add_argument("root", nargs="?", default=str(HERE.parent), help="folder to check")
    ap.add_argument("--fix", action="store_true", help="correct the high-confidence typos (makes a backup first)")
    ap.add_argument("--gate", action="store_true", help="also gate documentation files (code files are ALWAYS gated)")
    ap.add_argument("--level", choices=["high", "medium"], default="high", help="with --fix: how sure must it be")
    ap.add_argument("--limit", type=int, default=150, help="how many findings to print")
    ap.add_argument("--out", default=str(HERE / "reports" / "spelling-report.json"))
    ap.add_argument("--add", nargs="+", metavar="WORD", help="add words that are not mistakes, then exit")
    ap.add_argument("--undo", action="store_true", help="restore the files changed by the last --fix")
    args = ap.parse_args()

    if args.add:
        return add_words(args.add)
    if args.undo:
        return undo()
    root = Path(args.root).resolve()
    if not root.is_dir():
        sys.exit(f"Not a folder: {root}")

    print(f"Checking spelling in: {root}\n")
    files, scans, findings, unknown, checked = run_check(root)
    high = [f for f in findings if f["confidence"] == "high"]
    med = [f for f in findings if f["confidence"] == "medium"]

    by_file = defaultdict(list)
    for f in findings:
        by_file[f["file"]].append(f)
    printed = 0
    for rel in sorted(by_file):
        if printed >= args.limit:
            break
        print(rel)
        for f in sorted(by_file[rel], key=lambda f: f["offset"]):
            if printed >= args.limit:
                break
            alt = "" if len(f["alternatives"]) < 2 else "   (or: " + ", ".join(f["alternatives"][1:3]) + ")"
            print(f"  line {f['line']:<5} {f['word']} -> {f['suggestion']}   [{f['confidence']}] in {f['where']}{alt}")
            print(f"            {f['context']}")
            printed += 1
        print()
    if len(findings) > printed:
        print(f"... and {len(findings) - printed} more (all of them are in the report file)\n")

    print("---------------- Summary ----------------")
    print(f"Files checked: {len(scans)}    Words checked: {checked:,}")
    print(f"Spelling mistakes found: {len(findings)}   (high confidence: {len(high)}, medium: {len(med)})")
    if unknown:
        print("\nWords it did not recognise and could not correct (names? slang? project terms?):")
        print("  " + ", ".join(f"{u['word']} x{u['count']}" for u in unknown[:25]))
        print("If any of these are fine, teach them:  python spellcheck.py --add word1 word2")

    report = {
        "checked_at": datetime.now().isoformat(timespec="seconds"), "root": str(root),
        "totals": {"files": len(scans), "words_checked": checked, "mistakes": len(findings),
                   "high": len(high), "medium": len(med)},
        "findings": [{k: v for k, v in f.items()} for f in findings],
        "unrecognised_words": unknown,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nFull report saved to: {out}")

    if args.fix:
        apply_fixes(root, findings, args.level, gate=args.gate)
    try:
        import ledger
        ledger.reset("spelling")
        for f in findings:
            what = "%s -> %s (%s)" % (f["word"], f["suggestion"], f["where"])
            if args.fix and f["file"] in LAST_APPLY["changed"]:
                ledger.record("spelling", "fixed", "info", f["file"], f["line"], what, kind="spelling")
            elif args.fix and f["file"] in LAST_APPLY["held"]:
                ledger.record("spelling", "held", "warning", f["file"], f["line"], what + " (held back by the safety gate)",
                              "the change could affect behaviour", "check and fix by hand if it is a real typo", "spelling")
            elif f["confidence"] == "high" or (f["confidence"] == "medium" and args.level == "medium"):
                ledger.record("spelling", "fixable", "info", f["file"], f["line"], what, kind="spelling")
            else:
                ledger.record("spelling", "decision", "info", f["file"], f["line"], what, "", "unsure word; ignore or teach it with --add", "spelling")
    except Exception:
        pass
    if args.fix:
        pass
    elif high:
        print(f"\n{len(high)} of them are safe to fix automatically:  python spellcheck.py --fix")


if __name__ == "__main__":
    main()
