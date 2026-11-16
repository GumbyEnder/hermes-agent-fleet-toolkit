#!/usr/bin/env python3
"""test-wake-envelope-and-room-witness.py -- two defects, one envelope, measured.

1. ENVELOPE. The wake prompt carried `from` and `topic` and no message_id, so a turn
   could not name the message it answered. _already_answered on re-entry looks for a
   reply whose parent_id IS the mention, so an unparented answer reads as unanswered
   and the turn is re-spilled (measured 2026-11-15: 5463 and 5495, both rc=-9, both
   already answered in the room). The prompt now carries message_id/parent_id/wake_ts
   and tells the agent to post with reply_to=<message_id>.

2. ROOM WITNESS. A hard-cap kill takes the client's stdout with it, so
   reply_chars=0/posted_id=0 is a fact about the pipe, not the room. _retry_after_turn
   asks the room before re-spilling; a reply parented to the mention means the turn
   did its job. rc=0 with empty stdout stays silence (no retry) as before.

No network: the bus and the subprocess are both stubbed. Run from this directory.
"""
import importlib.util
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="wake-env-")
os.environ["AGENT_CHAT_SECRET"] = "test-secret"
os.environ["WAKE_SPILL"] = os.path.join(TMP, "spill.jsonl")
os.environ["WAKE_LOCK_DIR"] = os.path.join(TMP, "locks")
os.environ["WAKE_ALLOW"] = "frodo"
os.environ["WAKE_SKIP_DIR"] = os.path.join(TMP, "skipped-stdout")
os.environ["WAKE_BIN"] = "/bin/true"

spec = importlib.util.spec_from_file_location("wd", os.path.join(HERE, "wake_dispatcher.py"))
wd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wd)

fails, checks = [], 0


def check(name, got, want):
    global checks
    checks += 1
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'} {name}: got {got!r} want {want!r}")
    if not ok:
        fails.append(name)


# ---- 1. the prompt carries the envelope -------------------------------------
captured = {}


class FakeProc:
    returncode = 0

    def __init__(self, argv, **kw):
        captured["argv"] = argv
        captured["prompt"] = argv[4]

    def communicate(self, timeout=None):
        return ("", "")

    def kill(self):
        pass

    pid = 4242


wd.subprocess.Popen = FakeProc
wd._post_reply = lambda *a, **k: 0
wd._stamp_holder = lambda *a, **k: None

payload = {"from": "buzzy", "topic": "work", "text": "@frodo check this",
           "message_id": 5463, "parent_id": 4303, "ts": 1794751280.6161935}
wd._run_one("frodo", payload, -1)
prompt = captured["prompt"]
print("--- prompt head ---")
print("\n".join(prompt.splitlines()[:3]))
print("---")
check("prompt names message_id", "message_id=5463" in prompt, True)
check("prompt names parent_id", "parent_id=4303" in prompt, True)
check("prompt carries wake_ts", "wake_ts=" in prompt, True)
# mention_ts is the room row's ts as the sender knew it, carried in the signed
# payload. 4-decimal equality against `SELECT ts FROM messages WHERE id=<mid>`
# on a live wake is the acceptance test; this unit leg only proves it is plumbed
# from the payload into the envelope and not recomputed from a local clock.
check("prompt carries mention_ts from the payload",
      "mention_ts=1794751280.616194" in prompt, True)
check("prompt carries wake_ts beside mention_ts",
      "mention_ts=1794751280.616194 wake_ts=" in prompt, True)
check("prompt tells the agent reply_to=<message_id>", "reply_to=5463" in prompt, True)
check("prompt keeps the original mention header",
      prompt.startswith("[Agent Chat Room — you were @mentioned by buzzy in topic 'work']"), True)
# The body is re-fetched from the bus when the payload carries a message_id
# (text_src=refetch), so the payload's own text is not what goes in the prompt.
# Assert the body leg on the no-id path, where the payload text is authoritative.
wd._run_one("frodo", {"from": "buzzy", "topic": "work", "text": "BODY-MARKER"}, -1)
env_line = captured["prompt"].splitlines()[1]
check("payload body reaches the prompt when there is no id to refetch",
      "BODY-MARKER" in captured["prompt"], True)
check("no-id wake says none, not 0",
      env_line.startswith("[envelope message_id=none parent_id=none mention_ts=none wake_ts="), True)

# ---- 2. the retry decision asks the room ------------------------------------
replies = {}


def stub_answered(profile, mid):
    return replies.get(mid, False)


wd._already_answered = stub_answered
killed = {"reply": "", "err": "killed at hard cap 1350s (budget 900s)", "rc": -9}
answered = {"reply": "", "err": "", "rc": 0}

replies[5463] = True
before = wd._answered_in_room
check("hard-cap kill + reply in room -> no retry", wd._retry_after_turn("frodo", 5463, killed), False)
check("  ... and it is counted, not silent", wd._answered_in_room, before + 1)
replies[5464] = False
check("hard-cap kill + nothing in room -> retry", wd._retry_after_turn("frodo", 5464, killed), True)
replies[5465] = True
check("rc=0 silence -> no retry (unchanged)", wd._retry_after_turn("frodo", 5465, answered), False)
check("captured reply -> no retry", wd._retry_after_turn("frodo", 5465,
                                                         {"reply": "hi", "err": "", "rc": 0}), False)
check("rc=-15 no reply, room empty -> retry",
      wd._retry_after_turn("frodo", 5464, {"reply": "", "err": "stopped", "rc": -15}), True)

# ---- 3. the wrapper does not post a second reply under the same mention -----
# Measured 2026-11-15: 5674 (posted by the turn at 15:26:57.616) and 5675 (posted
# by the wrapper at 15:27:18.061) are the same answer twice. The guard is the
# clock: a child of the mention, from this profile, created after the turn started.
posts = []


class TalkingProc(FakeProc):
    def communicate(self, timeout=None):
        return ("Posting as the turn, not the wrapper.", "")


wd.subprocess.Popen = TalkingProc
wd._post_reply = lambda *a, **k: (posts.append(a), 9001)[1]

wd._answered_since = lambda profile, mid, since: True
out = wd._run_one("frodo", {"from": "buzzy", "topic": "work", "text": "x",
                            "message_id": 5678, "parent_id": 5674, "ts": 1794790495.288}, -1)
check("turn already posted -> wrapper does not post", posts, [])
check("turn already posted -> posted_id stays 0", out.get("posted"), 0)
# Suppressing the POST must not suppress the CONTENT. The skip path files the body
# and names the path; a character count alone is not an audit trail.
skip_files = [f for f in os.listdir(wd.SKIP_DIR)] if os.path.isdir(wd.SKIP_DIR) else []
filed = [f for f in skip_files if f.startswith("5678-frodo-")]
check("suppressed body is filed, not dropped", len(filed), 1)
check("  ... with the full text",
      open(os.path.join(wd.SKIP_DIR, filed[0]), encoding="utf-8").read() if filed else "",
      "Posting as the turn, not the wrapper.")
check("prompt says stdout is not the record",
      "YOUR STDOUT IS NOT THE RECORD" in prompt, True)

wd._answered_since = lambda profile, mid, since: False
wd._post_reply = lambda *a, **k: (posts.append(a), 9001)[1]
out = wd._run_one("frodo", {"from": "buzzy", "topic": "work", "text": "y",
                            "message_id": 5679, "parent_id": 5674}, -1)
check("turn did not post -> wrapper posts exactly once", len(posts), 1)
check("  ... and reports the id it got", out.get("posted"), 9001)

print()
print(f"{checks - len(fails)}/{checks} checks passed")
print("RESULT " + ("PASS" if not fails else "FAIL " + ", ".join(fails)))
sys.exit(0 if not fails else 1)
