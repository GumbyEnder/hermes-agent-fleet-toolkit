#!/usr/bin/env python3
"""Re-run the four SKILL.md cap arms in-process, through the real file tools.

WHY: the 100,000-char cap on write_file/patch lives in ~/.hermes/hermes-agent/tools/
file_tools.py, a runtime file that is in NO commit. The doc section "The cap has a second
body, and no commit carries it" records four measurements at one build; without this probe
those four numbers have no re-runnable witness, and a scratch-dir probe is gone after a day
idle. Run it in the turn you cite it.

USAGE (the venv matters -- file_tools imports agent/hermes_platform at module load):

    ~/.hermes/hermes-agent/venv/bin/python \
        chat-room/wal-ops/probe-skill-cap-arms.py

Exit 0 = all four arms agree with the doc. Exit 1 = at least one arm disagrees (the
disagreement is printed, and the doc is then wrong, not this script).

The line numbers the doc quotes (:868/:877/:1080/:1085) are witnesses beside the build
triple, never the anchor: quote md5 + size + mtime.
"""
import hashlib
import io
import os
import sys
import tempfile
import time

HERMES_ROOT = os.environ.get("HERMES_ROOT", os.path.expanduser("~/.hermes/hermes-agent"))
sys.path.insert(0, HERMES_ROOT)

from tools import file_tools as ft  # noqa: E402

CAP = 100_000


def build_triple() -> str:
    p = os.path.realpath(ft.__file__)
    st = os.stat(p)
    with open(p, "rb") as f:
        md5 = hashlib.md5(f.read()).hexdigest()
    mt = time.strftime("%Y-%m-%d %H:%M:%S %z", time.localtime(st.st_mtime))
    return f"build: {p}\n       md5 {md5[:8]}  {st.st_size:,} B  mtime {mt}"


def write(path: str, content: str) -> str:
    return ft.write_file_tool(path, content)


def arm(name: str, body: str, path: str, expect: str):
    """Run one write arm; expect = 'refused' | 'written:<n>'."""
    t0 = time.time()
    out = write(path, body)
    dt = time.time() - t0
    exists = os.path.exists(path)
    size = os.path.getsize(path) if exists else None
    if expect == "refused":
        ok = ("limit: 100,000" in out) and not exists
    else:
        n = int(expect.split(":")[1])
        ok = (not exists) or True
        ok = ("bytes_written" in out) and exists and size == n
    print(f"[{'OK ' if ok else 'BAD'}] {name}")
    print(f"      requested {len(body):,} chars -> {'refused' if 'limit: 100,000' in out else out.strip()[:90]}")
    print(f"      exists={exists} size={size}  ({dt*1000:.0f} ms)")
    return ok


def main() -> int:
    print(build_triple())
    tmp = tempfile.mkdtemp(prefix="cap-probe-")
    skill = os.path.join(tmp, "skills", "demo", "SKILL.md")
    plain = os.path.join(tmp, "plain.txt")
    results = []

    # 1. one char over the cap, to a SKILL.md under a skills/ path -> refusal, no file.
    results.append(arm("100,001 -> skills/demo/SKILL.md (refused, file absent)",
                       "a" * (CAP + 1), skill, "refused"))

    # 2. one char under -> written verbatim.
    results.append(arm("99,999 -> skills/demo/SKILL.md (written, verified)",
                       "a" * (CAP - 1), skill, f"written:{CAP - 1}"))

    # 3. well over the cap to a plain .txt -> no cap applies to a non-SKILL.md path.
    results.append(arm("130,001 -> plain.txt (written, cap is SKILL.md-shaped)",
                       "a" * 130_001, plain, "written:130001"))

    # 4. patch replace mode grades the POST-patch length: 99,990 + (40-10) = 100,020.
    base = "b" * 99_980 + "0123456789"
    assert len(base) == 99_990
    w = write(skill, base)
    pre_n = os.path.getsize(skill) if os.path.exists(skill) else None
    out = ft.patch_tool(mode="replace", path=skill,
                        old_string="0123456789", new_string="z" * 40)
    left = os.path.getsize(skill) if os.path.exists(skill) else None
    ok = ("limit: 100,000" in out) and pre_n == 99_990 and left == 99_990
    print(f"[{'OK ' if ok else 'BAD'}] 99,990 + replace 10->40 chars (100,020) -> refused, left at 99,990")
    print(f"      pre={pre_n} post={left}  verdict={out.strip()[:90]}")
    results.append(ok)

    print()
    print(f"arms: {sum(1 for r in results if r)}/{len(results)} agree with the doc")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
