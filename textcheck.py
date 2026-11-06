#!/usr/bin/env python3
"""
ARACHNID - Step 8: TEXT CHECKER  ("meaning breakers")
=====================================================
    python textcheck.py [folder]                look only: list every place where the writing says the wrong thing
    python textcheck.py [folder] --fix          fix the clear ones in documentation files (.md .txt .rst), with backup
    python textcheck.py --undo                  put the last text fix back

Spelling (spellcheck.py) finds words that do not exist.  This tool finds words that DO exist but are wrong, missing,
doubled or left over, so the sentence says something other than what you meant:

    "their is a problem"        every word is spelled right - the meaning is still broken (should be: there is)
    "the the"                   a word typed twice
    "your welcome"              means "belonging to you welcome" (should be: you're welcome)
    "Lorem ipsum"               placeholder text left on a real page
    "three (4) options"         the word and the number disagree
    "31 February"               a date that does not exist
    "We offer fast and"         a sentence that stops in the middle

What it reads
  .md .mdx .txt .rst files and the VISIBLE text of .html pages (scripts, styles and code are skipped).
  English only.  Files that are mostly not English are skipped, so it never "corrects" another language.

What it changes
  Nothing, unless you add --fix, and then ONLY clear-cut fixes (repeated word, their is -> there is, should of ->
  should have, ...) in documentation files.  Text on web pages is part of the product, so it is only ever reported.
  Everything else (grammar a/an, odd punctuation, a sentence that stops short) is reported with a blunt explanation.
"""
import argparse
import bisect
import json
import os
import re
import shutil
import sys
from collections import Counter
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "vendor"))

SKIP_DIRS = {"node_modules", ".git", "dist", "build", "__pycache__", ".venv", "venv", "env", "site-packages", ".next", ".cache",
             "arachnid", "backups", "reports", "coverage", "target", "vendor", ".wrangler", ".vite", "bin", "obj"}
DOC_EXT = {"md", "mdx", "txt", "rst"}
PAGE_EXT = {"html", "htm"}
SKIP_NAMES = re.compile(r"^(requirements.*\.txt|robots\.txt|cmakelists\.txt|license.*|licence.*|copying.*|notice.*|.*\.lock|"
                        r"constraints.*\.txt|manifest\.txt|sources\.txt|ignore.*\.txt|.*-lock\..*)$", re.I)
MAX_BYTES = 400_000


def recase(src, repl):
    return (repl[0].upper() + repl[1:]) if src[:1].isupper() else repl


# ----------------------------------------------------------------------------------------------------------------
# RULES.  id, pattern, flags, confidence, auto-fixable, replacement(m), blunt explanation(m)
# ----------------------------------------------------------------------------------------------------------------
I = re.I
RULES = []


def rule(rid, pat, conf, fix, repl, blunt, flags=I, pages=True, exclude=None):
    RULES.append(dict(id=rid, rx=re.compile(pat, flags), conf=conf, fix=fix, repl=repl, blunt=blunt, pages=pages, exclude=exclude))


rule("repeated-word", r"\b([A-Za-z]{2,})(\s+)\1\b", "high", True,
     lambda m: m.group(1),
     lambda m: "You typed '%s' twice in a row. Readers trip over it and it looks careless. Delete one." % m.group(1),
     exclude=lambda m: m.group(1).lower() in {"had", "that", "bye", "no", "very", "so", "knock", "duran", "yo", "blah", "ha", "hello", "go", "boom"} or m.group(1).isupper())
rule("their-there", r"\b(their)\s+(is|are|was|were|will|has|have|isn't|aren't)\b", "high", True,
     lambda m: recase(m.group(1), "there") + " " + m.group(2),
     lambda m: "'their' means 'belonging to them'. '%s %s' needs 'there' (the place/existence word). As written it reads as nonsense." % (m.group(1), m.group(2)))
rule("your-youre", r"\b(your)\s+(a|an|the|not|going|being|doing|getting|welcome|already|ready|able|here|there)\b", "high", True,
     lambda m: recase(m.group(1), "you're") + " " + m.group(2),
     lambda m: "'your' means 'belonging to you'. '%s %s' needs 'you're' (you are). As written the sentence is broken." % (m.group(1), m.group(2)),
     flags=re.I)
rule("its-its", r"\b(its)\s+(a|an|not|been|going|just|because|too|okay|ok|easy|hard|important|possible|true|false)\b", "high", True,
     lambda m: recase(m.group(1), "it's") + " " + m.group(2),
     lambda m: "'its' means 'belonging to it'. '%s %s' needs 'it's' (it is / it has)." % (m.group(1), m.group(2)))
rule("its-own", r"\b(it's)\s+(own|purpose|size|colou?r)\b", "medium", False, None,
     lambda m: "'it's' means 'it is'. '%s %s' needs 'its' (belonging to it)." % (m.group(1), m.group(2)))
rule("then-than", r"\b(more|less|better|worse|bigger|smaller|faster|slower|higher|lower|greater|larger|older|newer|rather|easier|harder|cheaper)\s+(then)\b", "high", True,
     lambda m: m.group(1) + " " + recase(m.group(2), "than"),
     lambda m: "'then' is about time. Comparing things uses 'than': '%s than'." % m.group(1))
rule("modal-of", r"\b(could|would|should|must|might)\s+of\b", "high", True,
     lambda m: m.group(1) + " have",
     lambda m: "'%s of' is not a thing. It is '%s have' (people write 'of' because it sounds the same)." % (m.group(1), m.group(1)))
rule("alot", r"\balot\b", "high", True, lambda m: recase(m.group(0), "a lot"),
     lambda m: "'alot' is not a word. It is two words: 'a lot'.")
CONTR = {"dont": "don't", "cant": "can't", "wont": "won't", "isnt": "isn't", "arent": "aren't", "doesnt": "doesn't", "didnt": "didn't",
         "wasnt": "wasn't", "werent": "weren't", "couldnt": "couldn't", "shouldnt": "shouldn't", "wouldnt": "wouldn't", "hasnt": "hasn't",
         "havent": "haven't", "hadnt": "hadn't", "youre": "you're", "theyre": "they're", "thats": "that's", "whats": "what's", "ive": "I've"}
rule("missing-apostrophe", r"\b(" + "|".join(CONTR) + r")\b", "high", True,
     lambda m: recase(m.group(1), CONTR[m.group(1).lower()]) if m.group(1).lower() != "ive" else "I've",
     lambda m: ("'%s' without the apostrophe is %s. It means '%s'. Add the apostrophe." % (
         m.group(1), "a different real word ('wont' = a habit, 'cant' = hypocrisy)" if m.group(1).lower() in ("wont", "cant", "ive") else "wrong",
         CONTR[m.group(1).lower()])),
     exclude=lambda m: m.group(1).isupper())
rule("lowercase-i", r"\bi\s+(am|was|have|will|can|think|had|would|do|need|want|like|love|know|see|got|just|feel|hope|believe|guess|mean)\b", "high", True,
     lambda m: "I " + m.group(1), lambda m: "The word 'I' is always a capital letter. '%s' looks like a typing slip." % m.group(0), flags=0)
rule("to-much", r"\bto\s+(much|many)\b(?!\s+(?:of|people|users|more))", "medium", False, None,
     lambda m: "'to %s' - if you mean 'more than enough', it is 'too %s' (with two o's). 'to' is the direction word." % (m.group(1), m.group(1)))
rule("too-to", r"\btoo\s+(be|do|go|make|get|have|see|use|try|help|find|take|give)\b", "medium", False, None,
     lambda m: "'too %s' - 'too' means 'also / excessively'. Before a verb you almost certainly want 'to %s'." % (m.group(1), m.group(1)))
rule("affect-effect", r"\b(an?|the|no|any|positive|negative|big|major|little|great)\s+(affect)\b", "medium", False, None,
     lambda m: "'%s affect' - as a thing (a result) the word is 'effect'. 'Affect' is the verb (to influence)." % m.group(1))
rule("lose-loose", r"\b(?:will|can|could|might|may|would|not|never|didn't|won't|don't)\s+(loose)\b", "medium", False, None,
     lambda m: "'loose' means 'not tight'. To not win / to misplace something is 'lose' (one o).")
rule("not-not", r"\bnot\s+not\b", "medium", False, None, lambda m: "'not not' cancels itself out. You say the opposite of what you probably mean.")
rule("article-a-an", r"\ba\s+([aeiou][a-z]+)\b", "low", False, None,
     lambda m: "'a %s' - before a vowel sound it is 'an %s'." % (m.group(1), m.group(1)), flags=0,
     exclude=lambda m: m.group(1).startswith(("u", "eu", "one", "once", "ewe", "ou", "eve", "ear")) or m.group(1) in ("a",))
rule("article-an-a", r"\ban\s+([bcdfgjklmnpqrstvwxyz][a-z]+)\b", "low", False, None,
     lambda m: "'an %s' - before a consonant sound it is 'a %s'." % (m.group(1), m.group(1)), flags=0,
     exclude=lambda m: m.group(1).startswith(("hour", "honest", "honor", "honour", "heir", "herb")))
rule("space-before-punct", r"(?<=[A-Za-z])\s+[,;](?=\s)|(?<=[A-Za-z])\s+\.(?=\s|$)", "low", False, None,
     lambda m: "A space before '%s' - punctuation sticks to the word before it." % m.group(0).strip())
rule("double-punct", r"(?<![.\w])\.\.(?!\.)|,,|;;", "low", False, None, lambda m: "'%s' doubled punctuation - one is a typo." % m.group(0))
rule("missing-space", r"\b[a-z]{3,}[.!?][A-Z][a-z]{2,}\b", "medium", False, None,
     lambda m: "'%s' - no space after the full stop, so two sentences are glued into one word." % m.group(0),
     flags=0, exclude=lambda m: m.group(0).split(".")[0] in {"node", "vue", "next", "nuxt", "three", "chart", "socket", "express", "alpine", "ember"})
rule("lowercase-sentence-start", r"\b([a-z]{3,})[.!?]\s+([a-z]{3,})\b", "medium", False, None,
     lambda m: "A sentence starts with a small letter after '%s.' - either a capital is missing or the full stop is wrong." % m.group(1),
     flags=0, exclude=lambda m: m.group(1) in {"etc", "vs", "approx", "fig", "inc", "ltd", "dept", "est", "min", "max", "avg", "vol", "cf", "al", "co", "resp", "ca", "no", "nos", "mr", "mrs", "dr", "ms", "prof", "sr", "jr", "st", "viz"}
     or m.group(1) in {"com", "org", "net", "edu", "gov", "io", "co", "dev", "app", "html", "css", "js", "py", "json", "md", "txt", "png", "jpg", "pdf"})
rule("count-mismatch", r"\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s*\((\d+)\)", "high", False, None,
     lambda m: "The word says '%s' but the number in brackets says %s. They disagree - one of them is wrong." % (m.group(1), m.group(2)),
     exclude=lambda m: {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}[m.group(1).lower()] == int(m.group(2)))
rule("placeholder", r"lorem\s+ipsum|dolor\s+sit\s+amet|\b(?:your|add|insert)\s+(?:text|content|description|title|name|company)\s+here\b|\[(?:insert|your|add|company|name|description)[^\]]{0,30}\]|\bdummy\s+text\b|\bsample\s+text\b", "high", False, None,
     lambda m: "'%s' is placeholder text. Visitors will read it as it is. Replace it with the real content." % m.group(0)[:40])
rule("placeholder-todo", r"\b(?:TODO|TBD|FIXME|XXX+)\b", "medium", False, None,
     lambda m: "'%s' is a note-to-self left in text people will read. Finish it or remove it." % m.group(0), flags=0, pages=True)

MONTHS = {"january": 31, "february": 29, "march": 31, "april": 30, "may": 31, "june": 30, "july": 31, "august": 31, "september": 30,
          "october": 31, "november": 30, "december": 31}
DATE_RX = [re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(" + "|".join(MONTHS) + r")(?:,?\s+(\d{4}))?\b", re.I),
           re.compile(r"\b(" + "|".join(MONTHS) + r")\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?\b", re.I)]
MID_END = re.compile(r"\b(and|or|but|the|a|an|of|to|with|for|in|on|at|by|that|which|because|if|as|than)\s*[.!?]?\s*$", re.I)


def leap(y):
    return y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)


def date_problems(text):
    out = []
    for i, rx in enumerate(DATE_RX):
        for m in rx.finditer(text):
            day, mon, yr = (int(m.group(1)), m.group(2), m.group(3)) if i == 0 else (int(m.group(2)), m.group(1), m.group(3))
            mx = MONTHS[mon.lower()]
            if mon.lower() == "february" and yr:
                mx = 29 if leap(int(yr)) else 28
            if day < 1 or day > mx:
                out.append((m.start(), m.end(), m.group(0), "That date does not exist: %s has %s days%s." % (
                    mon.capitalize(), mx, (" in %s" % yr) if yr else "")))
    return out


# ----------------------------------------------------------------------------------------------------------------
# turning files into paragraphs of plain prose
# ----------------------------------------------------------------------------------------------------------------
URL = re.compile(r"https?://\S+|www\.\S+|\b[\w.+-]+@[\w-]+\.[\w.]+\b")
FENCE = re.compile(r"^\s*(```|~~~)")
LIST_MARK = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")


def clean_md(s):
    s = re.sub(r"^\s*>+\s?", "", s)
    s = re.sub(r"^\s*#{1,6}\s+", "", s)
    s = LIST_MARK.sub("", s)
    s = re.sub(r"`[^`]*`", " CODE ", s)
    s = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"\[([^\]]*)\]\[[^\]]*\]", r"\1", s)
    s = URL.sub("URL", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"&nbsp;|&amp;|&[a-z]+;", " ", s)
    s = re.sub(r"\*\*|__|~~|(?<![\w])_|_(?![\w])|\*", "", s)
    return s.strip()


def paragraphs_doc(text, ext):
    """-> list of paragraphs; a paragraph is a list of (line_number, cleaned_line)."""
    lines = text.split("\n")
    paras, cur = [], []
    in_fence, prev_blank = False, True
    start = 0
    if ext in ("md", "mdx") and lines and lines[0].strip() == "---":
        for k in range(1, min(len(lines), 60)):
            if lines[k].strip() == "---":
                start = k + 1
                break
    def flush():
        nonlocal cur
        if cur:
            paras.append(cur)
        cur = []
    for n in range(start, len(lines)):
        raw = lines[n].rstrip("\r")
        if ext in ("md", "mdx"):
            if FENCE.match(raw):
                in_fence = not in_fence
                flush()
                continue
            if in_fence:
                continue
            if raw.strip().startswith("|") or re.match(r"^\s*[-:| ]{3,}$", raw) or re.match(r"^\s*(\*\s*){3,}$", raw):
                flush()
                continue
            if (raw.startswith("\t") or raw.startswith("    ")) and prev_blank and not LIST_MARK.match(raw):
                prev_blank = False
                continue                                   # indented code block
        if not raw.strip():
            flush()
            prev_blank = True
            continue
        prev_blank = False
        if ext in ("md", "mdx") and (re.match(r"^\s*#{1,6}\s", raw) or LIST_MARK.match(raw)):
            flush()
        c = clean_md(raw) if ext in ("md", "mdx") else URL.sub("URL", raw.strip())
        if ext == "rst" and re.match(r"^[=\-~^\"#*+]{3,}$", raw.strip()):
            flush()
            continue
        if c:
            cur.append((n + 1, c))
        if ext in ("md", "mdx") and re.match(r"^\s*#{1,6}\s", raw):
            flush()
    flush()
    return paras


class VisibleText(HTMLParser):
    SKIP = {"script", "style", "noscript", "code", "pre", "svg", "template", "textarea", "kbd", "samp", "math"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.chunks = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.depth += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.depth:
            self.depth -= 1

    def handle_data(self, data):
        if not self.depth:
            t = " ".join(data.split())
            if t:
                self.chunks.append((self.getpos()[0], t))


def paragraphs_page(text):
    p = VisibleText()
    try:
        p.feed(text)
    except Exception:
        pass
    return [[(ln, URL.sub("URL", t))] for ln, t in p.chunks]


# ----------------------------------------------------------------------------------------------------------------
_speller = None


def looks_english(texts):
    global _speller
    words = [w.lower() for t in texts for w in re.findall(r"[A-Za-z]{3,}", t)]
    if len(words) < 30:
        return True
    try:
        if _speller is None:
            from spellchecker import SpellChecker
            _speller = SpellChecker()
        sample = words[:600]
        known = len(_speller.known(set(sample)))
        return known / max(1, len(set(sample))) >= 0.45
    except Exception:
        return True


def analyse_file(rel, text, ext):
    page = ext in PAGE_EXT
    paras = paragraphs_page(text) if page else paragraphs_doc(text, ext)
    if not looks_english([t for p in paras for _, t in p]):
        return None
    orig_lines = text.split("\n")
    out = []
    seen_sent = Counter()
    for para in paras:
        joined = " ".join(t for _, t in para)
        if len(joined.split()) < 2:
            continue
        starts, pos = [], 0
        for ln, t in para:
            starts.append(pos)
            pos += len(t) + 1

        def line_at(p):
            return para[max(0, bisect.bisect_right(starts, p) - 1)][0]

        def add(rid, conf, s, e, says, blunt, fixrepl=None, fixable=False):
            ln = line_at(s)
            single = line_at(s) == line_at(max(s, e - 1))
            ctx_a = max(0, s - 35)
            ctx_b = min(len(joined), e + 35)
            out.append({"file": rel, "line": ln, "rule": rid, "confidence": conf, "says": says,
                        "context": ("..." if ctx_a else "") + joined[ctx_a:ctx_b] + ("..." if ctx_b < len(joined) else ""),
                        "blunt": blunt, "fix": fixrepl, "fixable": bool(fixable and single and fixrepl is not None and ext in DOC_EXT)})
        for r in RULES:
            if page and not r["pages"]:
                continue
            for m in r["rx"].finditer(joined):
                if r["exclude"] and r["exclude"](m):
                    continue
                repl = r["repl"](m) if r["repl"] else None
                add(r["id"], r["conf"], m.start(), m.end(), m.group(0), r["blunt"](m), repl, r["fix"])
        for s, e, says, why in date_problems(joined):
            add("impossible-date", "high", s, e, says, why)
        if len(joined.split()) >= (6 if page else 4) and MID_END.search(joined) and not joined.rstrip().endswith(":"):
            m = MID_END.search(joined)
            add("ends-mid-thought", "medium", m.start(), len(joined), joined[-50:],
                "This text stops on '%s' - a word that always needs something after it. The sentence is unfinished." % m.group(1))
        for sent in re.split(r"(?<=[.!?])\s+", joined):
            norm = re.sub(r"\W+", " ", sent.lower()).strip()
            if len(norm.split()) >= 6:
                seen_sent[norm] += 1
                if seen_sent[norm] == 2:
                    add("repeated-sentence", "low", joined.find(sent), joined.find(sent) + len(sent), sent[:60],
                        "This exact sentence already appeared earlier in this file. Say it once.")
    seen_ph = set()
    keep = []
    for f in out:                                            # one placeholder note per line is enough
        if f["rule"] == "placeholder":
            if (f["line"]) in seen_ph:
                continue
            seen_ph.add(f["line"])
        keep.append(f)
    out = keep
    # attach exact-line replacement info for safe fixing
    for f in out:
        f["_orig_line"] = orig_lines[f["line"] - 1] if f["line"] - 1 < len(orig_lines) else ""
    return out


def collect(root):
    root = Path(root).resolve()
    files = []
    stack = [root]
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
            else:
                x = e.suffix.lower().lstrip(".")
                if (x in DOC_EXT or x in PAGE_EXT) and not SKIP_NAMES.match(e.name) and not e.name.endswith(".min.html"):
                    try:
                        if e.stat().st_size <= MAX_BYTES:
                            files.append(e)
                    except OSError:
                        pass
    return root, sorted(files)


def run(root):
    root, files = collect(root)
    findings, skipped = [], []
    for p in files:
        rel = p.relative_to(root).as_posix()
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        r = analyse_file(rel, text, p.suffix.lower().lstrip("."))
        if r is None:
            skipped.append(rel)
        else:
            findings += r
    return root, len(files), findings, skipped


# ----------------------------------------------------------------------------------------------------------------
def apply_fix_text(text, items):
    """Replace each fixable match on its own line. Returns (new_text, applied_items)."""
    lines = text.split("\n")
    applied = []
    for f in sorted(items, key=lambda f: (f["line"])):
        if not f["fixable"]:
            continue
        i = f["line"] - 1
        ln = lines[i]
        said = f["says"]
        if ln.count(said) != 1:
            continue
        a = ln.index(said)
        if ln[:a].count("`") % 2 == 1:
            continue                                         # inside a code span
        new = ln[:a] + f["fix"] + ln[a + len(said):]
        if new != ln:
            lines[i] = new
            applied.append(f)
    return "\n".join(lines), applied


def fix_files(root, findings):
    by_file = {}
    for f in findings:
        if f["fixable"]:
            by_file.setdefault(f["file"], []).append(f)
    if not by_file:
        print("\nNothing to fix automatically (only clear-cut mistakes in .md/.txt/.rst files are fixed by themselves).")
        return set()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S") + "-text"
    bdir = HERE / "backups" / stamp
    changed, log = [], []
    for rel, items in by_file.items():
        path = root / rel
        if not os.access(path, os.W_OK):           # read-only: left exactly as it is (permissions never changed)
            print("   SKIPPED %s - the file is read-only, so it is left as it is" % rel)
            continue
        with open(path, encoding="utf-8", newline="") as fh:
            text = fh.read()
        crlf = "\r\n" in text
        t = text.replace("\r\n", "\n") if crlf else text
        new, applied = apply_fix_text(t, items)
        if not applied:
            continue
        if crlf:
            new = new.replace("\n", "\r\n")
        dest = bdir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(new)
        changed.append(rel)
        for f in applied:
            log.append({"file": rel, "line": f["line"], "from": f["says"], "to": f["fix"], "rule": f["rule"]})
    if not changed:
        return set()
    (bdir / "text-manifest.json").write_text(json.dumps({"root": str(root), "files": changed, "fixes": log}, indent=2), encoding="utf-8")
    print("\n---------------- Auto-fix ----------------")
    for x in log[:40]:
        print("  %s:%d   '%s'  ->  '%s'" % (x["file"], x["line"], x["from"], x["to"]))
    print("\nFixed %d thing(s) in %d file(s).  Backup: %s\nTo put everything back:  python textcheck.py --undo" % (len(log), len(changed), bdir))
    return {(x["file"], x["line"], x["from"]) for x in log}


def undo():
    base = HERE / "backups"
    sessions = sorted(p for p in base.glob("*") if (p / "text-manifest.json").exists()) if base.exists() else []
    if not sessions:
        sys.exit("No text backups found - nothing to undo.")
    last = sessions[-1]
    man = json.loads((last / "text-manifest.json").read_text(encoding="utf-8"))
    root = Path(man["root"])
    for rel in man["files"]:
        shutil.copy2(last / rel, root / rel)
    (last / "text-manifest.json").rename(last / "text-manifest.undone.json")
    print("Restored %d file(s) from backup %s." % (len(man["files"]), last.name))


def record_ledger(findings, fixed):
    try:
        import ledger
        ledger.reset("text")
        for f in findings:
            what = "'%s' - %s" % (f["says"][:60], f["blunt"][:170])
            key = (f["file"], f["line"], f["says"])
            if key in fixed:
                ledger.record("text", "fixed", "info", f["file"], f["line"], what, kind=f["rule"])
            elif f["fixable"]:
                ledger.record("text", "fixable", "info", f["file"], f["line"], what, kind=f["rule"])
            else:
                visible = f["file"].lower().endswith((".html", ".htm"))
                sev = "warning" if f["confidence"] in ("high", "medium") and (visible or f["confidence"] == "high") else "info"
                ledger.record("text", "decision", sev, f["file"], f["line"], what,
                              "readers will read this wrong" if sev == "warning" else "", "reword it", f["rule"])
    except Exception:
        pass


def main():
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Arachnid text checker - meaning breakers in documentation and page text")
    ap.add_argument("root", nargs="?", default=str(HERE.parent))
    ap.add_argument("--fix", action="store_true", help="fix clear-cut mistakes in .md/.txt/.rst files (backup first)")
    ap.add_argument("--all", action="store_true", help="print every finding, including low-confidence ones")
    ap.add_argument("--out", default=str(HERE / "reports" / "text-report.json"))
    ap.add_argument("--undo", action="store_true")
    a = ap.parse_args()
    if a.undo:
        return undo()
    root, nfiles, findings, skipped = run(a.root)
    print("Arachnid text checker - looking at: %s" % root)
    print("   Text files read: %d%s" % (nfiles, ("   (skipped, not English: %s)" % ", ".join(skipped[:4])) if skipped else ""))
    order = {"high": 0, "medium": 1, "low": 2}
    shown = [f for f in findings if a.all or f["confidence"] != "low"]
    shown.sort(key=lambda f: (f["file"], f["line"]))
    cur = None
    for f in shown[:80]:
        if f["file"] != cur:
            cur = f["file"]
            print("\n" + cur)
        print("  line %-5d [%-6s] %s" % (f["line"], f["confidence"], f["rule"]))
        print("      says  : %s" % f["context"])
        print("      blunt : %s" % f["blunt"])
        if f["fix"] is not None:
            print("      fix   : %s%s" % (f["fix"], "   [can be fixed automatically]" if f["fixable"] else "   (suggestion - reword by hand)"))
    if len(shown) > 80:
        print("\n  ... and %d more (all in the report file)" % (len(shown) - 80))
    c = Counter(f["rule"] for f in findings)
    print("\n---------------- Summary ----------------")
    print("Meaning breakers found: %d   (high %d, medium %d, low %d)" % (
        len(findings), *(sum(1 for f in findings if f["confidence"] == k) for k in ("high", "medium", "low"))))
    if c:
        print("By kind: " + ", ".join("%s %d" % kv for kv in c.most_common(8)))
    print("Fixable automatically (clear-cut, in .md/.txt/.rst): %d" % sum(1 for f in findings if f["fixable"]))
    if not a.all and any(f["confidence"] == "low" for f in findings):
        print("(low-confidence hints are hidden; add --all to see them)")
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps({"root": str(root), "findings": [{k: v for k, v in f.items() if not k.startswith("_")} for f in findings]},
                                      indent=1, ensure_ascii=False), encoding="utf-8")
    print("Full report saved to: %s" % a.out)
    fixed = set()
    if a.fix:
        fixed = fix_files(root, findings)
    record_ledger(findings, fixed)


if __name__ == "__main__":
    main()
