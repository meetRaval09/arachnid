"""Arachnid - Step 1: the crawler.

What it does, in plain words:
  1. Starts at a folder (the "root").
  2. Opens every folder inside it, one by one, and writes down every file it finds.
  3. Prints the result as a tree and saves a full report as a JSON file.

It only LOOKS. It never changes, moves or deletes anything.

Run it:
    python crawler.py                 (crawls the folder that contains this 'arachnid' folder)
    python crawler.py "C:\\some\\folder"  (crawls any other folder)
    python crawler.py --depth 5        (print the tree deeper; default is 3)
"""
import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

# Folders that are huge or not "your" code. The crawler skips them.
IGNORE_DIRS = {
    "node_modules", ".git", "dist", "build", "__pycache__", ".wrangler",
    ".vite", ".next", ".cache", ".claude", "arachnid", ".venv", "venv",
}

# Files with these endings are treated as text/code, so we can count their lines.
TEXT_EXT = {
    "html", "htm", "js", "mjs", "cjs", "ts", "tsx", "jsx", "json", "jsonc",
    "css", "scss", "md", "txt", "sql", "py", "yml", "yaml", "toml", "svg",
    "xml", "csv", "sh", "bat", "ini", "cfg",
}


def is_secret_file(name):
    """Files like .env hold passwords. We list them but never open them."""
    return name.lower().startswith(".env")


def looks_like_text(path):
    """A quick check: real text files have no zero-bytes in their first 2 KB."""
    try:
        with open(path, "rb") as f:
            return b"\x00" not in f.read(2048)
    except OSError:
        return False


def count_lines(path):
    try:
        with open(path, "rb") as f:
            data = f.read()
        return data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)
    except OSError:
        return None


def file_info(entry, root):
    path = Path(entry.path)
    name = entry.name
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    size = entry.stat().st_size
    info = {
        "name": name,
        "type": "file",
        "path": path.relative_to(root).as_posix(),
        "ext": ext,
        "size": size,
        "secret": is_secret_file(name),
        "lines": None,
    }
    if not info["secret"] and size < 2_000_000 and (ext in TEXT_EXT or looks_like_text(path)):
        info["lines"] = count_lines(path)
    return info


def scan(folder, root, stats, ignore):
    """Open one folder, record what is inside, and go into each sub-folder."""
    node = {"name": folder.name or str(folder), "type": "dir",
            "path": "" if folder == root else folder.relative_to(root).as_posix(),
            "children": []}
    try:
        entries = sorted(os.scandir(folder),
                         key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower()))
    except OSError as err:
        node["error"] = str(err)
        stats["errors"].append(node["path"] or ".")
        return node
    for e in entries:
        if e.is_symlink():
            stats["symlinks"] += 1
            continue
        if e.is_dir(follow_symlinks=False):
            if e.name in ignore:
                stats["ignored"].append(Path(e.path).relative_to(root).as_posix())
                continue
            node["children"].append(scan(Path(e.path), root, stats, ignore))
        elif e.is_file(follow_symlinks=False):
            node["children"].append(file_info(e, root))
    return node


def walk_files(node):
    for c in node["children"]:
        if c["type"] == "file":
            yield c
        else:
            yield from walk_files(c)


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def tree_chars():
    """Use pretty lines if the terminal can show them, plain ASCII if not (older Windows)."""
    try:
        "├└│─".encode(sys.stdout.encoding or "utf-8")
        return "├── ", "└── ", "│   ", "    "
    except (UnicodeEncodeError, LookupError):
        return "|-- ", "`-- ", "|   ", "    "


def print_tree(node, max_depth, chars, prefix="", depth=0):
    mid, end, bar, gap = chars
    kids = node["children"]
    for i, c in enumerate(kids):
        last = i == len(kids) - 1
        branch = end if last else mid
        if c["type"] == "dir":
            n_files = sum(1 for _ in walk_files(c))
            print(f"{prefix}{branch}{c['name']}/  ({n_files} files)")
            if depth + 1 < max_depth:
                print_tree(c, max_depth, chars, prefix + (gap if last else bar), depth + 1)
        else:
            extra = f"{human(c['size'])}" + (f", {c['lines']} lines" if c["lines"] is not None else "")
            tag = "  [secret file - not opened]" if c["secret"] else ""
            print(f"{prefix}{branch}{c['name']}  ({extra}){tag}")


def main():
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description="Arachnid crawler - Step 1")
    ap.add_argument("root", nargs="?", default=str(here.parent), help="folder to crawl")
    ap.add_argument("--depth", type=int, default=3, help="how deep to print the tree")
    ap.add_argument("--out", default=str(here / "reports" / "crawl-report.json"))
    args = ap.parse_args()

    root = Path(args.root).resolve()
    if not root.is_dir():
        sys.exit(f"Not a folder: {root}")

    stats = {"errors": [], "ignored": [], "symlinks": 0}
    tree = scan(root, root, stats, IGNORE_DIRS)
    files = list(walk_files(tree))

    by_ext = Counter()
    ext_size = Counter()
    ext_lines = Counter()
    for f in files:
        key = f["ext"] or "(none)"
        by_ext[key] += 1
        ext_size[key] += f["size"]
        ext_lines[key] += f["lines"] or 0

    n_dirs = sum(1 for _ in _dirs(tree))
    total_size = sum(f["size"] for f in files)
    total_lines = sum(f["lines"] or 0 for f in files)
    largest = sorted(files, key=lambda f: -f["size"])[:10]

    print(f"\nCrawled: {root}\n")
    print(f"{root.name}/")
    print_tree(tree, args.depth, tree_chars())
    print("\n---------------- Summary ----------------")
    print(f"Folders: {n_dirs}    Files: {len(files)}    Size: {human(total_size)}    Lines of text/code: {total_lines:,}")
    print("\nBy file type:")
    for ext, n in by_ext.most_common(10):
        print(f"  .{ext:<8} {n:>4} files  {human(ext_size[ext]):>9}  {ext_lines[ext]:>7,} lines")
    print("\nLargest files:")
    for f in largest:
        print(f"  {human(f['size']):>9}  {f['path']}")
    if stats["ignored"]:
        print(f"\nSkipped on purpose ({len(stats['ignored'])} folders): " + ", ".join(sorted(set(Path(p).name for p in stats['ignored']))))
    if stats["errors"]:
        print("\nCould not open:", ", ".join(stats["errors"]))

    report = {
        "crawled_at": datetime.now().isoformat(timespec="seconds"),
        "root": str(root),
        "totals": {"folders": n_dirs, "files": len(files), "bytes": total_size, "lines": total_lines},
        "by_extension": {e: {"files": by_ext[e], "bytes": ext_size[e], "lines": ext_lines[e]} for e in by_ext},
        "ignored_folders": stats["ignored"],
        "errors": stats["errors"],
        "tree": tree,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nFull report saved to: {out}")


def _dirs(node):
    for c in node["children"]:
        if c["type"] == "dir":
            yield c
            yield from _dirs(c)


if __name__ == "__main__":
    main()
