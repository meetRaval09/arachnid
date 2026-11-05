#!/usr/bin/env python3
"""
ARACHNID - Step 7: INSIGHTS  (suggestions only - this tool NEVER changes a file)
===============================================================================
    python insights.py [folder] [--out reports/insights-report.json] [--all]

It looks for five kinds of things and, for each, says HOW SURE it is:
  leak         code that opens / starts / creates something and never closes / stops / frees it
  memory       code that loads a lot into memory at once, or files that are very heavy
  dead-code    functions/classes whose name is never mentioned again anywhere, and lines after a `return`
  unused-file  files that nothing in the project mentions
  dependency   libraries you declare but never use, and heavy libraries that have lighter alternatives

Works on any language:
  * Rule packs know the typical leak patterns of JavaScript/TypeScript (incl. React and Three.js/WebGL), Python, Java,
    C/C++, C#, Go, Rust, PHP and Ruby.  Adding a language = adding a few lines to the table at the top of this file.
  * The language-neutral checks (names never mentioned again, unreachable lines, unreferenced files, heavy files,
    dependency lists) work for ANY text-based language, even ones without a rule pack.

How to read the confidence labels
  high    very likely a real finding
  medium  looks real, a human should glance at it
  low     a hint only - often fine on purpose (for example something that must live as long as the page)
Nothing here is proven.  Only running the program and measuring it can PROVE a leak; this tool reads code.
"""
import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKIP_DIRS = {"node_modules", ".git", "dist", "build", "__pycache__", ".venv", "venv", "env", "site-packages", ".next",
             ".cache", "arachnid", "backups", "reports", "coverage", ".mypy_cache", ".pytest_cache", "target",
             ".gradle", ".idea", ".vscode", "vendor", "Pods", ".wrangler", ".vite", "bin", "obj"}
MAX_TEXT = 1_500_000

FAMILY = {"py": "py", "js": "js", "jsx": "js", "mjs": "js", "cjs": "js", "ts": "js", "tsx": "js", "vue": "js", "svelte": "js",
          "java": "java", "kt": "kotlin", "kts": "kotlin", "scala": "java", "cs": "cs", "go": "go", "rs": "rust",
          "php": "php", "rb": "ruby", "swift": "swift", "c": "c", "h": "c", "cpp": "c", "cc": "c", "hpp": "c", "cxx": "c",
          "dart": "java", "lua": "lua", "sh": "sh", "pl": "perl", "r": "r", "m": "c"}
CODE_EXT = set(FAMILY)
TEXT_EXT = CODE_EXT | {"html", "htm", "css", "scss", "less", "json", "md", "txt", "yml", "yaml", "toml", "xml", "cfg", "ini",
                       "gradle", "sql", "env", "conf", "mdx", "svg", "csproj", "lock"}
ASSET_EXT = {"png", "jpg", "jpeg", "gif", "webp", "avif", "svg", "ico", "bmp", "mp4", "webm", "mov", "mp3", "wav", "ogg",
             "woff", "woff2", "ttf", "otf", "glb", "gltf", "bin", "obj", "hdr", "pdf"}
IMAGE_EXT = {"png", "jpg", "jpeg", "gif", "bmp", "tif", "tiff"}
SKIP_TEXT_NAMES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Cargo.lock", "composer.lock", "go.sum"}

# ---------------------------------------------------------------------------------------------------------------
# RULE PACKS.  To teach Arachnid a new language or pattern, add a row.
#   open  : something is started / opened / created
#   close : the thing that undoes it.  mode "none" = flag when the file never has a close; "gap" = flag when opens exceed closes by 2+
#   skip_if: if the file matches this, the rule does not apply (for example smart pointers in C++)
# ---------------------------------------------------------------------------------------------------------------
JS = {"js"}
LEAK_RULES = [
    dict(id="listener-never-removed", fam=JS, open=r"\.addEventListener\(", close=r"\.removeEventListener\(", mode="none", conf="low",
         msg="adds event listeners but this file never removes one",
         tip="if the element or component can disappear before the page does, remove the listener when it goes away (otherwise it stays in memory)"),
    dict(id="timer-never-stopped", fam=JS, open=r"\bsetInterval\(", close=r"\bclearInterval\(", mode="none", conf="medium",
         msg="starts a repeating timer (setInterval) that this file never stops",
         tip="call clearInterval when the timer is no longer needed, otherwise it runs forever and keeps everything it uses alive"),
    dict(id="animation-loop-never-cancelled", fam=JS, open=r"\brequestAnimationFrame\(", close=r"\bcancelAnimationFrame\(", mode="none", conf="low",
         msg="runs an animation loop (requestAnimationFrame) that is never cancelled",
         tip="fine for a loop that lives as long as the page; if the animation can be removed, cancel it so it stops using CPU and memory"),
    dict(id="webgl-objects-never-freed", fam=JS, conf="medium", mode="none",
         open=r"new\s+(?:THREE\.)?(?:\w*Geometry|\w*Material|WebGLRenderTarget|CanvasTexture|VideoTexture|DataTexture|Texture)\s*\(",
         close=r"\.dispose\(",
         msg="creates 3D objects (geometry/material/texture) but this file never calls .dispose()",
         tip="3D objects live in graphics memory, which the browser does NOT clean automatically; call .dispose() when a scene or object is removed"),
    dict(id="webgl-raw-never-deleted", fam=JS, conf="medium", mode="none",
         open=r"\.create(?:Texture|Buffer|Framebuffer|Program|Shader|Renderbuffer)\(", close=r"\.delete(?:Texture|Buffer|Framebuffer|Program|Shader|Renderbuffer)\(",
         msg="creates WebGL resources but never deletes any", tip="free graphics-memory objects with the matching delete... call when they are no longer needed"),
    dict(id="observer-never-disconnected", fam=JS, conf="low", mode="none",
         open=r"new\s+(?:IntersectionObserver|ResizeObserver|MutationObserver)\(", close=r"\.(?:disconnect|unobserve)\(",
         msg="creates an observer that is never disconnected", tip="disconnect it when the thing it watches goes away"),
    dict(id="blob-url-never-revoked", fam=JS, conf="medium", mode="none",
         open=r"URL\.createObjectURL\(", close=r"URL\.revokeObjectURL\(",
         msg="creates object URLs (they hold the whole file in memory) and never revokes them", tip="call URL.revokeObjectURL(url) when the URL is no longer needed"),
    dict(id="socket-never-closed", fam=JS, conf="low", mode="none",
         open=r"new\s+(?:WebSocket|EventSource)\(", close=r"\.close\(",
         msg="opens a connection that this file never closes", tip="close it when it is no longer needed"),
    dict(id="file-never-closed", fam={"py"}, conf="medium", mode="none", flags=re.M,
         open=r"^[ \t]*[\w.\[\]]+[ \t]*=[ \t]*(?:io\.|codecs\.)?open\(", close=r"\.close\(\)",
         msg="opens a file into a variable and never closes it",
         tip="use `with open(...) as f:` - the file is then closed automatically, even if an error happens"),
    dict(id="unbounded-cache", fam={"py"}, conf="medium", mode="none", open=r"lru_cache\(\s*maxsize\s*=\s*None\s*\)|@(?:functools\.)?cache\b", close=r"cache_clear\(",
         msg="uses a cache with no size limit", tip="give it a limit, e.g. @lru_cache(maxsize=1024), or it grows for as long as the program runs"),
    dict(id="resource-never-closed", fam={"java"}, conf="medium", mode="none",
         open=r"new\s+(?:[\w.]+\.)?(?:FileInputStream|FileOutputStream|FileReader|FileWriter|BufferedReader|Scanner|Socket|ServerSocket)\b|DriverManager\.getConnection\(",
         close=r"\.close\(\)|\btry\s*\(",
         msg="opens streams/connections and never closes them", tip="use try-with-resources: try (var x = ...) { } closes it automatically"),
    dict(id="threadlocal-never-removed", fam={"java"}, conf="medium", mode="none", open=r"new\s+ThreadLocal\b|ThreadLocal\.withInitial\(", close=r"\.remove\(\)",
         msg="uses ThreadLocal but never calls remove()", tip="in servers with thread pools, call remove() when done or values pile up"),
    dict(id="static-collection-grows", fam={"java"}, conf="low", mode="none", open=r"static\s+(?:final\s+)?(?:List|Map|Set|Queue|Deque|ArrayList|HashMap|HashSet)<",
         close=r"\.(?:remove|clear|poll|evict|removeIf)\(",
         msg="has a static collection but never removes anything from it", tip="a static collection lives for the whole run; if it only grows, memory only grows"),
    dict(id="malloc-without-free", fam={"c"}, conf="medium", mode="gap", open=r"\b(?:malloc|calloc|realloc|strdup)\s*\(", close=r"\bfree\s*\(",
         msg="allocates memory more often than it frees it", tip="every malloc/calloc needs exactly one free on every path; consider a tool like Valgrind or AddressSanitizer to confirm"),
    dict(id="new-without-delete", fam={"c"}, conf="medium", mode="gap", open=r"\bnew\s+[A-Za-z_][\w:<>]*\s*[\(\[{;]", close=r"\bdelete\b",
         skip_if=r"unique_ptr|shared_ptr|make_unique|make_shared|QObject|std::vector<std::unique",
         msg="uses `new` more often than `delete`", tip="prefer std::unique_ptr / std::make_unique so memory is freed automatically"),
    dict(id="fopen-without-fclose", fam={"c"}, conf="medium", mode="gap", open=r"\bfopen\s*\(", close=r"\bfclose\s*\(",
         msg="opens files with fopen and closes fewer", tip="close every file you open"),
    dict(id="go-resource-not-closed", fam={"go"}, conf="medium", mode="none",
         open=r"\bos\.Open\(|\bhttp\.(?:Get|Post|Head)\(|\bsql\.Open\(|\.Query\(", close=r"\.Close\(\)",
         msg="opens files/responses/connections and never calls Close()", tip="add `defer x.Close()` right after opening"),
    dict(id="go-ticker-not-stopped", fam={"go"}, conf="medium", mode="none", open=r"time\.NewTicker\(", close=r"\.Stop\(\)",
         msg="creates a ticker that is never stopped", tip="add `defer ticker.Stop()`"),
    dict(id="cs-not-disposed", fam={"cs"}, conf="medium", mode="none",
         open=r"new\s+(?:FileStream|StreamReader|StreamWriter|SqlConnection|HttpClient|MemoryStream|SqlCommand)\b", close=r"\busing\b|\.Dispose\(|\.Close\(",
         msg="creates disposable objects with no using/Dispose/Close", tip="wrap them in `using` so they are cleaned up"),
    dict(id="rust-deliberate-leak", fam={"rust"}, conf="low", mode="none", open=r"Box::leak\(|mem::forget\(", close=r"$^",
         msg="deliberately leaks memory (Box::leak / mem::forget)", tip="fine for program-lifetime data; otherwise let Rust free it"),
    dict(id="php-handle-not-closed", fam={"php"}, conf="low", mode="none", open=r"\bfopen\s*\(|\bcurl_init\s*\(", close=r"\bfclose\s*\(|\bcurl_close\s*\(",
         msg="opens handles and never closes them", tip="close them when done"),
    dict(id="ruby-file-not-closed", fam={"ruby"}, conf="low", mode="none", open=r"\bFile\.open\([^)]*\)\s*$|\bFile\.new\(", close=r"\.close\b|File\.open\([^)]*\)\s*(?:do|\{)",
         msg="opens a file without a block and never closes it", tip="use File.open(...) do |f| ... end so it closes itself"),
]
READ_ALL = [
    ({"py"}, r"\.read\(\s*\)|\.readlines\(\s*\)", "reads a whole file into memory at once", "for big files read line by line (`for line in f:`) or in chunks"),
    ({"js"}, r"\breadFileSync\(|\breadFile\(", "reads a whole file into memory at once", "for big files use streams (fs.createReadStream)"),
    ({"java"}, r"readAllBytes\(|Files\.readAllLines\(|Files\.readString\(", "reads a whole file into memory at once", "for big files read with a BufferedReader line by line"),
    ({"cs"}, r"File\.ReadAll(?:Text|Bytes|Lines)\(", "reads a whole file into memory at once", "for big files use StreamReader and read line by line"),
    ({"go"}, r"ioutil\.ReadAll\(|io\.ReadAll\(|os\.ReadFile\(", "reads a whole file/response into memory at once", "for big data use bufio.Scanner or io.Copy"),
    ({"rust"}, r"read_to_string\(|fs::read\(", "reads a whole file into memory at once", "for big files use BufReader and read in pieces"),
    ({"php"}, r"file_get_contents\(|\bfile\(", "reads a whole file into memory at once", "for big files read with fgets / fread in pieces"),
    ({"ruby"}, r"File\.read\(|\.readlines\b", "reads a whole file into memory at once", "for big files use File.foreach"),
]
# how to find where a function/class is DEFINED, per language (group 1 = name)
DEF_RULES = {
    "py": [r"^[ \t]*(?:async[ \t]+)?def[ \t]+([A-Za-z_]\w*)", r"^[ \t]*class[ \t]+([A-Za-z_]\w*)"],
    "js": [r"\bfunction[ \t]*\*?[ \t]*([A-Za-z_$][\w$]*)[ \t]*\(",
           r"\b(?:const|let|var)[ \t]+([A-Za-z_$][\w$]*)[ \t]*=[ \t]*(?:async[ \t]*)?(?:function\b|\([^)\n]*\)[ \t]*(?::[^=\n]+)?=>|[A-Za-z_$][\w$]*[ \t]*=>)",
           r"\bclass[ \t]+([A-Za-z_$][\w$]*)"],
    "java": [r"^[ \t]*(?:@\w+[ \t]+)*(?:public|private|protected)?[ \t]*(?:static[ \t]+)?(?:final[ \t]+)?(?:abstract[ \t]+)?[\w<>\[\],.? ]+?[ \t]+([A-Za-z_]\w*)[ \t]*\([^;{)]*\)[ \t]*(?:throws[\w ,.]+)?\{",
             r"\b(?:class|interface|enum)[ \t]+([A-Za-z_]\w*)"],
    "cs": [r"^[ \t]*(?:public|private|protected|internal)?[ \t]*(?:static[ \t]+)?(?:async[ \t]+)?[\w<>\[\],.? ]+?[ \t]+([A-Za-z_]\w*)[ \t]*\([^;{)]*\)[ \t]*\{?[ \t]*$",
           r"\b(?:class|interface|struct|enum)[ \t]+([A-Za-z_]\w*)"],
    "kotlin": [r"\bfun[ \t]+(?:<[^>]+>[ \t]*)?(?:[\w.]+\.)?([A-Za-z_]\w*)", r"\b(?:class|object|interface)[ \t]+([A-Za-z_]\w*)"],
    "swift": [r"\bfunc[ \t]+([A-Za-z_]\w*)", r"\b(?:class|struct|enum|protocol)[ \t]+([A-Za-z_]\w*)"],
    "go": [r"^func[ \t]+(?:\([^)]*\)[ \t]*)?([A-Za-z_]\w*)", r"^type[ \t]+([A-Za-z_]\w*)[ \t]+(?:struct|interface)"],
    "rust": [r"\bfn[ \t]+([A-Za-z_]\w*)", r"\b(?:struct|enum|trait)[ \t]+([A-Za-z_]\w*)"],
    "php": [r"\bfunction[ \t]+([A-Za-z_]\w*)", r"\bclass[ \t]+([A-Za-z_]\w*)"],
    "ruby": [r"^[ \t]*def[ \t]+(?:self\.)?([A-Za-z_]\w*[?!]?)", r"^[ \t]*class[ \t]+([A-Z]\w*)"],
    "c": [r"^[A-Za-z_][\w \t\*&:<>,]*?[ \t\*&]([A-Za-z_]\w*)[ \t]*\([^;)]*\)[ \t]*\{?[ \t]*$"],
    "lua": [r"\bfunction[ \t]+(?:[\w.:]+[.:])?([A-Za-z_]\w*)[ \t]*\("],
    "sh": [r"^[ \t]*(?:function[ \t]+)?([A-Za-z_]\w*)[ \t]*\(\)[ \t]*\{"],
    "perl": [r"^[ \t]*sub[ \t]+([A-Za-z_]\w*)"],
    "r": [r"^([A-Za-z_.][\w.]*)[ \t]*<-[ \t]*function\b"],
}
NOT_DEAD = {"main", "init", "setup", "teardown", "setUp", "tearDown", "run", "handler", "lambda_handler", "default", "constructor",
            "render", "toString", "equals", "hashCode", "compareTo", "get", "set", "new", "dispose", "close", "Main", "Run", "Start",
            "Update", "Awake", "OnEnable", "OnDisable", "OnDestroy", "Configure", "ConfigureServices", "Index", "Create", "Delete", "Edit",
            "Details", "if", "for", "while", "switch", "catch", "return", "else", "elif", "sizeof"}
KEYWORDS = {"if", "for", "while", "switch", "catch", "return", "else", "elif", "sizeof", "do", "case", "new", "delete", "throw", "using", "lock", "foreach", "typeof"}

HEAVY_LIBS = {
    "npm": {
        "moment": ("dayjs or date-fns (or the built-in Intl.DateTimeFormat)", "moment is large (~300 KB) and cannot be trimmed down"),
        "moment-timezone": ("Intl.DateTimeFormat or date-fns-tz", "very large because it ships every time zone"),
        "lodash": ("lodash-es with single imports, or plain JavaScript", "the whole library is bundled unless you import single functions"),
        "underscore": ("plain JavaScript (modern JS has most of it built in)", "most helpers now exist in the language"),
        "jquery": ("plain DOM functions (document.querySelector, fetch, classList)", "adds ~90 KB for things browsers now do natively"),
        "axios": ("the built-in fetch", "fetch is built into browsers and Node 18+"),
        "request": ("the built-in fetch", "deprecated and heavy"),
        "node-fetch": ("the built-in fetch in Node 18+", "built in now"),
        "bluebird": ("native Promise", "native promises cover it"),
        "uuid": ("crypto.randomUUID()", "built into browsers and Node"),
        "left-pad": ("String.prototype.padStart", "built into the language"),
        "core-js": ("targeting modern browsers (it is only needed for very old ones)", "large polyfill bundle"),
        "rxjs": ("plain async/await, unless you really use streams of events", "large if only a few operators are used"),
    },
    "pip": {
        "pandas": ("the built-in csv / statistics modules if you only read or sum simple tables", "pandas is very large to install and import"),
        "tensorflow": ("onnxruntime or tflite-runtime if you only run a trained model", "tensorflow is huge; inference-only runtimes are far smaller"),
        "torch": ("onnxruntime if you only run a trained model", "very large"),
        "six": ("nothing - Python 3 does not need it", "only for Python 2 compatibility"),
        "pytz": ("zoneinfo (built in since Python 3.9)", "built in now"),
        "simplejson": ("the built-in json module", "built in"),
        "mock": ("unittest.mock (built in)", "built in"),
        "nose": ("pytest or unittest", "abandoned"),
        "requests": ("urllib.request only if you need zero dependencies", "fine to keep for most projects"),
    },
}
PY_ALIASES = {"pyyaml": "yaml", "pillow": "PIL", "beautifulsoup4": "bs4", "scikit_learn": "sklearn", "opencv_python": "cv2",
              "opencv_python_headless": "cv2", "python_dateutil": "dateutil", "attrs": "attr", "pyserial": "serial", "protobuf": "google",
              "python_dotenv": "dotenv", "msgpack_python": "msgpack", "pymysql": "pymysql", "psycopg2_binary": "psycopg2"}
DEV_HINT = re.compile(r"^(@types/|eslint|prettier|jest|vitest|mocha|chai|webpack|vite|rollup|babel|@babel|typescript|ts-|nodemon|husky|lint|"
                      r"sass|postcss|autoprefixer|tailwind|@vitejs|stylelint|cypress|playwright|puppeteer|pytest|black|flake8|mypy|tox|coverage|"
                      r"sphinx|wheel|setuptools|pip|twine|build$)")


# ---------------------------------------------------------------------------------------------------------------
def ext_of(p):
    return p.suffix.lower().lstrip(".")


class Project:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.texts = {}                     # rel -> text
        self.sizes = {}                     # rel -> bytes (every file)
        self.langs = Counter()
        stack = [self.root]
        while stack:
            d = stack.pop()
            try:
                entries = list(d.iterdir())
            except OSError:
                continue
            for e in entries:
                if e.is_symlink():
                    continue
                if e.is_dir():
                    if e.name not in SKIP_DIRS and not e.name.startswith(".cms"):
                        stack.append(e)
                    continue
                rel = e.relative_to(self.root).as_posix()
                try:
                    size = e.stat().st_size
                except OSError:
                    continue
                self.sizes[rel] = size
                x = ext_of(e)
                if x in CODE_EXT:
                    self.langs[x] += 1
                if (x in TEXT_EXT or e.name in ("Dockerfile", "Makefile", "go.mod", "Gemfile", "Pipfile")) and size <= MAX_TEXT and e.name not in SKIP_TEXT_NAMES \
                        and not e.name.endswith((".min.js", ".min.css", ".map")):
                    try:
                        self.texts[rel] = e.read_text(encoding="utf-8")
                    except (UnicodeDecodeError, OSError):
                        pass
        self.tokens = Counter()
        self.token_files = defaultdict(set)
        for rel, t in self.texts.items():
            for tok in re.findall(r"[A-Za-z_$][\w$]*", t):
                self.tokens[tok] += 1
                self.token_files[tok].add(rel)


def line_of(text, pos):
    return text.count("\n", 0, pos) + 1


def code_only(text):
    """Text with whole-line comments blanked (keeps positions)."""
    out = []
    for ln in text.split("\n"):
        s = ln.lstrip()
        out.append(" " * len(ln) if s.startswith(("//", "#", "*", "/*", "--", "'")) and not s.startswith("#include") and not s.startswith("#[") else ln)
    return "\n".join(out)


def finding(cat, conf, rel, line, what, tip, rule=""):
    return {"category": cat, "confidence": conf, "file": rel, "line": line, "what": what, "suggestion": tip, "rule": rule}


# ---------------------------------------------------------------------------------------------------------------
def leaks_and_memory(proj):
    out = []
    for rel, text in proj.texts.items():
        x = ext_of(Path(rel))
        fam = FAMILY.get(x)
        if not fam:
            continue
        body = code_only(text)
        for r in LEAK_RULES:
            if fam not in r["fam"]:
                continue
            if r.get("skip_if") and re.search(r["skip_if"], body):
                continue
            fl = r.get("flags", 0)
            opens = list(re.finditer(r["open"], body, fl))
            if not opens:
                continue
            closes = len(re.findall(r["close"], body, fl))
            bad = closes == 0 if r["mode"] == "none" else (len(opens) - closes >= 2)
            if bad:
                n = len(opens)
                detail = "%s (%d place%s, %d matching clean-up)" % (r["msg"], n, "" if n == 1 else "s", closes)
                out.append(finding("leak", r["conf"], rel, line_of(body, opens[0].start()), detail, r["tip"], r["id"]))
        if fam == "js":
            out += react_effects(rel, body)
        for fams, pat, msg, tip in READ_ALL:
            if fam in fams:
                m = re.search(pat, body)
                if m:
                    out.append(finding("memory", "low", rel, line_of(body, m.start()), msg, tip, "reads-whole-file"))
    return out


def react_effects(rel, body):
    out = []
    for m in re.finditer(r"\buseEffect\s*\(", body):
        i, depth = m.end(), 1
        while i < len(body) and depth:
            depth += {"(": 1, ")": -1}.get(body[i], 0)
            i += 1
        block = body[m.end():i]
        if re.search(r"addEventListener\(|setInterval\(|\.subscribe\(|new\s+(?:WebSocket|ResizeObserver|IntersectionObserver)", block) \
                and not re.search(r"\breturn\s*(?:\(\s*\)\s*=>|function|\w+\s*;)", block):
            out.append(finding("leak", "medium", rel, line_of(body, m.start()),
                               "a React useEffect starts a listener/timer/subscription but returns no clean-up function",
                               "return a function from the effect that removes the listener / clears the timer, otherwise one is added again on every render",
                               "react-effect-no-cleanup"))
    return out


def heavy_files(proj):
    out = []
    for rel, size in proj.sizes.items():
        x = ext_of(Path(rel))
        mb = size / 1048576
        kb = size / 1024
        if x in IMAGE_EXT and kb > 300:
            out.append(finding("memory", "high" if mb > 1 else "medium", rel, 0, "image is %.1f MB" % mb,
                               "compress it or convert to WebP/AVIF (often 50-90% smaller), and size it to how big it is actually shown; add loading=\"lazy\" if it is below the fold",
                               "heavy-image"))
        elif x in ("mp4", "webm", "mov") and mb > 5:
            out.append(finding("memory", "medium", rel, 0, "video is %.1f MB" % mb,
                               "re-encode with a lower bitrate, and avoid autoloading it (preload=\"none\" or a poster image)", "heavy-video"))
        elif x in ("ttf", "otf") and kb > 300:
            out.append(finding("memory", "low", rel, 0, "font file is %.0f KB" % kb, "convert to WOFF2 and keep only the characters you use", "heavy-font"))
        elif x in ("glb", "gltf", "obj", "hdr", "bin") and mb > 2:
            out.append(finding("memory", "medium", rel, 0, "3D/data file is %.1f MB" % mb,
                               "compress models with Draco/Meshopt, textures with KTX2/WebP, or reduce polygon count", "heavy-3d"))
        elif x in ("js", "css", "mjs") and kb > 400 and not rel.endswith((".min.js", ".min.css")):
            out.append(finding("memory", "medium", rel, 0, "source file is %.0f KB" % kb, "split it into smaller pieces that load only when needed", "huge-source"))
    return out


# ---------------------------------------------------------------------------------------------------------------
def dead_code(proj):
    out = []
    for rel, text in proj.texts.items():
        x = ext_of(Path(rel))
        fam = FAMILY.get(x)
        if not fam or fam not in DEF_RULES:
            continue
        is_test = bool(re.search(r"(^|/)(tests?|spec|__tests__|e2e)(/|$)|(\.|_)(test|spec)\.|^test_", rel, re.I))
        lines = text.split("\n")
        seen = set()
        for pat in DEF_RULES[fam]:
            for m in re.finditer(pat, text, re.M):
                name = m.group(1)
                if name in seen or name in NOT_DEAD or name in KEYWORDS or len(name) < 3 or (name.startswith("__") and name.endswith("__")):
                    continue
                if name.startswith(("test", "Test", "on", "handle")) and (is_test or name.startswith(("test", "Test"))):
                    continue
                if proj.tokens[name] != 1:
                    continue
                ln = line_of(text, m.start())
                defline = lines[ln - 1] if ln - 1 < len(lines) else ""
                prev = lines[ln - 2].strip() if ln >= 2 else ""
                if prev.startswith(("@", "[")) or "@" in defline.split(name)[0]:
                    continue                                       # decorators / annotations: a framework may call it
                if is_test:
                    continue
                if re.search(r"\boverride\b", defline):
                    continue
                if fam == "py" and defline.startswith((" ", "\t")):
                    # a method of a class that inherits from something: the library/framework may call it by name
                    ind = len(defline) - len(defline.lstrip())
                    base = None
                    for k in range(ln - 2, -1, -1):
                        cl = lines[k]
                        if cl.strip() and len(cl) - len(cl.lstrip()) < ind:
                            base = re.match(r"\s*class\s+\w+\s*\(([^)]*)\)", cl)
                            break
                    if base and base.group(1).strip() not in ("", "object"):
                        continue
                seen.add(name)
                exported = bool(re.search(r"\bexport\b|\bpublic\b|\bpub\b|module\.exports|\bopen\b", defline)) or \
                    (fam == "go" and name[0].isupper()) or (fam == "py" and not name.startswith("_") and not defline.startswith((" ", "\t")))
                private = bool(re.search(r"\bprivate\b|\bstatic\b", defline)) or (fam == "py" and name.startswith("_")) or \
                    (fam == "go" and name[0].islower()) or (fam in ("js", "rust") and not exported)
                conf = "low" if exported else ("high" if private else "medium")
                kind = "class/type" if re.search(r"\b(class|struct|enum|trait|interface|object|protocol|type)\b", defline) else "function"
                why = "the %s `%s` is never mentioned anywhere else in the project" % (kind, name)
                tip = "if nothing calls it, it can be removed (check first that no outside code or framework uses it by name)" \
                    if not exported else "it is public, so something outside this project might use it; if not, it can go"
                out.append(finding("dead-code", conf, rel, ln, why, tip, "never-mentioned"))
    return out


TERM = re.compile(r"^(return\b|throw\b|raise\b|break\b|continue\b|process\.exit\(|sys\.exit\(|os\.Exit\()")
CLOSERS = ("}", ")", "]", "case ", "case:", "default", "else", "elif", "except", "finally", "catch", "end", "when", "rescue", "ensure", "#", "//", "/*", "*", "--", "@")


def unreachable(proj):
    out = []
    for rel, text in proj.texts.items():
        x = ext_of(Path(rel))
        fam = FAMILY.get(x)
        if not fam:
            continue
        lines = text.split("\n")
        for i, ln in enumerate(lines):
            s = ln.strip()
            if not TERM.match(s):
                continue
            if re.search(r"\b(if|unless|and|or)\b", s) and fam in ("ruby", "py", "perl", "sh"):
                continue                                          # `return x if y` is conditional
            if s.count("(") != s.count(")") or s.count("[") != s.count("]") or s.count("{") != s.count("}"):
                continue
            if fam in ("py", "ruby", "lua", "sh"):
                if s.endswith((",", "\\", "(", "+", "-", "*", "/", "and", "or", "&", "|", ":")):
                    continue
            elif not s.endswith(";") and not s.endswith("}") and fam not in ("go", "rust", "kotlin", "swift", "js"):
                continue
            elif fam in ("js",) and s.endswith((",", "(", "+", "-", "*", "/", "&&", "||", "?", ":", "=>")):
                continue
            if s.endswith("{") or s.endswith(":") and fam != "py":
                continue
            indent = ln[:len(ln) - len(ln.lstrip())]
            for j in range(i + 1, min(i + 8, len(lines))):
                t = lines[j]
                ts = t.strip()
                if not ts:
                    continue
                if ts.startswith(("//", "#", "/*", "*", "--")) and not ts.startswith("#include"):
                    continue
                tin = t[:len(t) - len(t.lstrip())]
                if tin == indent and not ts.startswith(CLOSERS) and not re.match(r"[\w$]+\s*:\s*$", ts) \
                        and not (fam == "js" and TERM.match(s) and s.startswith("break")):
                    out.append(finding("dead-code", "medium", rel, j + 1,
                                       "this line comes right after `%s` at the same level, so it can never run" % s.split()[0].rstrip("(;"),
                                       "delete it, or move it before the `%s`" % s.split()[0].rstrip("(;"), "unreachable"))
                break
    return out


ENTRY_STEMS = {"index", "main", "app", "__init__", "__main__", "setup", "manage", "conftest", "readme", "license", "changelog", "dockerfile",
               "makefile", "package", "tsconfig", "vite.config", "webpack.config", "favicon", "robots", "sitemap", "manifest", "gulpfile",
               "gruntfile", "wsgi", "asgi", "settings", "cargo", "go", "pom", "build", "lib", "mod", "program", "startup", "application", "server",
               "worker", "service-worker", "sw", "404", "500", "_app", "_document", "layout", "page", "route", "middleware"}


def unreferenced_files(proj):
    out = []
    names_in = defaultdict(set)                    # lowercase token (file name or stem) -> files mentioning it
    for rel, t in proj.texts.items():
        for tok in set(re.findall(r"[A-Za-z0-9_\-.@]+", t.lower())):
            names_in[tok].add(rel)
    for rel in proj.sizes:
        p = Path(rel)
        x = ext_of(p)
        if x not in CODE_EXT and x not in ASSET_EXT and x not in ("css", "scss", "less", "html", "htm"):
            continue
        stem, base = p.stem.lower(), p.name.lower()
        if stem in ENTRY_STEMS or re.search(r"(^|/)(tests?|spec|__tests__|e2e|migrations?|scripts?|bin|examples?|docs?)(/|$)", rel, re.I) \
                or stem.startswith(("test_", "conftest")) or stem.endswith(("_test", ".test", ".spec", "_spec", "test")):
            continue
        if x in ("html", "htm"):
            continue                              # pages are reached by URL, not by name from another file
        refs = (names_in.get(base, set()) | names_in.get(stem, set())) - {rel}
        if refs:
            continue
        web = bool(re.match(r"(public|static|assets|www|media)/", rel))
        conf = "low" if (web or x in CODE_EXT and FAMILY.get(x) in ("js", "py")) else "medium"
        kind = "asset" if x in ASSET_EXT else "file"
        out.append(finding("unused-file", conf, rel, 0, "no other file in the project mentions this %s by name" % kind,
                           "if nothing uses it (and no outside page/link points to it by address), it can be removed" + (
                               " - it sits in a public web folder, so visitors or other sites might link to it directly" if web else ""), "unreferenced-file"))
    return out


# ---------------------------------------------------------------------------------------------------------------
def dependencies(proj):
    out = []
    blob_parts = []
    for rel, t in proj.texts.items():
        if Path(rel).name in ("package.json", "requirements.txt", "Cargo.toml", "go.mod", "pyproject.toml"):
            continue
        blob_parts.append(t)
    blob = "\n".join(blob_parts).lower()
    toks = {t.lower() for t in proj.tokens}

    def files_using(pattern):
        rx = re.compile(pattern)
        return [rel for rel, t in proj.texts.items() if rx.search(t) and Path(rel).name != "package.json"]

    for rel, text in proj.texts.items():
        name = Path(rel).name
        if name == "package.json":
            try:
                data = json.loads(text)
            except ValueError:
                continue
            scripts = json.dumps(data.get("scripts", {})).lower()
            for section in ("dependencies", "devDependencies"):
                for dep in (data.get(section) or {}):
                    d = dep.lower()
                    used = d in blob or d in scripts or d.split("/")[-1] in scripts
                    ln = next((i + 1 for i, l in enumerate(text.split("\n")) if '"%s"' % dep in l), 0)
                    if not used and not (section == "devDependencies" and DEV_HINT.match(d)):
                        out.append(finding("dependency", "medium" if section == "dependencies" else "low", rel, ln,
                                           "`%s` is listed in %s but its name appears nowhere in the code or scripts" % (dep, section),
                                           "if nothing uses it (also not a plugin loaded by a config), remove it from package.json to make installs and builds lighter",
                                           "declared-never-used"))
                    if dep in HEAVY_LIBS["npm"] and section == "dependencies":
                        alt, why = HEAVY_LIBS["npm"][dep]
                        users = files_using(r"""(?:from\s+|require\(\s*|import\s+)['"]%s(?:/[^'"]*)?['"]""" % re.escape(dep))
                        extra = ""
                        if dep == "lodash":
                            fns = set()
                            for u in users:
                                fns |= set(re.findall(r"\b_\.(\w+)", proj.texts[u]))
                                for g in re.findall(r"import\s*\{([^}]*)\}\s*from\s*['\"]lodash", proj.texts[u]):
                                    fns |= {s.strip() for s in g.split(",") if s.strip()}
                            if fns:
                                extra = " (you use only: %s)" % ", ".join(sorted(fns)[:8])
                        out.append(finding("dependency", "medium" if len(users) <= 3 else "low", rel, ln,
                                           "`%s`: %s; imported in %d file%s%s" % (dep, why, len(users), "" if len(users) == 1 else "s", extra),
                                           "consider %s. Check the effect on your bundle size before switching - nothing here is measured" % alt, "lighter-alternative"))
        elif name == "requirements.txt":
            for i, ln_ in enumerate(text.split("\n"), 1):
                m = re.match(r"\s*([A-Za-z0-9_.\-]+)", ln_)
                if not m or ln_.strip().startswith(("#", "-")):
                    continue
                pkg = m.group(1)
                mod = PY_ALIASES.get(re.sub(r"[-_.]+", "_", pkg.lower()), re.sub(r"[-_.]+", "_", pkg.lower()))
                if mod.lower() not in toks and pkg.lower() not in toks and not DEV_HINT.match(pkg.lower()):
                    out.append(finding("dependency", "low", rel, i, "`%s` is listed but no `import %s` was found" % (pkg, mod),
                                       "if the code never imports it (some packages are only used as plugins or tools), remove it", "declared-never-used"))
                if pkg.lower() in HEAVY_LIBS["pip"]:
                    alt, why = HEAVY_LIBS["pip"][pkg.lower()]
                    users = files_using(r"(?m)^\s*(?:import|from)\s+%s\b" % re.escape(mod))
                    out.append(finding("dependency", "medium" if len(users) <= 2 else "low", rel, i,
                                       "`%s`: %s; imported in %d file%s" % (pkg, why, len(users), "" if len(users) == 1 else "s"),
                                       "consider %s. Measure install size and start-up time before switching" % alt, "lighter-alternative"))
        elif name == "Cargo.toml":
            sect = None
            for i, ln_ in enumerate(text.split("\n"), 1):
                s = ln_.strip()
                if s.startswith("["):
                    sect = s
                    continue
                m = re.match(r"([A-Za-z0-9_\-]+)\s*=", s)
                if m and sect in ("[dependencies]", "[dev-dependencies]") and m.group(1).replace("-", "_").lower() not in toks:
                    out.append(finding("dependency", "low", rel, i, "crate `%s` is declared but never named in the code" % m.group(1),
                                       "if no code uses it, remove it to speed up builds", "declared-never-used"))
        elif name == "go.mod":
            for i, ln_ in enumerate(text.split("\n"), 1):
                m = re.match(r"\s*(?:require\s+)?([\w.\-]+\.[\w.\-]+/[\w.\-/]+)\s+v[\w.\-+]+", ln_)
                if m and m.group(1).lower() not in blob and "// indirect" not in ln_:
                    out.append(finding("dependency", "low", rel, i, "module `%s` is required but never imported" % m.group(1),
                                       "run `go mod tidy` - it removes unused modules safely", "declared-never-used"))
    # ---- dependency files of other languages (Java, Go, Rust, PHP, Ruby, C#, Dart, Swift, modern Python)
    try:
        import deps as D
        ds = [d for d in D.parse_all(proj) if d["eco"] != "js"]
        already = {"go", "rust"}                       # their 'declared but never used' check is above
        for d, alt, why, level, users in D.replacement_findings(ds, lambda d: []):
            out.append(finding("dependency", level if level != "high" else "high", d["file"], d["line"],
                               "`%s`: %s" % (d["name"], why), "consider %s. Check compatibility and measure before switching - nothing here is measured" % alt, "lighter-alternative"))
        for d in D.unused_candidates([x for x in ds if x["eco"] not in already], proj, toks, blob):
            out.append(finding("dependency", "low", d["file"], d["line"], "`%s` is declared in %s but its name appears nowhere in the code" % (d["name"], d["file"]),
                               "if nothing uses it (frameworks sometimes load libraries by themselves), remove it to make downloads and builds lighter", "declared-never-used"))
        for d in ds:
            if d["eco"] == "rust" and "full" in d.get("features", "") and d["name"] == "tokio":
                out.append(finding("dependency", "medium", d["file"], d["line"], "`tokio` is built with the \"full\" feature set",
                                   "\"full\" compiles every part of tokio; choosing only the parts you use (rt-multi-thread, net, time ...) shortens builds and shrinks the program", "heavy-features"))
    except Exception:
        pass
    return out


# ---------------------------------------------------------------------------------------------------------------
CAT_TEXT = {
    "leak": ("Possible memory leaks", "something is opened/started/created and never closed/stopped/freed, so it stays in memory"),
    "memory": ("Heavy on memory", "code or files that use a lot of memory at once"),
    "dead-code": ("Code that does nothing", "functions nobody mentions, and lines that can never run"),
    "unused-file": ("Files nobody mentions", "files that no other file refers to"),
    "dependency": ("Libraries", "libraries you declared but do not use, and heavy ones that have lighter options"),
}
ORDER = {"high": 0, "medium": 1, "low": 2}


def analyse(root):
    proj = Project(root)
    fs = []
    fs += leaks_and_memory(proj)
    fs += heavy_files(proj)
    fs += dead_code(proj)
    fs += unreachable(proj)
    fs += unreferenced_files(proj)
    fs += dependencies(proj)
    fs.sort(key=lambda f: (list(CAT_TEXT).index(f["category"]), ORDER[f["confidence"]], f["file"], f["line"]))
    return proj, fs


def print_report(proj, fs, show_all=False, per_cat=12):
    print("Arachnid insights - looking at: %s   (suggestions only; nothing is changed)" % proj.root)
    deep = {"py", "js", "java", "cs", "go", "rust", "php", "ruby", "c"}
    langs = Counter()
    for x, n in proj.langs.items():
        langs[x] += n
    if langs:
        parts = []
        for x, n in langs.most_common(8):
            parts.append("%s %d%s" % (x, n, "" if FAMILY[x] in deep else " (general checks only)"))
        print("   Code files: " + ", ".join(parts))
    if not fs:
        print("\nNothing to suggest.")
        return
    for cat, (title, expl) in CAT_TEXT.items():
        items = [f for f in fs if f["category"] == cat]
        if not items:
            continue
        print("\n=== %s - %s ===" % (title, expl))
        shown = items if show_all else [f for f in items if f["confidence"] != "low"][:per_cat] or items[:per_cat]
        for f in shown:
            loc = f["file"] + (":%d" % f["line"] if f["line"] else "")
            print("  [%-6s] %s" % (f["confidence"], loc))
            print("           %s" % f["what"])
            print("           -> %s" % f["suggestion"])
        if len(items) > len(shown):
            print("  ... and %d more in this group (all are in the report file%s)" % (len(items) - len(shown), "" if show_all else "; add --all to print them"))
    c = Counter(f["confidence"] for f in fs)
    print("\n---------------- Summary ----------------")
    print("Suggestions: %d   (high %d, medium %d, low %d)" % (len(fs), c["high"], c["medium"], c["low"]))
    print("Reminder: these come from READING the code. Only running the program and measuring it can prove a leak or a speed-up.")


def main():
    ap = argparse.ArgumentParser(description="Arachnid insights - leaks, heavy memory, dead code, lighter libraries (suggestions only)")
    ap.add_argument("root", nargs="?", default=str(HERE.parent))
    ap.add_argument("--out", default=str(HERE / "reports" / "insights-report.json"))
    ap.add_argument("--all", action="store_true", help="print low-confidence hints and every item")
    a = ap.parse_args()
    proj, fs = analyse(a.root)
    print_report(proj, fs, a.all)
    try:
        import ledger
        ledger.reset("insights")
        for f in fs:
            sev = "warning" if f["category"] in ("leak", "dead-code") and f["confidence"] in ("high", "medium") else "info"
            ledger.record("insights", "idea", sev, f["file"], f["line"], "[%s] %s" % (f["confidence"], f["what"]), "", f["suggestion"], f["category"])
    except Exception:
        pass
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"root": str(proj.root), "count": len(fs), "findings": fs}, indent=1, ensure_ascii=False), encoding="utf-8")
    print("Full report saved to: %s" % out)


if __name__ == "__main__":
    main()
