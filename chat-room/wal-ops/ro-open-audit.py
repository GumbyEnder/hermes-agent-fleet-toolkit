#!/usr/bin/env python3
"""ro-open-audit.py -- find every script that can open a SQLite database, and say
whether the open is read-only, because a read-only open is the one that fabricates
the state the WAL instruments report.

WHY (measured 2026-11-14 01:30:07 ET): a `mode=ro` open of a WAL-mode db whose
-wal/-shm pair is ABSENT manufactures `wal 0 B / shm 32,768 B` and leaves it there
after close. It writes no log line anywhere, so it is invisible to every instrument
that watches the sidecars. At 01:30:07 one of my own sweep loops opened seven local
copies AND /mnt/nas/agent-chat/room.db.bak read-only, which is where the 0 B / 32,768 B
pair beside the NAS backup came from. The audit is therefore not academic: the
reader was the artifact.

WHAT THIS DOES: text scan only. It never opens a database and never stats a sidecar.
Classifies each script by
  RO   -- an explicit read-only marker (mode=ro, -readonly, immutable=1)
  RW   -- connects but no read-only marker (query_only=1 counts as RW+guarded)
  PATH -- names a .db path but opens no connection (stat/hash/copy only)
and flags ROW+LIVE when the script's db target resolves to the production room db
or to a backup of it, which is the class that fakes a holder.

USAGE: ro-open-audit.py [--scratch] [--md OUT.md]
  --scratch  also scan profile cache/scratch trees (throwaway probes)
  --md       write a markdown table (default: stdout only)

Exit 2 if any script carries an unguarded read-only open against a live/backup path.
"""
import argparse
import ast
import os
import re
import sys
import time
import warnings

ROOTS = [
    os.path.expanduser("~/agents"),
    os.path.expanduser("~/Agents/Projects/hermes-agent-fleet-toolkit"),
    "/mnt/nas/host-ops/agent-chat",
    "/mnt/nas/agent-chat",
    os.path.expanduser("~/.local/bin"),
]
SCRATCH_GLOBS = [os.path.expanduser("~/.hermes/profiles/*/cache/scratch")]
SKIP_PARTS = {"node_modules", ".git", "__pycache__", "spillover", "site-packages"}

CONNECT = [
    ("py-sqlite3", re.compile(r"sqlite3\.connect\s*\(")),
    ("py-sqlite3-uri", re.compile(r"connect\s*\(\s*['\"]file:")),
    ("cli", re.compile(r"(?<![\w./-])sqlite3\s+(?:-\w+\s+)*['\"$]")),
    ("node-better", re.compile(r"new\s+Database\s*\(")),
    ("node-sqlite", re.compile(r"DatabaseSync\s*\(|open\s*\(\s*['\"][^'\"]*\.db")),
]
RO_MARK = re.compile(r"mode=ro\b|-readonly\b|immutable=1\b|mode=ro&")
GUARD = re.compile(r"query_only\s*=\s*1|PRAGMA\s+query_only", re.I)
DB_PATH = re.compile(r"[\w./${}~+-]*\.db(?:-wal|-shm|\.bak)?\b")
LIVE = re.compile(r"(agents/agent-chat/room\.db|/mnt/nas/agent-chat/room\.db)")
TEXT_EXT = {".py", ".sh", ".bash", ".mjs", ".js", ".cjs", ".pl", ".rb"}
MAX_BYTES = 2_000_000


def wanted(path):
    if any(p in path.split(os.sep) for p in SKIP_PARTS):
        return False
    _, ext = os.path.splitext(path)
    if ext in TEXT_EXT:
        return True
    try:
        with open(path, "rb") as fh:
            head = fh.readline(200)
        return head.startswith(b"#!") and (b"bash" in head or b"python" in head or b"sh" in head)
    except OSError:
        return False


def docstring_lines(txt):
    """1-based line numbers taken up by module/class/function docstrings.

    A marker in PROSE is not behaviour, and prose is not always a # comment: qread.py
    documents the mode=ro trap in its module docstring and graded RO under the first
    version of this audit (frodo, 2026-11-14 06:19, item 3). ast tells a docstring from
    the ordinary string that carries a real marker -- `connect("file:%s?mode=ro")` --
    which is why only docstring spans are dropped, never all string literals: stripping
    strings wholesale would delete every genuine read-only open this audit exists to find.
    """
    try:
        with warnings.catch_warnings():
            # a SyntaxWarning from one unrelated .py in the corpus must not land on the
            # audit's own stdout/stderr: it is noise about a file, not a grading result.
            warnings.simplefilter("ignore")
            tree = ast.parse(txt)
    except (SyntaxError, ValueError):
        return set()
    spans = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", [])
        if not body:
            continue
        first = body[0]
        if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            start = first.lineno
            end = getattr(first, "end_lineno", start) or start
            spans.update(range(start, end + 1))
    return spans


def scan(path):
    try:
        if os.path.getsize(path) > MAX_BYTES:
            return None
        txt = open(path, "r", errors="replace").read()
    except OSError:
        return None
    # Markers inside comments are documentation, not behaviour: the comment block in
    # wal-checkpoint.sh explains the mode=ro trap and must not be graded as an ro open.
    # Docstrings are prose too (qread.py) -- drop those lines as well, by ast span.
    doc = docstring_lines(txt) if path.endswith(".py") else set()
    code_lines = [ln for i, ln in enumerate(txt.splitlines(), 1)
                  if i not in doc and not ln.lstrip().startswith("#")]
    code = "\n".join(code_lines)
    kinds = [name for name, rx in CONNECT if rx.search(code)]
    if not kinds:
        return None
    ro = bool(RO_MARK.search(code))
    guarded = bool(GUARD.search(code))
    targets = sorted(set(DB_PATH.findall(txt)))[:6]
    # HIGH: the read-only marker is on a line that names the live db, either literally or
    # through a variable assigned the live path one hop earlier. This catches
    # `P = expanduser("~/agents/.../room.db")` ... `connect("file:%s?mode=ro" % P)`.
    live_vars = set()
    for ln in code_lines:
        m = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", ln)
        if m:
            rhs = ln.split("=", 1)[1]
            if LIVE.search(rhs):
                live_vars.add(m.group(1))
    def is_live(ln):
        if LIVE.search(ln):
            return True
        return any(re.search(r"\b%s\b" % re.escape(v), ln) for v in live_vars)
    live = any(RO_MARK.search(ln) and is_live(ln) for ln in code_lines)
    if ro:
        cls = "RO"
    elif guarded:
        cls = "RW+guarded"
    else:
        cls = "RW"
    risk = "HIGH" if (ro and live) else ("MED" if ro else ("LOW" if live else ""))
    return {
        "path": path,
        "bytes": os.path.getsize(path),
        "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(path))),
        "kinds": ",".join(kinds),
        "class": cls,
        "targets": " ".join(targets) or "(variable)",
        "risk": risk,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scratch", action="store_true")
    ap.add_argument("--md")
    a = ap.parse_args()

    roots = list(ROOTS)
    if a.scratch:
        import glob
        roots += [p for g in SCRATCH_GLOBS for p in glob.glob(g)]

    rows, scanned = [], 0
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_PARTS]
            for fn in filenames:
                p = os.path.join(dirpath, fn)
                if not wanted(p):
                    continue
                scanned += 1
                r = scan(p)
                if r:
                    rows.append(r)

    order = {"HIGH": 0, "MED": 1, "LOW": 2, "": 3}
    rows.sort(key=lambda r: (order[r["risk"]], r["class"] != "RO", r["path"]))

    ro = [r for r in rows if r["class"] == "RO"]
    high = [r for r in rows if r["risk"] == "HIGH"]
    hdr = f"scanned {scanned} script-like files under {len(roots)} roots; {len(rows)} open a database; {len(ro)} read-only; {len(high)} READ-ONLY + live/backup path"

    lines = ["# ro-open audit", "", f"generated {time.strftime('%Y-%m-%d %H:%M:%S %z')}", "", hdr, "",
             "Generated by /mnt/nas/host-ops/agent-chat/ro-open-audit.py (text scan only; opens no database).", "",
             "| risk | class | script | bytes | mtime | api | db targets |",
             "|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append("| {risk} | {class} | `{path}` | {bytes} | {mtime} | {kinds} | `{targets}` |".format(**r))

    print(hdr)
    print()
    for r in rows:
        print(f"{r['risk'] or '   ':4} {r['class']:11} {r['path']}  [{r['kinds']}]  {r['targets']}")
    if a.md:
        open(a.md, "w").write("\n".join(lines) + "\n")
        print(f"\nmarkdown: {a.md}")
    return 2 if high else 0


if __name__ == "__main__":
    sys.exit(main())
