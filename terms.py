#!/usr/bin/env python3
"""
ARACHNID - Step 9: TERMINOLOGY  (scan first, sense what needs fixing, improve - without changing the meaning)
==========================================================================================================
    python terms.py [folder]              SCAN + SENSE: show what terms are used, which are inconsistent, what would change
    python terms.py [folder] --fix        apply the meaning-safe changes (backup first)
    python terms.py --undo                put the last change back

The three steps
  1 SCAN   reads every text area of the project: documentation files (.md .mdx .txt .rst .adoc .org), the visible text of
           web pages, and the comments inside code files (any language).  Code itself is never read as text.
  2 SENSE  takes a census of every term and decides what each one needs:
             * official spelling       "javascript", "Javascript", "java script"  ->  JavaScript   (also GitHub, Node.js, WebGL ...)
             * one name for one thing  "e-mail" vs "email" -> whichever your text already uses most (needs a clear winner)
             * one English            "colour" vs "color" -> your project's dominant style (needs a clear winner)
             * your own glossary       terms you teach it in glossary.txt
           and prints the plan BEFORE touching anything.
  3 FIX    changes ONLY the spelling/capitalisation of a term.  The words stay the same words, so the meaning stays the same.

What it will never do
  * Change code, identifiers, file names, folder names, links, addresses, anything inside `backticks` or a code block.
  * Change text on web pages (it is the product: a changed letter changes the page) - reported only.
  * Change a comment in a language where it cannot PROVE the program stayed identical - reported only.
  * Guess a winner: if your text uses two styles about equally, nothing is changed and you are told.
  * Rewrite sentences.  Plainer-wording ideas ("utilize" -> "use") are only suggested, never applied.

glossary.txt (optional, next to this file) - teach it your own terms:
      GitHub               a canonical spelling: every capitalisation of it is made to match
      colour => color      always replace the left word with the right word
      !Centre              never touch this word
"""
import argparse
import json
import os
import re
import shutil
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "vendor"))
import textcheck  # noqa: E402  (page-text extraction + English test)

SKIP_DIRS = textcheck.SKIP_DIRS
DOC_EXT = {"md", "mdx", "txt", "rst", "adoc", "asciidoc", "org"}
PAGE_EXT = {"html", "htm"}
COMMENT_STYLE = {}
for _e in ("js", "jsx", "mjs", "cjs", "ts", "tsx", "java", "kt", "kts", "scala", "cs", "go", "rs", "php", "swift", "dart", "c", "h", "cpp", "cc", "hpp", "cxx", "css", "scss", "less", "vue", "svelte"):
    COMMENT_STYLE[_e] = ("//", "/*", "*/")
for _e in ("py", "rb", "sh", "bash", "yml", "yaml", "toml", "r", "pl", "ps1", "ini", "cfg", "conf", "mk", "dockerfile"):
    COMMENT_STYLE[_e] = ("#", None, None)
for _e in ("sql", "lua", "hs"):
    COMMENT_STYLE[_e] = ("--", "/*" if _e == "sql" else None, "*/" if _e == "sql" else None)
for _e in ("xml", "svg"):
    COMMENT_STYLE[_e] = (None, "<!--", "-->")
COMMENT_STYLE["css"] = (None, "/*", "*/")
COMMENT_STYLE["scss"] = ("//", "/*", "*/")
MAX_BYTES = 400_000

# ---------------------------------------------------------------------------------------------------------------
# DATA
# ---------------------------------------------------------------------------------------------------------------
# official spellings: (canonical, regex of the wrong forms, plural allowed)
BRANDS = [
    ("JavaScript", r"java[\s-]?script", False), ("TypeScript", r"type[\s-]?script", False),
    ("GitHub", r"git[\s-]?hub", False), ("GitLab", r"git[\s-]?lab", False),
    ("Node.js", r"node(?:\.|[\s-]?)js", False), ("Vue.js", r"vue(?:\.|[\s-]?)js", False), ("Next.js", r"next(?:\.|[\s-]?)js", False),
    ("Three.js", r"three(?:\.|[\s-]?)js", False), ("React", r"react(?:\.|[\s-]?)js", False),
    ("WebGL", r"web[\s-]?gl", False), ("WebSocket", r"web[\s-]?socket", True), ("WebAssembly", r"web[\s-]?assembly", False),
    ("WordPress", r"word[\s-]?press", False), ("YouTube", r"you[\s-]?tube", False), ("LinkedIn", r"linked[\s-]?in", False),
    ("WhatsApp", r"whats[\s-]?app", False), ("PostgreSQL", r"postgre[\s-]?sql", False), ("MongoDB", r"mongo[\s-]?db", False),
    ("MySQL", r"my[\s-]?sql", False), ("SQLite", r"sqlite", False), ("OAuth", r"oauth", False), ("DevOps", r"dev[\s-]?ops", False),
    ("Wi-Fi", r"wi[\s-]?fi", False), ("iOS", r"ios", False), ("macOS", r"mac[\s-]?os", False), ("PowerShell", r"power[\s-]?shell", False),
    ("VS Code", r"vs[\s-]?code|visual studio code", False), ("Stack Overflow", r"stack[\s-]?overflow", False),
    ("JSON", r"json", False), ("HTML", r"html", False), ("CSS", r"css", False), ("XML", r"xml", False), ("YAML", r"yaml", False),
    ("API", r"api", True), ("URL", r"url", True), ("SEO", r"seo", False), ("PDF", r"pdf", True), ("CPU", r"cpu", True), ("GPU", r"gpu", True),
    ("USB", r"usb", False), ("SQL", r"sql", False), ("HTTPS", r"https", False), ("HTTP", r"http", False), ("UI", r"ui", False), ("UX", r"ux", False),
    ("FAQ", r"faq", True), ("CMS", r"cms", False), ("CDN", r"cdn", True), ("SDK", r"sdk", True), ("CLI", r"cli", True), ("IDE", r"ide", True),
]
# one name for one thing: the project's own majority decides (all spellings mean the same word)
CONSISTENCY = [
    ["email", "e-mail"], ["website", "web site", "web-site"], ["username", "user name", "user-name"],
    ["filename", "file name", "file-name"], ["homepage", "home page", "home-page"], ["database", "data base"],
    ["frontend", "front-end", "front end"], ["backend", "back-end", "back end"], ["checkbox", "check box", "check-box"],
    ["dropdown", "drop-down", "drop down"], ["timestamp", "time stamp", "time-stamp"],
]
# British / American: (british stem, american stem, endings).  Only words with no second meaning.
UKUS = [
    ("colour", "color", r"(?:s|ed|ing|ful|less|s)?"), ("favourite", "favorite", r"s?"), ("behaviour", "behavior", r"(?:s|al)?"),
    ("neighbour", "neighbor", r"(?:s|hood|hoods|ing)?"), ("honour", "honor", r"(?:s|ed|ing|able)?"), ("flavour", "flavor", r"(?:s|ed|ing)?"),
    ("humour", "humor", r"s?"), ("labour", "labor", r"(?:s|ed|ing)?"), ("catalogue", "catalog", r"(?:s|d)?"),
    ("defence", "defense", r"s?"), ("offence", "offense", r"s?"),
    ("analys", "analyz", r"(?:e|ed|ing|er|ers)"), ("organis", "organiz", r"(?:e|ed|es|ing|er|ers|ation|ations)"),
    ("customis", "customiz", r"(?:e|ed|es|ing|ation)"), ("optimis", "optimiz", r"(?:e|ed|es|ing|er|ation)"),
    ("realis", "realiz", r"(?:e|ed|es|ing|ation)"), ("recognis", "recogniz", r"(?:e|ed|es|ing|ition)"),
    ("minimis", "minimiz", r"(?:e|ed|es|ing|ation)"), ("maximis", "maximiz", r"(?:e|ed|es|ing|ation)"),
    ("summaris", "summariz", r"(?:e|ed|es|ing)"), ("categoris", "categoriz", r"(?:e|ed|es|ing|ation)"),
    ("utilis", "utiliz", r"(?:e|ed|es|ing|ation)"), ("authoris", "authoriz", r"(?:e|ed|es|ing|ation)"),
    ("normalis", "normaliz", r"(?:e|ed|es|ing|ation)"), ("initialis", "initializ", r"(?:e|ed|es|ing|ation)"),
    ("synchronis", "synchroniz", r"(?:e|ed|es|ing|ation)"), ("visualis", "visualiz", r"(?:e|ed|es|ing|ation)"),
    ("personalis", "personaliz", r"(?:e|ed|es|ing|ation)"), ("prioritis", "prioritiz", r"(?:e|ed|es|ing|ation)"),
    ("emphasis", "emphasiz", r"(?:e|ed|es|ing)"), ("apologis", "apologiz", r"(?:e|ed|es|ing)"),
    ("cancell", "cancel", r"(?:ed|ing|ation)"), ("travell", "travel", r"(?:ed|ing|er|ers)"), ("modell", "model", r"(?:ed|ing|er|ers)"),
    ("labell", "label", r"(?:ed|ing)"),
]
PLAIN = [  # suggestions only
    (r"\butili[sz](?:e|es|ed|ing)\b", "use", "'utilize' just means 'use'. 'Use' is shorter and clearer."),
    (r"\bin order to\b", "to", "'in order to' can almost always be just 'to'."),
    (r"\bdue to the fact that\b", "because", "'due to the fact that' is four words for 'because'."),
    (r"\bat this point in time\b", "now", "'at this point in time' means 'now'."),
    (r"\bprior to\b", "before", "'prior to' means 'before'."),
    (r"\bsubsequent to\b", "after", "'subsequent to' means 'after'."),
    (r"\bin the event that\b", "if", "'in the event that' means 'if'."),
    (r"\bwith regard to\b", "about", "'with regard to' means 'about'."),
    (r"\ba number of\b", "several", "'a number of' is vague - 'several' or the actual number is clearer."),
    (r"\bleverag(?:e|es|ed|ing)\b", "use", "'leverage' is usually just 'use'."),
]
LB = r"(?<![\w@#/\\.:_\-])"
RA = r"(?![\w@/\\_]|[.\-:]\w|://)"


class Hit:
    __slots__ = ("file", "line", "start", "end", "surface", "repl", "family", "kind", "editable", "why_not", "area")

    def __init__(self, **k):
        for a in self.__slots__:
            setattr(self, a, k.get(a))


# ---------------------------------------------------------------------------------------------------------------
# glossary
# ---------------------------------------------------------------------------------------------------------------
def load_glossary():
    g = {"canon": [], "map": [], "never": set()}
    f = HERE / "glossary.txt"
    if not f.exists():
        try:
            f.write_text("# Teach Arachnid your own terms. One per line.\n#   GitHub              make every capitalisation of this term match\n"
                         "#   colour => color     always replace the left word with the right word\n#   !Centre             never touch this word\n", encoding="utf-8")
        except OSError:
            pass
        return g
    for ln in f.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        if ln.startswith("!"):
            g["never"].add(ln[1:].strip().lower())
        elif "=>" in ln:
            a, b = [x.strip() for x in ln.split("=>", 1)]
            if a and b:
                g["map"].append((a, b))
        else:
            g["canon"].append(ln)
    return g


# ---------------------------------------------------------------------------------------------------------------
# where the prose is: yields (line_number, line_text, protected_spans)
# ---------------------------------------------------------------------------------------------------------------
PROT = [re.compile(p) for p in (r"``[^`]*``", r"`[^`]*`", r"https?://\S+|www\.\S+", r"\]\([^)]*\)", r"^\s*\[[^\]]+\]:\s*\S+", r"<[^>]+>",
                                  r"\{\{.*?\}\}|\{%.*?%\}", r"\b[\w.+-]+@[\w-]+\.[\w.]+\b", r"\$[^$\n]+\$")]


def protect(line, extra=None):
    sp = list(extra or [])
    for rx in PROT:
        sp += [(m.start(), m.end()) for m in rx.finditer(line)]
    return sp


def doc_lines(text, ext):
    lines = text.split("\n")
    start = 0
    if ext in ("md", "mdx") and lines and lines[0].strip() == "---":
        for k in range(1, min(len(lines), 80)):
            if lines[k].strip() == "---":
                start = k + 1
                break
    fence, prev_blank, literal = False, True, False
    for n in range(start, len(lines)):
        raw = lines[n].rstrip("\r")
        s = raw.strip()
        if ext in ("md", "mdx", "adoc", "asciidoc", "org"):
            if re.match(r"^\s*(```|~~~|----+|\.\.\.\.+|#\+begin_src)", raw, re.I) or re.match(r"^\s*#\+end_src", raw, re.I):
                fence = not fence
                continue
            if fence:
                continue
        if ext == "rst":
            if literal:
                if not s or raw.startswith((" ", "\t")):
                    continue
                literal = False
            if re.match(r"^\s*\.\.\s+(code|code-block|sourcecode|literalinclude|raw)::", raw):
                literal = True
                continue
            if s.endswith("::"):
                literal = True
        if ext in ("md", "mdx") and (raw.startswith("\t") or raw.startswith("    ")) and prev_blank and not re.match(r"^\s*(?:[-*+]|\d+[.)])\s", raw):
            continue
        prev_blank = not s
        if s:
            yield n + 1, raw, protect(raw)


def comment_lines(text, ext):
    style = COMMENT_STYLE.get(ext)
    if not style:
        return
    line_mark, b_open, b_close = style
    in_block = False
    for n, raw in enumerate(text.split("\n"), 1):
        line = raw.rstrip("\r")
        if in_block:
            end = line.find(b_close)
            if end >= 0:
                in_block = False
                yield n, line, [(end, len(line))]
            else:
                yield n, line, []
            continue
        cands = []
        if line_mark:
            i = line.find(line_mark)
            while i >= 0:
                before = line[:i]
                quotes = before.count('"') + before.count("'") + before.count("`")
                if quotes % 2 == 0 and not (line_mark == "//" and i > 0 and line[i - 1] == ":") and not (line_mark == "#" and line[i:i + 2] in ("#!", "#[", "#{")):
                    cands.append((i, "line"))
                    break
                i = line.find(line_mark, i + 1)
        if b_open:
            j = line.find(b_open)
            while j >= 0:
                before = line[:j]
                if (before.count('"') + before.count("'") + before.count("`")) % 2 == 0:
                    cands.append((j, "block"))
                    break
                j = line.find(b_open, j + 1)
        if not cands:
            continue
        i, kind = min(cands)
        if kind == "line":
            yield n, line, protect(line, [(0, i + len(line_mark))])
        else:
            end = line.find(b_close, i + len(b_open))
            if end >= 0:
                yield n, line, protect(line, [(0, i + len(b_open)), (end, len(line))])
            else:
                in_block = True
                yield n, line, protect(line, [(0, i + len(b_open))])


def page_lines(text):
    p = textcheck.VisibleText()
    try:
        p.feed(text)
    except Exception:
        pass
    for ln, t in p.chunks:
        yield ln, t, protect(t)


# ---------------------------------------------------------------------------------------------------------------
# matching
# ---------------------------------------------------------------------------------------------------------------
BRAND_RX = [(c, re.compile(LB + r"(?:" + pat + r")" + (r"(s)?" if pl else "") + RA, re.I), pl) for c, pat, pl in BRANDS]
CONS_RX = []
for forms in CONSISTENCY:
    alts = sorted({re.escape(f).replace(r"\ ", "[ ]").replace(r"\-", "-") for f in forms}, key=len, reverse=True)
    CONS_RX.append((forms, re.compile(LB + "(" + "|".join(alts) + ")" + RA, re.I)))
UKUS_RX = []
for uk, us, end in UKUS:
    UKUS_RX.append((uk, us, re.compile(LB + "((?:" + uk + "|" + us + ")" + end + ")" + RA, re.I)))
PLAIN_RX = [(re.compile(p, re.I), r, w) for p, r, w in PLAIN]


def inside(spans, a, b):
    return any(a < e and b > s for s, e in spans)


def sentence_start(line, a):
    pre = re.sub(r"^[\s#>*\-+\d.)\]]*", "", line[:a])
    return pre.strip() == "" or pre.rstrip()[-1:] in ".!?:"


def keep_case(surface, new):
    return new[:1].upper() + new[1:] if surface[:1].isupper() else new


def find_hits(rel, area, editable, why_not, lines, glossary):
    hits = []
    never = glossary["never"]
    canon_extra = [(c, re.compile(LB + re.escape(c) + RA, re.I)) for c in glossary["canon"]]
    for ln, line, spans in lines:
        # glossary: explicit mappings
        for a, b in glossary["map"]:
            for m in re.finditer(LB + re.escape(a) + RA, line):
                if not inside(spans, m.start(), m.end()) and m.group(0).lower() not in never:
                    hits.append(Hit(file=rel, line=ln, start=m.start(), end=m.end(), surface=m.group(0), repl=b, family="glossary",
                                    kind="glossary", editable=editable, why_not=why_not, area=area))
        for c, rx in canon_extra:
            for m in rx.finditer(line):
                if m.group(0) != c and not inside(spans, m.start(), m.end()) and m.group(0).lower() not in never:
                    hits.append(Hit(file=rel, line=ln, start=m.start(), end=m.end(), surface=m.group(0), repl=c, family=c, kind="official",
                                    editable=editable, why_not=why_not, area=area))
        for c, rx, pl in BRAND_RX:
            for m in rx.finditer(line):
                if inside(spans, m.start(), m.end()) or m.group(0).lower() in never:
                    continue
                surface = m.group(0)
                new = c + ("s" if pl and m.group(1) else "")
                if surface == new:
                    hits.append(Hit(file=rel, line=ln, start=m.start(), end=m.end(), surface=surface, repl=new, family=c, kind="official-ok",
                                    editable=False, why_not="", area=area))
                else:
                    hits.append(Hit(file=rel, line=ln, start=m.start(), end=m.end(), surface=surface, repl=new, family=c, kind="official",
                                    editable=editable, why_not=why_not, area=area))
        for forms, rx in CONS_RX:
            for m in rx.finditer(line):
                if inside(spans, m.start(), m.end()) or m.group(0).lower() in never:
                    continue
                s = m.group(0)
                if s.isupper() and len(s) > 2:
                    continue
                if s[:1].isupper() and not sentence_start(line, m.start()):
                    continue                                   # a title or a name: leave it
                hits.append(Hit(file=rel, line=ln, start=m.start(), end=m.end(), surface=s, repl=None, family=forms[0], kind="consistency",
                                editable=editable, why_not=why_not, area=area))
        for uk, us, rx in UKUS_RX:
            for m in rx.finditer(line):
                if inside(spans, m.start(), m.end()) or m.group(0).lower() in never:
                    continue
                s = m.group(1)
                if s.isupper() and len(s) > 2:
                    continue
                if s[:1].isupper() and not sentence_start(line, m.start()):
                    continue                                   # "Atlas Fitness Centre" is a name
                is_uk = s.lower().startswith(uk)
                hits.append(Hit(file=rel, line=ln, start=m.start(), end=m.end(), surface=s, repl=None, family=("uk" if is_uk else "us"),
                                kind="ukus:" + uk, editable=editable, why_not=why_not, area=area))
        for rx, r, why in PLAIN_RX:
            for m in rx.finditer(line):
                if not inside(spans, m.start(), m.end()):
                    hits.append(Hit(file=rel, line=ln, start=m.start(), end=m.end(), surface=m.group(0), repl=r, family="plain", kind="plain",
                                    editable=False, why_not=why, area=area))
    return hits


def swap_ukus(surface, uk, us, to_uk):
    src, dst = (us, uk) if to_uk else (uk, us)
    low = surface.lower()
    if not low.startswith(src):
        return None
    new = dst + low[len(src):]
    return keep_case(surface, new)


# ---------------------------------------------------------------------------------------------------------------
# SCAN
# ---------------------------------------------------------------------------------------------------------------
def collect_files(root):
    root = Path(root).resolve()
    out, stack = [], [root]
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
            x = e.suffix.lower().lstrip(".") or e.name.lower()
            if x in DOC_EXT and textcheck.SKIP_NAMES.match(e.name):
                continue
            if x in DOC_EXT or x in PAGE_EXT or x in COMMENT_STYLE:
                if e.name.endswith((".min.js", ".min.css", ".map")) or e.name in ("package-lock.json",):
                    continue
                try:
                    if e.stat().st_size <= MAX_BYTES:
                        out.append(e)
                except OSError:
                    pass
    return root, sorted(out)


def scan(root, glossary):
    root, files = collect_files(root)
    hits, areas = [], Counter()
    texts = {}
    for p in files:
        rel = p.relative_to(root).as_posix()
        x = p.suffix.lower().lstrip(".") or p.name.lower()
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if x in DOC_EXT:
            lines = list(doc_lines(text, x))
            if not textcheck.looks_english([l for _, l, _ in lines]):
                continue
            areas["documentation files"] += 1
            texts[rel] = text
            hits += find_hits(rel, "doc", True, "", lines, glossary)
        elif x in PAGE_EXT:
            lines = list(page_lines(text))
            if not textcheck.looks_english([l for _, l, _ in lines]):
                continue
            areas["web pages (visible text)"] += 1
            hits += find_hits(rel, "page", False, "text on a web page is part of the product - a changed letter changes the page", lines, glossary)
        else:
            lines = list(comment_lines(text, x))
            if not lines:
                continue
            areas["code files (comments only)"] += 1
            texts[rel] = text
            hits += find_hits(rel, "comment", True, "", lines, glossary)
    return root, hits, areas, texts


# ---------------------------------------------------------------------------------------------------------------
# SENSE
# ---------------------------------------------------------------------------------------------------------------
def sense(hits):
    """Decide the replacement for every hit. Returns the plan lines for printing."""
    plan = []
    # official spellings
    off = defaultdict(Counter)
    for h in hits:
        if h.kind in ("official", "official-ok"):
            off[h.family][h.surface] += 1
    for fam, c in sorted(off.items()):
        wrong = sum(v for k, v in c.items() if k not in (fam, fam + "s"))
        if wrong:
            plan.append(("official", fam, dict(c), 'use "%s" (the official spelling)' % fam, wrong))
    # consistency groups
    cons = defaultdict(Counter)
    for h in hits:
        if h.kind == "consistency":
            cons[h.family][re.sub(r"[\s-]+", " ", h.surface.lower())] += 1
    chosen = {}
    forms_of = {f[0]: f for f in CONSISTENCY}
    for fam, c in sorted(cons.items()):
        forms = forms_of[fam]
        norm = {re.sub(r"[\s-]+", " ", f): f for f in forms}
        total = sum(c.values())
        if len(c) < 2:
            continue
        top, topn = c.most_common(1)[0]
        if total >= 4 and topn / total >= 0.75:
            chosen[fam] = norm.get(top, top)
            plan.append(("consistency", fam, dict(c), 'use "%s" (%d%% of your text already does)' % (chosen[fam], round(100 * topn / total)), total - topn))
        else:
            plan.append(("mixed", fam, dict(c), "no clear winner - NOT changed (decide, then teach it in glossary.txt)", 0))
    # British / American, one style for the whole project
    uk = sum(1 for h in hits if h.kind.startswith("ukus:") and h.family == "uk")
    us = sum(1 for h in hits if h.kind.startswith("ukus:") and h.family == "us")
    style = None
    if uk + us:
        total = uk + us
        if total >= 4 and max(uk, us) / total >= 0.75:
            style = "uk" if uk > us else "us"
            if min(uk, us):
                plan.append(("english", "British/American spelling", {"British": uk, "American": us},
                             "use %s spelling everywhere (%d%% of your text already does)" % ("British" if style == "uk" else "American", round(100 * max(uk, us) / total)),
                             min(uk, us)))
        elif min(uk, us):
            plan.append(("mixed", "British/American spelling", {"British": uk, "American": us}, "no clear winner - NOT changed", 0))
    # fill in replacements
    for h in hits:
        if h.kind == "consistency" and h.family in chosen:
            target = chosen[h.family]
            if re.sub(r"[\s-]+", " ", h.surface.lower()) != re.sub(r"[\s-]+", " ", target):
                h.repl = keep_case(h.surface, target)
        elif h.kind.startswith("ukus:") and style and h.family != style:
            uk_s, us_s = [(a, b) for a, b, _e in UKUS if a == h.kind.split(":", 1)[1]][0]
            h.repl = swap_ukus(h.surface, uk_s, us_s, to_uk=(style == "uk"))
    return plan


# ---------------------------------------------------------------------------------------------------------------
# FIX
# ---------------------------------------------------------------------------------------------------------------
def apply_to_text(text, hits):
    lines = text.split("\n")
    by_line = defaultdict(list)
    for h in hits:
        by_line[h.line].append(h)
    for ln, hs in by_line.items():
        raw = lines[ln - 1]
        cr = raw.endswith("\r")
        body = raw[:-1] if cr else raw
        for h in sorted(hs, key=lambda h: -h.start):
            if body[h.start:h.end] != h.surface:
                continue
            body = body[:h.start] + h.repl + body[h.end:]
        lines[ln - 1] = body + ("\r" if cr else "")
    return "\n".join(lines)


def actionable(h):
    return h.repl is not None and h.repl != h.surface and h.kind not in ("plain", "official-ok")


def fix(root, hits, texts):
    from gate import proven_same_program
    by_file = defaultdict(list)
    for h in hits:
        if actionable(h) and h.editable:
            by_file[h.file].append(h)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S") + "-terms"
    bdir = HERE / "backups" / stamp
    changed, log, applied_ids, held = [], [], set(), {}
    for rel, hs in sorted(by_file.items()):
        path = root / rel
        old = texts[rel]
        new = apply_to_text(old, hs)
        if new == old:
            continue
        area = hs[0].area
        if area == "comment":
            if not proven_same_program(root, [(rel, old, new)]):
                for h in hs:
                    held[id(h)] = "comment in a file where Arachnid cannot PROVE the program stays identical"
                continue
        with open(path, encoding="utf-8", newline="") as fh:
            disk = fh.read()
        if disk != old:
            continue                                         # changed while we were working: do not touch it
        if not os.access(path, os.W_OK):                     # read-only: left exactly as it is (permissions never changed)
            for h in hs:
                held[id(h)] = "the file is read-only"
            continue
        dest = bdir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(new)
        changed.append(rel)
        for h in hs:
            applied_ids.add(id(h))
            log.append({"file": rel, "line": h.line, "from": h.surface, "to": h.repl})
    if changed:
        (bdir / "terms-manifest.json").write_text(json.dumps({"root": str(root), "files": changed, "fixes": log}, indent=2), encoding="utf-8")
        print("\n---------------- Auto-fix ----------------")
        for x in log[:40]:
            print("  %s:%d   '%s'  ->  '%s'" % (x["file"], x["line"], x["from"], x["to"]))
        if len(log) > 40:
            print("  ... and %d more" % (len(log) - 40))
        print("\nChanged %d term(s) in %d file(s).  Backup: %s\nTo put everything back:  python terms.py --undo" % (len(log), len(changed), bdir))
    else:
        print("\nNothing to change automatically.")
    for h in hits:
        if id(h) in held:
            h.why_not = held[id(h)]
            h.editable = False
    return applied_ids


def undo():
    base = HERE / "backups"
    sessions = sorted(p for p in base.glob("*") if (p / "terms-manifest.json").exists()) if base.exists() else []
    if not sessions:
        sys.exit("No terminology backups found - nothing to undo.")
    last = sessions[-1]
    man = json.loads((last / "terms-manifest.json").read_text(encoding="utf-8"))
    root = Path(man["root"])
    for rel in man["files"]:
        shutil.copy2(last / rel, root / rel)
    (last / "terms-manifest.json").rename(last / "terms-manifest.undone.json")
    print("Restored %d file(s) from backup %s." % (len(man["files"]), last.name))


# ---------------------------------------------------------------------------------------------------------------
def record_ledger(hits, applied):
    try:
        import ledger
        ledger.reset("terms")
        for h in hits:
            if h.kind == "official-ok":
                continue
            if h.kind == "plain":
                ledger.record("terms", "idea", "info", h.file, h.line, "plainer wording: '%s' -> '%s'. %s" % (h.surface, h.repl, h.why_not), kind="plain-wording")
            elif id(h) in applied:
                ledger.record("terms", "fixed", "info", h.file, h.line, "'%s' -> '%s'" % (h.surface, h.repl), kind="terminology")
            elif actionable(h) and h.editable:
                ledger.record("terms", "fixable", "info", h.file, h.line, "'%s' -> '%s'" % (h.surface, h.repl), kind="terminology")
            elif actionable(h):
                ledger.record("terms", "decision", "info", h.file, h.line, "'%s' should be '%s' (not changed: %s)" % (h.surface, h.repl, h.why_not), kind="terminology")
    except Exception:
        pass


def main():
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Arachnid terminology - scan, sense, improve (meaning-safe)")
    ap.add_argument("root", nargs="?", default=str(HERE.parent))
    ap.add_argument("--fix", action="store_true")
    ap.add_argument("--all", action="store_true", help="also list plainer-wording ideas")
    ap.add_argument("--out", default=str(HERE / "reports" / "terms-report.json"))
    ap.add_argument("--undo", action="store_true")
    a = ap.parse_args()
    if a.undo:
        return undo()
    glossary = load_glossary()
    root, hits, areas, texts = scan(a.root, glossary)
    print("Arachnid terminology - looking at: %s" % root)
    print("\nSTEP 1 - SCAN   read: " + (", ".join("%d %s" % (v, k) for k, v in areas.items()) if areas else "no text areas found"))
    plan = sense(hits)
    print("\nSTEP 2 - SENSE  what the text needs:")
    if not plan:
        print("   Terms are already spelled and used consistently. Nothing to improve.")
    for kind, fam, counts, decision, n in plan:
        seen = ", ".join("%s x%d" % kv for kv in sorted(counts.items(), key=lambda kv: -kv[1])[:5])
        print("   %-26s seen as: %s" % (fam, seen))
        print("   %-26s -> %s%s" % ("", decision, ("   [%d to change]" % n) if n else ""))
    todo = [h for h in hits if actionable(h)]
    now = [h for h in todo if h.editable]
    print("\nSTEP 3 - FIX    %d change(s) are safe to apply automatically%s; %d are reported only (page text, comments that cannot be proven safe)" % (
        len(now), "" if not a.fix else " (applying now)", len(todo) - len(now)))
    shown = 0
    cur = None
    for h in sorted(todo, key=lambda h: (h.file, h.line)):
        if shown >= 60:
            break
        if h.file != cur:
            cur = h.file
            print("\n" + h.file)
        shown += 1
        print("  line %-5d '%s'  ->  '%s'   %s" % (h.line, h.surface, h.repl, "[auto]" if h.editable else "[report only: %s]" % h.why_not))
    if len(todo) > shown:
        print("\n  ... and %d more (all in the report file)" % (len(todo) - shown))
    plain = [h for h in hits if h.kind == "plain"]
    if plain and a.all:
        print("\nPlainer-wording ideas (suggestions only, never applied):")
        for h in plain[:20]:
            print("  %s:%d  '%s' -> '%s'   %s" % (h.file, h.line, h.surface, h.repl, h.why_not))
    elif plain:
        print("\n%d plainer-wording idea(s) available (add --all to see them). They are never applied automatically." % len(plain))
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps({"root": str(root), "plan": [{"kind": k, "term": f, "seen": c, "decision": d, "changes": n} for k, f, c, d, n in plan],
                                       "changes": [{"file": h.file, "line": h.line, "from": h.surface, "to": h.repl, "auto": h.editable, "reason": h.why_not}
                                                   for h in todo]}, indent=1), encoding="utf-8")
    print("\nFull report saved to: %s" % a.out)
    applied = set()
    if a.fix:
        applied = fix(root, hits, texts)
    else:
        if now:
            print("To apply the safe changes:  python terms.py --fix")
    record_ledger(hits, applied)


if __name__ == "__main__":
    main()
