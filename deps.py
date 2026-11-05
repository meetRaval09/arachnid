#!/usr/bin/env python3
"""
ARACHNID - dependency files of many languages (shared by insights.py, advisor.py and netcheck.py)
=================================================================================================
A "dependency file" is the list of outside libraries a project needs.  Every language has its own:

    Java      pom.xml (Maven), build.gradle / build.gradle.kts (Gradle)
    Go        go.mod                          Rust      Cargo.toml
    PHP       composer.json                   Ruby      Gemfile
    C# / .NET *.csproj, packages.config       Dart      pubspec.yaml
    Python    requirements.txt, pyproject.toml, Pipfile     JavaScript   package.json
    Swift     Package.swift

This module reads them all into one common shape, so the same questions can be asked of every language:
  * is the library actually used anywhere in the code?                (insights)
  * is there a lighter / built-in / maintained replacement?           (insights, advisor)
  * is the version pinned, and is it fetched over a safe address?     (netcheck)

Run on its own to see what it found:   python deps.py [folder]
"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

MANIFEST_NAMES = {"pom.xml", "build.gradle", "build.gradle.kts", "go.mod", "Cargo.toml", "composer.json", "Gemfile", "pubspec.yaml", "Pipfile",
                  "pyproject.toml", "packages.config", "Package.swift"}


def is_manifest(rel):
    n = rel.replace("\\", "/").split("/")[-1]
    return n in MANIFEST_NAMES or n.endswith(".csproj")


def ln_of(text, pos):
    return text.count("\n", 0, pos) + 1


def dep(eco, name, ver, scope, rel, line, **kw):
    d = {"eco": eco, "name": name, "ver": (ver or "").strip(), "scope": scope, "file": rel, "line": line}
    d.update(kw)
    return d


# ---------------------------------------------------------------------------------------------------------------
def parse(rel, text):
    n = rel.replace("\\", "/").split("/")[-1]
    out = []
    if n == "pom.xml":
        for m in re.finditer(r"<dependency>(.*?)</dependency>", text, re.S):
            b = m.group(1)
            g = re.search(r"<groupId>\s*([^<\s]+)\s*</groupId>", b)
            a = re.search(r"<artifactId>\s*([^<\s]+)\s*</artifactId>", b)
            v = re.search(r"<version>\s*([^<\s]*)\s*</version>", b)
            sc = re.search(r"<scope>\s*([^<\s]+)", b)
            if g and a:
                out.append(dep("java", "%s:%s" % (g.group(1), a.group(1)), v.group(1) if v else "", "dev" if sc and sc.group(1) == "test" else "runtime", rel, ln_of(text, m.start()), group=g.group(1), artifact=a.group(1)))
        for m in re.finditer(r"<(?:url)>\s*(http://[^<\s]+)", text):
            out.append(dep("java", "(repository)", "", "source", rel, ln_of(text, m.start()), insecure=m.group(1)))
    elif n.startswith("build.gradle"):
        for m in re.finditer(r"""^\s*(implementation|api|compileOnly|runtimeOnly|compile|testImplementation|testCompile|annotationProcessor|kapt|classpath)\s*\(?\s*['"]([^'":\s]+):([^'":\s]+)(?::([^'"\s)]+))?['"]""", text, re.M):
            out.append(dep("java", "%s:%s" % (m.group(2), m.group(3)), m.group(4) or "", "dev" if m.group(1).startswith("test") else "runtime", rel, ln_of(text, m.start()), group=m.group(2), artifact=m.group(3)))
        for m in re.finditer(r"""(?:url\s*(?:=\s*uri\()?\s*\(?|maven\s*\{?\s*url\s+)['"](http://[^'"]+)['"]""", text):
            out.append(dep("java", "(repository)", "", "source", rel, ln_of(text, m.start()), insecure=m.group(1)))
    elif n == "go.mod":
        for m in re.finditer(r"^\s*(?:require\s+)?([\w.\-]+\.[\w.\-]+/[\w.\-/]+)\s+(v[\w.\-+]+)(\s*//\s*indirect)?", text, re.M):
            out.append(dep("go", m.group(1), m.group(2), "indirect" if m.group(3) else "runtime", rel, ln_of(text, m.start())))
    elif n == "Cargo.toml":
        sect = None
        for i, raw in enumerate(text.split("\n"), 1):
            s = raw.strip()
            if s.startswith("["):
                sect = s
                continue
            m = re.match(r"([A-Za-z0-9_\-]+)\s*=\s*(.*)", s)
            if not m or not sect or not re.match(r"\[(?:target\.[^\]]+\.)?(dev-|build-)?dependencies\]", sect):
                continue
            val = m.group(2)
            v = re.search(r"""version\s*=\s*['"]([^'"]*)['"]""", val) or re.match(r"""['"]([^'"]*)['"]""", val)
            feats = re.search(r"features\s*=\s*\[([^\]]*)\]", val)
            out.append(dep("rust", m.group(1), v.group(1) if v else "", "dev" if "dev-" in sect else "runtime", rel, i,
                           git=bool(re.search(r"\bgit\s*=", val)), features=feats.group(1) if feats else "", path=bool(re.search(r"\bpath\s*=", val))))
    elif n == "composer.json":
        try:
            data = json.loads(text)
        except ValueError:
            return out
        for key, scope in (("require", "runtime"), ("require-dev", "dev")):
            for k, v in (data.get(key) or {}).items():
                if k == "php" or k.startswith("ext-"):
                    continue
                out.append(dep("php", k, str(v), scope, rel, next((i for i, l in enumerate(text.split("\n"), 1) if '"%s"' % k in l), 1)))
        for r in (data.get("repositories") or []) if isinstance(data.get("repositories"), list) else []:
            if isinstance(r, dict) and str(r.get("url", "")).startswith("http://"):
                out.append(dep("php", "(repository)", "", "source", rel, 1, insecure=r["url"]))
    elif n == "Gemfile":
        group_dev = False
        for i, raw in enumerate(text.split("\n"), 1):
            s = raw.strip()
            if re.match(r"group\s+.*:(development|test)", s):
                group_dev = True
            elif s == "end":
                group_dev = False
            m = re.match(r"""gem\s+['"]([^'"]+)['"](?:\s*,\s*['"]([^'"]+)['"])?(.*)""", s)
            if m:
                out.append(dep("ruby", m.group(1), m.group(2) or "", "dev" if group_dev else "runtime", rel, i, git=bool(re.search(r"\bgit(?:hub)?:", m.group(3))), ref=bool(re.search(r"\b(?:ref|tag|branch):", m.group(3)))))
            ms = re.match(r"""source\s+['"](http://[^'"]+)['"]""", s)
            if ms:
                out.append(dep("ruby", "(source)", "", "source", rel, i, insecure=ms.group(1)))
    elif n.endswith(".csproj") or n == "packages.config":
        for m in re.finditer(r"""<PackageReference\s+[^>]*Include\s*=\s*"([^"]+)"([^>]*?)(?:/>|>(.*?)</PackageReference>)""", text, re.S):
            v = re.search(r"""Version\s*=\s*"([^"]*)\"""", m.group(2) or "") or re.search(r"<Version>\s*([^<\s]+)", m.group(3) or "")
            out.append(dep("dotnet", m.group(1), v.group(1) if v else "", "runtime", rel, ln_of(text, m.start())))
        for m in re.finditer(r"""<package\s+id\s*=\s*"([^"]+)"\s+version\s*=\s*"([^"]*)\"""", text):
            out.append(dep("dotnet", m.group(1), m.group(2), "runtime", rel, ln_of(text, m.start())))
    elif n == "pubspec.yaml":
        sect = None
        for i, raw in enumerate(text.split("\n"), 1):
            if re.match(r"^(dependencies|dev_dependencies|dependency_overrides)\s*:", raw):
                sect = raw.split(":")[0]
                continue
            if raw and not raw.startswith((" ", "\t", "#")):
                sect = None
            m = re.match(r"^\s{2}([A-Za-z0-9_]+)\s*:\s*(.*)$", raw)
            if m and sect and m.group(1) not in ("flutter", "sdk", "flutter_test"):
                out.append(dep("dart", m.group(1), m.group(2).strip(), "dev" if sect == "dev_dependencies" else "runtime", rel, i))
    elif n == "Pipfile":
        sect = None
        for i, raw in enumerate(text.split("\n"), 1):
            s = raw.strip()
            if s.startswith("["):
                sect = s
                continue
            m = re.match(r"""([A-Za-z0-9_.\-]+)\s*=\s*['"]?([^'"\s{]*)""", s)
            if m and sect in ("[packages]", "[dev-packages]"):
                out.append(dep("python", m.group(1), m.group(2), "dev" if sect == "[dev-packages]" else "runtime", rel, i))
    elif n == "pyproject.toml":
        for m in re.finditer(r"(?ms)^dependencies\s*=\s*\[(.*?)\]", text):
            for q in re.finditer(r"""['"]([A-Za-z0-9_.\-]+)\s*([^'"]*)['"]""", m.group(1)):
                out.append(dep("python", q.group(1), q.group(2), "runtime", rel, ln_of(text, m.start(1) + q.start())))
        sect = None
        for i, raw in enumerate(text.split("\n"), 1):
            s = raw.strip()
            if s.startswith("["):
                sect = s
                continue
            m = re.match(r"""([A-Za-z0-9_.\-]+)\s*=\s*['"]([^'"]*)['"]""", s)
            if m and sect in ("[tool.poetry.dependencies]", "[tool.poetry.dev-dependencies]", "[tool.poetry.group.dev.dependencies]") and m.group(1).lower() != "python":
                out.append(dep("python", m.group(1), m.group(2), "runtime" if sect.endswith("poetry.dependencies]") else "dev", rel, i))
    elif n == "Package.swift":
        for m in re.finditer(r"""\.package\(\s*url:\s*"([^"]+)"[^)]*\)""", text):
            u = m.group(1)
            out.append(dep("swift", u.rstrip("/").split("/")[-1].replace(".git", ""), "from" if "from:" in m.group(0) else ("exact" if "exact:" in m.group(0) else ("branch" if "branch:" in m.group(0) else "")),
                           "runtime", rel, ln_of(text, m.start()), insecure=u if u.startswith("http://") else None))
    return out


def parse_all(proj):
    out = []
    for rel, text in proj.texts.items():
        if "node_modules" in rel or not is_manifest(rel):
            continue
        out += parse(rel, text)
    return out


# ---------------------------------------------------------------------------------------------------------------
# lighter / built-in / maintained replacements.  key: ecosystem -> {name: (replacement, reason, level)}
REPLACE = {
    "java": {
        "com.google.guava:guava": ("the JDK's own collections/streams (Java 11+)", "Guava is ~3 MB; many projects use two or three helpers from it", "low"),
        "joda-time:joda-time": ("java.time (built into Java 8+)", "built in, and Joda itself recommends it", "medium"),
        "log4j:log4j": ("SLF4J + Logback or Log4j 2", "Log4j 1.x is end-of-life and has known security holes", "high"),
        "commons-logging:commons-logging": ("SLF4J", "older logging layer", "low"),
        "org.apache.httpcomponents:httpclient": ("java.net.http.HttpClient (Java 11+)", "built in, no extra dependency", "low"),
        "junit:junit": ("JUnit 5 (org.junit.jupiter)", "JUnit 4 is in maintenance only", "low"),
        "commons-lang:commons-lang": ("commons-lang3 or the JDK", "the old line is unmaintained", "medium"),
    },
    "go": {
        "github.com/sirupsen/logrus": ("log/slog (Go 1.21+)", "built into the standard library; logrus is in maintenance mode", "low"),
        "github.com/pkg/errors": ("the standard `errors` package with %w wrapping", "archived; stdlib covers it", "medium"),
        "golang.org/x/net/context": ("the standard `context` package", "merged into the standard library years ago", "medium"),
        "github.com/dgrijalva/jwt-go": ("github.com/golang-jwt/jwt", "unmaintained, has a known security issue", "high"),
        "github.com/gorilla/mux": ("net/http's own ServeMux (Go 1.22+ has patterns)", "the standard router now handles methods and path values", "low"),
    },
    "rust": {
        "lazy_static": ("std::sync::LazyLock / OnceLock (Rust 1.80+)", "built into the standard library", "medium"),
        "failure": ("thiserror or anyhow", "unmaintained", "medium"),
        "structopt": ("clap (derive feature)", "merged into clap 3+", "medium"),
        "time": ("check you need it: std::time covers durations", "only if you do not format dates", "low"),
    },
    "php": {
        "swiftmailer/swiftmailer": ("symfony/mailer", "Swift Mailer is abandoned", "medium"),
        "fzaninotto/faker": ("fakerphp/faker", "abandoned fork source", "low"),
        "phpmailer/phpmailer": ("keep - but check it is >= 6.5", "older versions have security issues", "low"),
    },
    "ruby": {
        "therubyracer": ("mini_racer or Node", "unmaintained and heavy to build", "medium"),
        "rest-client": ("Net::HTTP or Faraday", "unmaintained", "low"),
        "json": ("nothing - it is part of Ruby", "bundled with Ruby", "low"),
    },
    "dotnet": {
        "Newtonsoft.Json": ("System.Text.Json (built into .NET)", "built in, faster, smaller; keep Newtonsoft only for features it alone has", "medium"),
        "Microsoft.AspNetCore.All": ("Microsoft.AspNetCore.App", "the 'All' metapackage pulls in far more than most apps use", "medium"),
        "EntityFramework": ("Entity Framework Core", "EF6 is legacy", "low"),
        "System.Net.Http": ("nothing - built into modern .NET", "the package is only needed for very old frameworks", "low"),
    },
    "dart": {
        "dio": ("the lighter `http` package, if you only make simple requests", "dio brings interceptors/cancel tokens you may not use", "low"),
    },
}


def replacement_findings(deps, uses_fn):
    """Yield (dep, replacement, reason, level, users) for known replaceable libraries."""
    for d in deps:
        table = REPLACE.get(d["eco"], {})
        key = d["name"]
        if key in table and d["scope"] != "indirect":
            alt, why, level = table[key]
            yield d, alt, why, level, uses_fn(d)


def unused_candidates(deps, proj, tokens_lower, blob):
    """Declared but never mentioned.  Returns [(dep, why_unsure)] - conservative: names that frameworks load by themselves are skipped."""
    out = []
    for d in deps:
        if d["scope"] in ("source", "indirect") or d["name"].startswith("("):
            continue
        eco, name = d["eco"], d["name"]
        if eco == "java":
            a, g = d.get("artifact", ""), d.get("group", "")
            if re.search(r"starter|-bom$|plugin|processor|^slf4j|logback|jdbc|driver|runtime", a) or d["scope"] == "dev" and re.search(r"junit|mockito|assertj|hamcrest", a):
                continue
            gp = ".".join(g.split(".")[:3])
            if g.lower() in blob or gp.lower() in blob or a.lower().replace("-", ".") in blob or a.lower() in tokens_lower:
                continue
        elif eco == "php":
            parts = re.split(r"[/\-_]", name.lower())
            if any(p in blob for p in parts if len(p) > 3) or name.split("/")[0].lower() in ("phpunit", "squizlabs", "friendsofphp", "phpstan", "psalm"):
                continue
        elif eco == "ruby":
            if name.lower() in ("rails", "rake", "puma", "bundler", "bootsnap", "sprockets", "webpacker", "tzinfo-data", "listen", "spring", "web-console", "debug", "byebug", "pg", "mysql2", "sqlite3", "sass-rails", "jbuilder") or d["scope"] == "dev":
                continue
            if name.lower().replace("-", "_") in tokens_lower or ("'" + name.lower()) in blob or name.lower() in blob:
                continue
        elif eco == "dotnet":
            first = name.split(".")[0].lower()
            if name.lower().startswith(("microsoft.net.", "microsoft.aspnetcore", "microsoft.extensions", "microsoft.entityframeworkcore", "xunit", "nunit", "coverlet", "microsoft.nettest")):
                continue
            if ("using " + name.lower()) in blob or ("using " + first) in blob or name.lower() in blob:
                continue
        elif eco == "dart":
            if ("package:" + name.lower() + "/") in blob or d["scope"] == "dev":
                continue
        elif eco == "python":
            mod = re.sub(r"[-_.]+", "_", name.lower())
            if d["scope"] == "dev" or mod in tokens_lower or name.lower() in tokens_lower:
                continue
        elif eco == "swift":
            if name.lower() in blob:
                continue
        else:
            continue
        out.append(d)
    return out


# ---------------------------------------------------------------------------------------------------------------
def pin_problems(d):
    """Return (severity, kind, what, why, todo) or None for one dependency."""
    eco, v, name = d["eco"], d["ver"], d["name"]
    if d.get("insecure"):
        return ("warning", "insecure-dep", "%s is fetched over an unencrypted address: %s" % (d["name"] if not d["name"].startswith("(") else "a package source", d["insecure"][:70]),
                "packages can be swapped in transit - what you build is no longer what the author published", "use https://")
    if d["scope"] == "source":
        return None
    unpinned = False
    if eco == "java":
        # a missing version is normal (managed by a parent / bill of materials); a "latest" or open range is not
        unpinned = v in ("+", "LATEST", "RELEASE", "latest.release", "latest.integration") or v.endswith("+") or bool(re.match(r"^[\[(].*,\s*[\])]$", v))
    elif eco == "rust":
        unpinned = v in ("*", "") and not d.get("path") and not d.get("git") or bool(d.get("git"))
        if d.get("git"):
            return ("warning", "unpinned-dep", "crate `%s` comes straight from a Git repository" % name, "a Git branch can change under you", "pin a `rev = \"...\"` commit or use a released version")
    elif eco == "php":
        unpinned = v in ("*", "dev-master", "dev-main", "@dev") or v.startswith("dev-")
    elif eco == "ruby":
        if d.get("git") and not d.get("ref"):
            return ("warning", "unpinned-dep", "gem `%s` comes straight from a Git repository with no ref" % name, "whatever is on the default branch gets installed", "add `ref:` (a commit) or `tag:`")
        return ("info", "unpinned-dep", "gem `%s` has no version" % name, "each `bundle update` may pull a different release (Gemfile.lock holds it until then)", "add a `~>` version") if (not v and d["scope"] == "runtime") else None
    elif eco == "dotnet":
        unpinned = bool(re.search(r"\*|\[.*,\s*\)", v))
    elif eco == "dart":
        unpinned = v in ("any", "")
    elif eco == "python":
        unpinned = v in ("*", "")
    elif eco == "swift":
        unpinned = v == "branch"
    elif eco == "go":
        return None
    if unpinned:
        return ("warning", "unpinned-dep", "`%s` has no fixed version (%s)" % (name, v or "none"), "every install can pull a different (maybe broken or hacked) release", "pin an exact or ranged version and commit the lock file")
    return None


def main():
    root = Path(sys.argv[1] if len(sys.argv) > 1 else HERE.parent).resolve()
    sys.path.insert(0, str(HERE))
    import insights
    proj = insights.Project(root)
    ds = parse_all(proj)
    if not ds:
        print("No dependency files of the supported kinds found.")
        return 0
    by = {}
    for d in ds:
        by.setdefault((d["eco"], d["file"]), []).append(d)
    for (eco, f), items in sorted(by.items()):
        print("%-7s %-40s %d dependenc%s" % (eco, f, len(items), "y" if len(items) == 1 else "ies"))
        for d in items[:8]:
            print("         %-46s %-14s %s" % (d["name"][:46], d["ver"][:14], d["scope"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
