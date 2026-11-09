#!/usr/bin/env python3
"""
ARACHNID - KNOWN SECURITY HOLES IN YOUR LIBRARIES  (looks only, never changes anything)
=====================================================================================
    python vulncheck.py [folder]             look up every library version your project installs
    python vulncheck.py [folder] --offline   do nothing (same as ARACHNID_OFFLINE=1 or "vulncheck": false in arachnid.config.json)

Your project is built on libraries other people wrote.  When someone discovers a security hole in one of them, it is
published in public databases (CVE, GitHub advisories ...).  This check takes the EXACT version of every library your
project installs - read from its lock files - and looks each one up in OSV (https://osv.dev, a free database run by
Google that combines all of those sources).

What is sent: only library NAMES and VERSIONS (e.g. "lodash 4.17.15").  Never your code, file names or anything else.
If you prefer nothing to leave your computer, switch it off (see above).

How sure it is
  ALARM     a CRITICAL or HIGH hole in a library your project lists itself
  DECISION  MODERATE / LOW / unrated holes, and HIGH/CRITICAL ones in libraries that came in through another library
Each finding names the safe version to update to.  Nothing is updated automatically: a new version can change how
your product behaves, so a person updates it and runs the tests.

Exact versions are read from: package-lock.json, yarn.lock, pnpm-lock.yaml, package.json (exact pins), requirements*.txt
(==), Pipfile.lock, poetry.lock, uv.lock, pyproject.toml (==), go.mod, Cargo.lock, Gemfile.lock, composer.lock, pom.xml,
*.csproj, pubspec.lock.  A library given only as a range (^1.2.0) without a lock file is not looked up - its real
version is unknown, and guessing would raise false alarms.
"""
import argparse
import concurrent.futures
import json
import math
import os
import posixpath
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import dedupe  # noqa: E402
import ledger  # noqa: E402

OSV = os.environ.get("ARACHNID_OSV_URL", "https://api.osv.dev").rstrip("/")
CACHE = HERE / "reports" / "osv-cache.json"
TIMEOUT = 25
LOCKS = {"package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "package.json", "Pipfile.lock",
         "poetry.lock", "uv.lock", "pyproject.toml", "go.mod", "Cargo.lock", "Gemfile.lock", "composer.lock", "pom.xml",
         "pubspec.lock"}


def _line(text, pos):
    return text.count("\n", 0, pos) + 1


def _pkg(eco, name, ver, rel, line, direct):
    ver = (ver or "").strip().lstrip("=v").strip()
    if not name or not re.match(r"^\d", ver):
        return None
    return {"eco": eco, "name": name, "ver": ver, "file": rel, "line": line, "direct": direct}


# ------------------------------------------------------------------------------------------------- readers
def read_npm_lock(rel, text, direct_names):
    out = []
    try:
        data = json.loads(text)
    except ValueError:
        return out
    pk = data.get("packages")
    if isinstance(pk, dict):
        for path, info in pk.items():
            if not path or not isinstance(info, dict) or info.get("link"):
                continue
            name = info.get("name") or path.split("node_modules/")[-1]
            top = path.count("node_modules/") == 1
            hit = text.find('"%s"' % path)
            p = _pkg("npm", name, info.get("version"), rel, _line(text, hit) if hit >= 0 else 1, top and name in direct_names)
            if p:
                out.append(p)
        return out

    def walk(deps, depth):
        for name, info in (deps or {}).items():
            if isinstance(info, dict):
                hit = text.find('"%s"' % name)
                p = _pkg("npm", name, info.get("version"), rel, _line(text, hit) if hit >= 0 else 1, depth == 0 and name in direct_names)
                if p:
                    out.append(p)
                walk(info.get("dependencies"), depth + 1)
    walk(data.get("dependencies"), 0)
    return out


def read_yarn_lock(rel, text, direct_names):
    out = []
    for m in re.finditer(r'(?m)^"?((?:@[^@/\s"]+/)?[^@\s",]+)@[^\n]*:\s*\n(?:[ \t]+[^\n]*\n)*?[ \t]+version:?\s+"?([^"\s]+)"?', text):
        p = _pkg("npm", m.group(1), m.group(2), rel, _line(text, m.start()), m.group(1) in direct_names)
        if p:
            out.append(p)
    return out


def read_pnpm_lock(rel, text, direct_names):
    out = []
    for m in re.finditer(r"(?m)^  '?/?((?:@[^@/\s']+/)?[^@\s'/(]+)[@/](\d[^:('\s]*)", text):
        p = _pkg("npm", m.group(1), m.group(2), rel, _line(text, m.start()), m.group(1) in direct_names)
        if p:
            out.append(p)
    return out


def read_package_json(rel, text):
    out, names = [], set()
    try:
        data = json.loads(text)
    except ValueError:
        return out, names
    for sect in ("dependencies", "devDependencies", "optionalDependencies"):
        for name, spec in (data.get(sect) or {}).items():
            names.add(name)
            if isinstance(spec, str) and re.fullmatch(r"=?v?\d+\.\d+\.\d+([-+][\w.]+)?", spec.strip()):
                hit = text.find('"%s"' % name)
                p = _pkg("npm", name, spec, rel, _line(text, hit) if hit >= 0 else 1, True)
                if p:
                    out.append(p)
    return out, names


def read_requirements(rel, text):
    out = []
    for m in re.finditer(r"(?m)^\s*([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?\s*===?\s*([0-9][^\s;#,]*)", text):
        p = _pkg("PyPI", m.group(1), m.group(2), rel, _line(text, m.start()), True)
        if p:
            out.append(p)
    return out


def read_pipfile_lock(rel, text):
    out = []
    try:
        data = json.loads(text)
    except ValueError:
        return out
    for sect in ("default", "develop"):
        for name, info in (data.get(sect) or {}).items():
            if isinstance(info, dict):
                hit = text.find('"%s"' % name)
                p = _pkg("PyPI", name, (info.get("version") or "").lstrip("="), rel, _line(text, hit) if hit >= 0 else 1, True)
                if p:
                    out.append(p)
    return out


def read_toml_packages(rel, text, eco, direct_names=None):
    """poetry.lock / uv.lock / Cargo.lock: [[package]] name = "x" version = "y" """
    out = []
    for m in re.finditer(r'\[\[package\]\]\s*\nname\s*=\s*"([^"]+)"\s*\nversion\s*=\s*"([^"]+)"', text):
        direct = True if direct_names is None else m.group(1).lower() in direct_names
        p = _pkg(eco, m.group(1), m.group(2), rel, _line(text, m.start()), direct)
        if p:
            out.append(p)
    return out


def read_go_mod(rel, text):
    out = []
    for m in re.finditer(r"(?m)^\s*(?:require\s+)?([\w.-]+\.[\w./-]+)\s+v(\d[^\s]*)(\s*//\s*indirect)?", text):
        p = _pkg("Go", m.group(1), m.group(2), rel, _line(text, m.start()), not m.group(3))
        if p:
            out.append(p)
    return out


def read_gemfile_lock(rel, text):
    out = []
    specs = re.search(r"(?ms)^GEM\b.*?^  specs:\n(.*?)(?=^\S|\Z)", text)
    direct = set(re.findall(r"(?m)^  ([\w.-]+)", (re.search(r"(?ms)^DEPENDENCIES\n(.*?)(?=^\S|\Z)", text) or [None, ""])[1] if
                 re.search(r"(?ms)^DEPENDENCIES\n(.*?)(?=^\S|\Z)", text) else ""))
    if specs:
        base = specs.start(1)
        for m in re.finditer(r"(?m)^    ([\w.-]+) \(([^)]+)\)", specs.group(1)):
            p = _pkg("RubyGems", m.group(1), m.group(2).split("-")[0], rel, _line(text, base + m.start()), m.group(1) in direct)
            if p:
                out.append(p)
    return out


def read_composer_lock(rel, text):
    out = []
    try:
        data = json.loads(text)
    except ValueError:
        return out
    for sect in ("packages", "packages-dev"):
        for info in data.get(sect) or []:
            name = info.get("name")
            hit = text.find('"%s"' % name)
            p = _pkg("Packagist", name, info.get("version"), rel, _line(text, hit) if hit >= 0 else 1, True)
            if p:
                out.append(p)
    return out


def read_pom(rel, text):
    out = []
    for m in re.finditer(r"<dependency>(.*?)</dependency>", text, re.S):
        b = m.group(1)
        g = re.search(r"<groupId>\s*([^<\s]+)", b)
        a = re.search(r"<artifactId>\s*([^<\s]+)", b)
        v = re.search(r"<version>\s*([^<\s$]+)\s*<", b)
        if g and a and v:
            p = _pkg("Maven", "%s:%s" % (g.group(1), a.group(1)), v.group(1), rel, _line(text, m.start()), True)
            if p:
                out.append(p)
    return out


def read_csproj(rel, text):
    out = []
    for m in re.finditer(r'<PackageReference\s+Include="([^"]+)"\s+Version="([^"*\[\]()]+)"', text):
        p = _pkg("NuGet", m.group(1), m.group(2), rel, _line(text, m.start()), True)
        if p:
            out.append(p)
    return out


def read_pubspec_lock(rel, text):
    out = []
    for m in re.finditer(r'(?m)^  ([\w]+):\n(?:    [^\n]*\n)*?    version: "?([^"\s]+)"?', text):
        p = _pkg("Pub", m.group(1), m.group(2), rel, _line(text, m.start()), True)
        if p:
            out.append(p)
    return out


def collect(root):
    """Every (ecosystem, name, exact version) the project installs, with where it is written."""
    project = dedupe.Project(root)
    by_dir = {}
    for rel in project.files:
        if "node_modules/" in rel or "/vendor/" in "/" + rel:
            continue
        name = posixpath.basename(rel)
        if name in LOCKS or name.endswith(".csproj") or re.fullmatch(r"requirements[\w.-]*\.txt", name):
            by_dir.setdefault(posixpath.dirname(rel), []).append(rel)
    found = []
    for d, rels in by_dir.items():
        texts = {}
        for rel in rels:
            try:
                texts[posixpath.basename(rel)] = (rel, (project.root / rel).read_text(encoding="utf-8", errors="replace"))
            except OSError:
                pass
        direct_npm = set()
        pj_pins = []
        if "package.json" in texts:
            pj_pins, direct_npm = read_package_json(*texts["package.json"])
        npm_lock = [n for n in ("package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml") if n in texts]
        npm_start = len(found)
        if "package-lock.json" in texts or "npm-shrinkwrap.json" in texts:
            found += read_npm_lock(*texts["package-lock.json" if "package-lock.json" in texts else "npm-shrinkwrap.json"], direct_npm)
        elif "yarn.lock" in texts:
            found += read_yarn_lock(*texts["yarn.lock"], direct_npm)
        elif "pnpm-lock.yaml" in texts:
            found += read_pnpm_lock(*texts["pnpm-lock.yaml"], direct_npm)
        if not npm_lock:
            found += pj_pins
        elif "package.json" in texts:                # point libraries you list yourself at package.json - that is what you edit
            pj_rel, pj_text = texts["package.json"]
            for q in found[npm_start:]:
                if q["direct"]:
                    hit = pj_text.find('"%s"' % q["name"])
                    if hit >= 0:
                        q["file"], q["line"] = pj_rel, _line(pj_text, hit)
        py_direct = set()
        for n, (rel, t) in texts.items():
            if n.startswith("requirements"):
                py_direct |= {p["name"].lower() for p in read_requirements(rel, t)}
        if "pyproject.toml" in texts:
            py_direct |= {x.lower() for x in re.findall(r'"\s*([A-Za-z0-9][\w.-]*)\s*(?:\[[^\]]*\])?\s*[<>=~!^]', texts["pyproject.toml"][1])}
            py_direct |= {x.lower() for x in re.findall(r'(?m)^([A-Za-z0-9][\w.-]*)\s*=\s*["{]', texts["pyproject.toml"][1])}
        if "Pipfile.lock" in texts:
            found += read_pipfile_lock(*texts["Pipfile.lock"])
        elif "poetry.lock" in texts or "uv.lock" in texts:
            n = "poetry.lock" if "poetry.lock" in texts else "uv.lock"
            found += read_toml_packages(*texts[n], "PyPI", py_direct or None)
        else:
            for n, (rel, t) in texts.items():
                if n.startswith("requirements"):
                    found += read_requirements(rel, t)
            if "pyproject.toml" in texts:
                found += read_requirements(*texts["pyproject.toml"])
        if "go.mod" in texts:
            found += read_go_mod(*texts["go.mod"])
        if "Cargo.lock" in texts:
            found += read_toml_packages(*texts["Cargo.lock"], "crates.io")
        if "Gemfile.lock" in texts:
            found += read_gemfile_lock(*texts["Gemfile.lock"])
        if "composer.lock" in texts:
            found += read_composer_lock(*texts["composer.lock"])
        if "pom.xml" in texts:
            found += read_pom(*texts["pom.xml"])
        if "pubspec.lock" in texts:
            found += read_pubspec_lock(*texts["pubspec.lock"])
        for n, (rel, t) in texts.items():
            if n.endswith(".csproj"):
                found += read_csproj(rel, t)
    uniq = {}
    for p in found:
        key = (p["eco"], p["name"].lower() if p["eco"] == "PyPI" else p["name"], p["ver"])
        if key not in uniq or (p["direct"] and not uniq[key]["direct"]):
            uniq[key] = p
    return list(uniq.values())


# ------------------------------------------------------------------------------------------------- severity
def cvss3_score(vector):
    """Base score from a CVSS v3.x vector (the published formula)."""
    try:
        m = dict(x.split(":") for x in vector.split("/")[1:])
        av = {"N": 0.85, "A": 0.62, "L": 0.55, "P": 0.2}[m["AV"]]
        ac = {"L": 0.77, "H": 0.44}[m["AC"]]
        ui = {"N": 0.85, "R": 0.62}[m["UI"]]
        changed = m["S"] == "C"
        pr = {"N": 0.85, "L": 0.68 if changed else 0.62, "H": 0.5 if changed else 0.27}[m["PR"]]
        c, i, a = ({"H": 0.56, "L": 0.22, "N": 0}[m[k]] for k in ("C", "I", "A"))
    except (KeyError, ValueError):
        return None
    iss = 1 - (1 - c) * (1 - i) * (1 - a)
    impact = 7.52 * (iss - 0.029) - 3.25 * (iss - 0.02) ** 15 if changed else 6.42 * iss
    if impact <= 0:
        return 0.0
    expl = 8.22 * av * ac * pr * ui
    raw = min(1.08 * (impact + expl), 10) if changed else min(impact + expl, 10)
    return math.ceil(raw * 10 - 1e-9) / 10


def level_of(v):
    s = ((v.get("database_specific") or {}).get("severity") or "").upper()
    if s in ("CRITICAL", "HIGH", "MODERATE", "MEDIUM", "LOW"):
        return "MODERATE" if s == "MEDIUM" else s
    for sev in v.get("severity") or []:
        if str(sev.get("type", "")).startswith("CVSS_V3"):
            sc = cvss3_score(sev.get("score", ""))
            if sc is not None:
                return "CRITICAL" if sc >= 9 else "HIGH" if sc >= 7 else "MODERATE" if sc >= 4 else "LOW"
    for a in v.get("affected") or []:
        s = ((a.get("ecosystem_specific") or {}).get("severity") or (a.get("database_specific") or {}).get("severity") or "")
        if str(s).upper() in ("CRITICAL", "HIGH", "MODERATE", "MEDIUM", "LOW"):
            return "MODERATE" if str(s).upper() == "MEDIUM" else str(s).upper()
    return "UNRATED"


RANK = {"CRITICAL": 4, "HIGH": 3, "MODERATE": 2, "LOW": 1, "UNRATED": 0}


def vkey(v):
    """Loose version order that works across ecosystems: 1.10.0 > 1.9.2, 2.0.0rc1 < 2.0.0."""
    parts = re.findall(r"\d+|[a-zA-Z]+", v)
    out = []
    for p in parts:
        if p.isdigit():
            out.append((1, int(p), ""))
        else:
            out.append((0, 0, p.lower()))      # letters (rc, beta) sort below the release
    return out + [(0.5, 0, "")]


def fixed_version(v, pkg):
    best = None
    for a in v.get("affected") or []:
        pn = (a.get("package") or {}).get("name", "")
        if pn.lower() != pkg["name"].lower():
            continue
        for r in a.get("ranges") or []:
            intro, cur = "0", vkey(pkg["ver"])
            for ev in r.get("events") or []:
                if "introduced" in ev:
                    intro = ev["introduced"]
                elif "fixed" in ev:
                    fx = ev["fixed"]
                    if (intro == "0" or vkey(intro) <= cur) and cur < vkey(fx):
                        if best is None or vkey(fx) < vkey(best):
                            best = fx
    return best


# ------------------------------------------------------------------------------------------------- OSV
def _post(path, body):
    req = urllib.request.Request(OSV + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json",
                                 "User-Agent": "arachnid-vulncheck"}, method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def _get(path):
    req = urllib.request.Request(OSV + path, headers={"User-Agent": "arachnid-vulncheck"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def lookup(pkgs):
    """{index in pkgs: [vulnerability records]} - raises OSError when the database cannot be reached."""
    ids_for = {}
    for start in range(0, len(pkgs), 1000):
        chunk = pkgs[start:start + 1000]
        queries = [{"package": {"name": p["name"], "ecosystem": p["eco"]}, "version": p["ver"]} for p in chunk]
        res = _post("/v1/querybatch", {"queries": queries}).get("results") or []
        for i, r in enumerate(res):
            vulns = [v["id"] for v in (r or {}).get("vulns") or []]
            token = (r or {}).get("next_page_token")
            while token:
                more = _post("/v1/query", dict(queries[i], page_token=token))
                vulns += [v["id"] for v in more.get("vulns") or []]
                token = more.get("next_page_token")
            if vulns:
                ids_for[start + i] = vulns
    try:
        cache = json.loads(CACHE.read_text(encoding="utf-8"))
        if time.time() - cache.get("_saved", 0) > 86400:
            cache = {}
    except (OSError, ValueError):
        cache = {}
    need = sorted({i for ids in ids_for.values() for i in ids if i not in cache})

    def fetch(i):
        try:
            return i, _get("/v1/vulns/" + urllib.request.quote(i, safe=""))
        except (OSError, ValueError):
            return i, None
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        for i, rec in ex.map(fetch, need):
            if rec:
                cache[i] = {k: rec.get(k) for k in ("id", "summary", "details", "aliases", "severity", "database_specific", "affected")}
    try:
        cache["_saved"] = cache.get("_saved") or time.time()
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(cache), encoding="utf-8")
    except OSError:
        pass
    return {k: [cache[i] for i in ids if i in cache] for k, ids in ids_for.items()}


def merge_aliases(records):
    """The same hole often has several ids (GHSA-..., PYSEC-..., CVE-...): keep one per hole."""
    groups = []
    for r in records:
        names = {r["id"]} | set(r.get("aliases") or [])
        for g in groups:
            if g["names"] & names:
                g["names"] |= names
                g["recs"].append(r)
                break
        else:
            groups.append({"names": names, "recs": [r]})
    return groups


def findings(pkgs, results):
    out = []
    for idx, recs in results.items():
        p = pkgs[idx]
        holes = []
        for g in merge_aliases(recs):
            best = max(g["recs"], key=lambda r: RANK[level_of(r)])
            cve = sorted(n for n in g["names"] if n.startswith("CVE-"))
            holes.append({"level": max((level_of(r) for r in g["recs"]), key=lambda s: RANK[s]),
                          "id": cve[0] if cve else best["id"], "summary": (best.get("summary") or best.get("details") or "").strip().split("\n")[0][:110],
                          "fix": max((fixed_version(r, p) for r in g["recs"] if fixed_version(r, p)), key=vkey, default=None)})
        if not holes:
            continue
        holes.sort(key=lambda h: -RANK[h["level"]])
        worst = holes[0]["level"]
        fixes = [h["fix"] for h in holes if h["fix"]]
        safe = max(fixes, key=vkey) if fixes else None
        alarm = p["direct"] and worst in ("CRITICAL", "HIGH")
        what = "%s %s has %d known security hole%s (worst: %s) - %s: %s" % (
            p["name"], p["ver"], len(holes), "" if len(holes) == 1 else "s", worst.lower(), holes[0]["id"], holes[0]["summary"] or "see the advisory")
        why = ("attackers know about published holes and look for apps that still use the old version"
               + ("" if p["direct"] else " (this library came in through another library you use)"))
        todo = ("update %s to %s or newer, then run your tests" % (p["name"], safe) if safe else
                "no fixed version is published yet - check the advisory for a workaround, or replace the library")
        if len(holes) > 1:
            todo += "; also: " + ", ".join("%s (%s)" % (h["id"], h["level"].lower()) for h in holes[1:4]) + ("..." if len(holes) > 4 else "")
        out.append({"file": p["file"], "line": p["line"], "severity": "critical" if alarm else "warning", "what": what, "why": why,
                    "todo": todo, "pkg": p, "holes": holes, "safe": safe})
    return out


def switched_off(root):
    if os.environ.get("ARACHNID_OFFLINE") in ("1", "true", "yes"):
        return True
    try:
        cfg = json.loads((Path(root) / "arachnid.config.json").read_text(encoding="utf-8"))
        return cfg.get("vulncheck") is False
    except (OSError, ValueError):
        return False


def main():
    ap = argparse.ArgumentParser(description="Arachnid known security holes in libraries (looks only)")
    ap.add_argument("root", nargs="?", default=str(HERE.parent))
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--out", default=str(HERE / "reports" / "vuln-report.json"))
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    print("Arachnid known security holes - looking at:", Path(a.root).resolve())
    ledger.reset("vulns")
    if a.offline or switched_off(a.root):
        print("Switched off (offline mode) - nothing was looked up.")
        return 0
    pkgs = collect(a.root)
    print("Library versions found: %d" % len(pkgs))
    if not pkgs:
        return 0
    try:
        results = lookup(pkgs)
    except (OSError, ValueError) as e:
        print("Could not reach the vulnerability database (%s) - this check was skipped. Your code was not sent anywhere."
              % str(getattr(e, "reason", e))[:80])
        return 0
    found = findings(pkgs, results)
    for f in sorted(found, key=lambda f: (f["severity"] != "critical", f["file"], f["line"])):
        print("\n  %s  line %d   [%s]" % (f["file"], f["line"], "ALARM" if f["severity"] == "critical" else "DECISION"))
        print("     %s" % f["what"])
        print("     fix: %s" % f["todo"])
        ledger.record("vulns", "found", f["severity"], f["file"], f["line"], f["what"], f["why"], f["todo"], "known-vulnerability")
    crit = sum(1 for f in found if f["severity"] == "critical")
    print("\n---------------- Summary ----------------")
    print("Libraries looked up: %d    With known holes: %d   (alarms %d)" % (len(pkgs), len(found), crit))
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps({"root": str(Path(a.root).resolve()),
                                       "findings": [{k: v for k, v in f.items() if k != "pkg"} | {"package": f["pkg"]} for f in found]},
                                      indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
