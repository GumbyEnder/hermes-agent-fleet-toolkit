#!/bin/bash
# Holder-field harness for the v2 split-pair witness's `# wal ...` line and the
# additive `# holder-class` line.
#
# Why: at the 2026-11-13T16:15:21 slot the sidecar was absent, and
#   $(fuser "$DBP-wal" 2>&1 || true)
# put fuser's own error sentence ("Specified filename ... does not exist.") where the
# holder list belongs. The shipped gate survived only because it keys on `^# wal [0-9]+`;
# any reader grepping the holders field for ": <pid>" tokens would have parsed a sentence
# as a holder list, and any reader treating an empty holders field as "no holder measured"
# would have been wrong about the CAUSE. This harness pins the fixed contract:
#   absent sidecar      -> `# wal absent holders: n/a (...)`   and NO fuser error text
#   present, no holder  -> `# wal <size> <ts> holders: none`
#   present, one holder -> `# wal <size> <ts> holders: <path>: <pid>`
#   holder present      -> exactly one `# holder-class <pid>:<comm>@<unit>` per holder,
#                          unit = deepest .service/.scope in /proc/<pid>/cgroup
#   no .service/.scope  -> `@gone` (and _never_ the first-component `user@1000.service`)
# plus a regression test: the real 16:15 capture (which still carries the sentence) must
# keep grading VACUOUS (STRUCTURAL) through the live gate.
#
# It does not copy the wal code: it extracts the block from the shipped script by marker.
# The class walk is extracted as the awk program itself and run against fixture cgroups,
# because a dead pid cannot be a live holder -- `@gone` is unreachable through fuser.
#
# VERSIONED IN-TREE on purpose (2026-11-13): this file pins a shipped script, so it lives
# beside it under MANIFEST.md5. It sat at /mnt/nas/host-ops/agent-chat/ (no commit) while
# the code it pins was committed, so the delivery sentence "lands with the test updated in
# the same commit" was false. /mnt/nas/host-ops/agent-chat/test-witness-wal-line.sh is an
# installed copy of this file, not a second source.
#
# Usage: test-witness-wal-line.sh [path-to-witness-script] [path-to-real-1615-log]
set -u
WIT="${1:-/mnt/nas/host-ops/agent-chat/witness-split-pair-v2.sh}"
OLD="${2:-$HOME/agents/agent-chat/pairsplit2-20261113T161521.log}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
FAILS=0

awk '/^  # --- wal line:/{f=1} /^  echo "# keeper/{f=0} f' "$WIT" > "$TMP/wal.sh"
if ! grep -q 'fuser' "$TMP/wal.sh"; then
  echo "EXTRACT FAILED -- wal-line marker missing in $WIT"; exit 1
fi
echo "# wal block: $(wc -l < "$TMP/wal.sh") lines extracted from $WIT"

# The unit walk, as shipped (the awk program only, so fixtures can drive it).
UNIT_PROG="$(sed -n "s/.*_unit=\"\$(awk -F\/ '\(.*\)' .*/\1/p" "$WIT" | head -1)"
if [ -z "$UNIT_PROG" ]; then
  echo "EXTRACT FAILED -- unit-walk awk missing in $WIT"; exit 1
fi
UNIT_AWK="awk -F/ '$UNIT_PROG'"

ok() { printf 'PASS  %s\n' "$1"; }
bad() { printf 'FAIL  %s\n      %s\n' "$1" "$2"; FAILS=$((FAILS+1)); }

# --- A: absent sidecar: no fuser error text in the holders field
DBP="$TMP/nothing.db" bash "$TMP/wal.sh" > "$TMP/a.out" 2>&1
if grep -q '^# wal absent holders: n/a' "$TMP/a.out" && ! grep -qiE 'specified filename|no such file|does not exist' "$TMP/a.out"; then
  ok "A absent: explicit n/a, no fuser sentence  -> $(cat "$TMP/a.out")"
else
  bad "A absent sidecar" "$(cat "$TMP/a.out")"
fi

# --- B: present, zero holders
: > "$TMP/b.db-wal"
DBP="$TMP/b.db" bash "$TMP/wal.sh" > "$TMP/b.out" 2>&1
if grep -qE '^# wal [0-9]+ [0-9]{4}-[0-9]{2}-[0-9]{2} .* holders: none$' "$TMP/b.out"; then
  ok "B present/no holder: holders: none  -> $(cat "$TMP/b.out")"
else
  bad "B present, no holder" "$(cat "$TMP/b.out")"
fi

# --- C: present with a real holder (an fd held open by another process)
: > "$TMP/c.db-wal"
tail -f "$TMP/c.db-wal" >/dev/null 2>&1 &
HPID=$!
sleep 0.5
DBP="$TMP/c.db" bash "$TMP/wal.sh" > "$TMP/c.out" 2>&1
if grep -qE "^# wal [0-9]+ .* holders: .*/c\.db-wal: +$HPID( |$)" "$TMP/c.out"; then
  ok "C present/one holder: pid token present  -> $(grep '^# wal' "$TMP/c.out")"
else
  bad "C present, held by pid $HPID" "$(cat "$TMP/c.out")"
fi

# --- E: the class line is pinned, not just observed. One holder -> exactly one
#        `# holder-class <pid>:<comm>@<unit>`, pid = the holders pid, unit = the deepest
#        .service/.scope in the LIVE /proc cgroup, compared to an independent read.
WANT_COMM="$(cat "/proc/$HPID/comm" 2>/dev/null || echo '?')"
WANT_UNIT="$(printf '%s' "$(cat "/proc/$HPID/cgroup" 2>/dev/null)" | tr '/' '\n' | grep -E '[.](service|scope)$' | tail -1)"
WANT_UNIT="${WANT_UNIT:-gone}"
NCLS="$(grep -c '^# holder-class' "$TMP/c.out")"
GOTCLS="$(grep '^# holder-class' "$TMP/c.out" | sed 's/^# holder-class *//')"
if [ "$NCLS" = 1 ] && printf '%s' "$GOTCLS" | grep -qE "^${HPID}:${WANT_COMM}@${WANT_UNIT}\$"; then
  ok "E class line: one per holder, pid:comm@unit  -> $GOTCLS"
else
  bad "E class line" "count=$NCLS got='$GOTCLS' want='${HPID}:${WANT_COMM}@${WANT_UNIT}'"
fi
kill "$HPID" 2>/dev/null; wait "$HPID" 2>/dev/null

# --- F: the unit walk itself, on fixture cgroups. Deepest wins; .scope counts;
#        a cgroup with neither resolves to `gone` -- never `user@1000.service`.
F_WANT_svc="foosvc.service"
F_WANT_scope="fooprobe.scope"
F_WANT_app="app-org.kde.konsole-4242.scope"
F_WANT_session="user@1000.service"
F_WANT_none="gone"
f_case() { # name fixture want
  local got; got="$(printf '%s\n' "$2" | eval "$UNIT_AWK" 2>/dev/null)"
  got="${got:-gone}"
  if [ "$got" = "$3" ]; then ok "F $1 -> $got"; else bad "F $1" "got '$got' want '$3' (fixture $2)"; fi
}
f_case "service, deepest"  "/user.slice/user-1000.slice/user@1000.service/app.slice/foosvc.service" "$F_WANT_svc"
f_case "scope, deepest"    "/user.slice/user-1000.slice/user@1000.service/app.slice/fooprobe.scope" "$F_WANT_scope"
f_case "app scope"         "/user.slice/user-1000.slice/user@1000.service/app.slice/app-org.kde.konsole-4242.scope" "$F_WANT_app"
# A process in the session slice has no unit of its own: the deepest match IS the manager
# unit. Measured, not a mis-walk -- pin it so nobody "fixes" it back to a first-component
# read, and so a reader knows user@1000.service means "no unit of its own", not "a unit".
f_case "session slice (no own unit)" "/user.slice/user-1000.slice/user@1000.service/app.slice" "$F_WANT_session"
f_case "root only"         "/" "$F_WANT_none"
f_case "empty cgroup (dead)" "" "$F_WANT_none"

# --- D: the old sentence-carrying capture still grades VACUOUS (STRUCTURAL) through the LIVE gate.
awk '/^# --- presence first/{f=1} f' "$WIT" > "$TMP/gate.sh"
if [ -f "$OLD" ]; then
  out="$(OUT="$OLD" bash "$TMP/gate.sh" 2>&1)"; rc=$?
  if [ "$rc" = 0 ] && printf '%s' "$out" | grep -qF 'VACUOUS (STRUCTURAL)'; then
    ok "D legacy 16:15 capture still VACUOUS (STRUCTURAL) ($(basename "$OLD"))"
  else
    bad "D legacy capture graded rc=$rc" "$out"
  fi
else
  echo "SKIP  D legacy capture not found ($OLD)"
fi

echo
if [ "$FAILS" -eq 0 ]; then echo "ALL WAL-LINE + HOLDER-CLASS SHAPES PASS"; else echo "$FAILS FAILURE(S)"; fi
exit "$FAILS"
