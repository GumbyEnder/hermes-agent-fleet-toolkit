#!/usr/bin/env bash
# enumerate-exec-set.sh -- enumerate the published set from the objects that RUN.
#
# Why this exists: every coverage token in this directory iterates LEG_TABLE, and
# LEG_TABLE is hand-written. A hand-listed table cannot enumerate a set nobody
# listed. verify-slot-manifest.sh sat inside the repo and outside the table while
# its NAS copy was hand-maintained; grade-slot.service execs the grader hourly with
# no repo copy and no roster row. Same shape, and neither is visible from the table.
#
# So walk the executors instead:
#   1. grep Exec(Start|StartPre|Stop|Reload) out of this repo's systemd/
#   2. systemctl show -p ExecStart over every unit installed in the two roots that
#      run here (~/.config/systemd/user and /etc/systemd/system). The loaded
#      directive, never the file digest: md5 on a unit file goes red on a trailing
#      newline systemd's parser discards and prints the same red for a real
#      ExecStart change.
#   3. pull every script path out of those directives, including the ones buried
#      inside `bash -c '... exec /path/x.sh'`, and keep the ones in this room's
#      universe: basename tracked in this repo, a LEG_TABLE row, or a path under a
#      root the room writes to ($NAS, ~/agents/agent-chat). Host units that exec
#      none of those are somebody else's business and are skipped, not counted.
#   4. classify each path; nothing may be silently absent:
#        LEG           a row of LEG_TABLE (writer + a measured leg)
#        INPLACE-REPO  the path IS a tracked file in this repo, so there is no copy
#                      to witness -- the unit executes the object git records
#        NONLEG        written down below with a reason
#        UNWITNESSED   runs, is not a leg, is not the tracked object, nobody wrote
#                      down why -- this is the finding, and it fails the run
#   5. assert the unit set in BOTH directions: repo units with no install, and
#      in-universe installed units with no repo copy (the reverse hole)
#
# rc: 0 clean | 1 unwitnessed path(s) | 4 a root could not be read (skip, not pass)

set -u

WALOPS=$(cd -- "$(dirname -- "$0")" && pwd)
REPO=$(git -C "$WALOPS" rev-parse --show-toplevel 2>/dev/null) || REPO=""
[ -n "$REPO" ] || { echo "FAIL not inside a git work tree: $WALOPS" >&2; exit 4; }
REPO_SYSTEMD=$WALOPS/systemd
USER_ROOT=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user
ROOT_ROOT=/etc/systemd/system
NAS=${NAS:-/mnt/nas/host-ops/agent-chat}
BUSDIR=${BUSDIR:-$HOME/agents/agent-chat}

rc=0

# --- the table, read and not sourced (sourcing would run the installer) --------
leg_table=$(sed -n '/^LEG_TABLE="/,/"$/p' "$WALOPS/install-mirror-legs.sh" 2>/dev/null \
            | sed '1d;$d' | sed '/^[[:space:]]*$/d')
[ -n "$leg_table" ] || { echo "FAIL cannot read LEG_TABLE from install-mirror-legs.sh" >&2; exit 4; }
legs=$(printf '%s\n' "$leg_table" | awk -F'|' 'NF{print $1}')

# --- written-down non-legs -----------------------------------------------------
# path|reason. Adding a row is a claim to defend; deleting one is how a new hole
# gets announced.
NONLEG_TABLE="
/usr/bin/env|interpreter: the exec of the unit, not a published file
/usr/bin/bash|interpreter: carries the flock prelude, execs the real object below it
/bin/sh|interpreter
/usr/bin/python3|interpreter
"

nonleg_reason() {
  printf '%s\n' "$NONLEG_TABLE" | awk -F'|' -v p="$1" 'NF && $1==p {print $2; found=1} END{exit !found}'
}

workspace=$(mktemp -d)
trap 'rm -rf "$workspace"' EXIT
: > "$workspace/directives"

# tracked basenames: the universe this room is responsible for
git -C "$REPO" ls-files -- chat-room | awk -F/ 'NF{print $NF}' | sort -u > "$workspace/repo.basenames"

in_universe() {  # $1 = absolute path
  local p=$1 base
  base=$(basename "$p")
  grep -qxF "$base" "$workspace/repo.basenames" && return 0
  printf '%s\n' "$legs" | grep -qxF "$base" && return 0
  case "$p" in "$NAS"/*|"$BUSDIR"/*) return 0 ;; esac
  return 1
}

# --- repo units ----------------------------------------------------------------
for f in "$REPO_SYSTEMD"/*; do
  [ -f "$f" ] || continue
  name=$(basename "$f")
  while IFS= read -r line; do
    printf 'repo\t%s\t%s\n' "$name" "$line" >> "$workspace/directives"
  done < <(grep -hE '^(ExecStart|ExecStartPre|ExecStop|ExecReload)=' "$f" 2>/dev/null)
done

# --- installed units -----------------------------------------------------------
scan_root() {  # $1 = root, $2 = user|system
  local root=$1 mode=$2 f name directive pre=""
  [ -d "$root" ] || { echo "SKIP root absent: $root"; return; }
  if [ "$mode" = system ] && [ "$(id -u)" -ne 0 ]; then
    if sudo -n true 2>/dev/null; then pre="sudo -n"; else
      echo "SKIP $root needs root for the units that live there; no non-interactive sudo"
      rc=4
      return
    fi
  fi
  for f in "$root"/*.service; do
    [ -f "$f" ] || continue
    name=$(basename "$f")
    case "$name" in *.bak-*) continue ;; esac
    if [ "$mode" = user ]; then
      directive=$(systemctl --user show -p ExecStart "$name" 2>/dev/null | sed -n 's/^ExecStart=//p')
    else
      directive=$($pre systemctl show -p ExecStart "$name" 2>/dev/null | sed -n 's/^ExecStart=//p')
    fi
    [ -n "${directive:-}" ] || { echo "SKIP unreadable unit: $name"; rc=4; continue; }
    printf 'inst:%s\t%s\t%s\n' "$root" "$name" "$directive" >> "$workspace/directives"
  done
}

echo "root  $REPO_SYSTEMD"
echo "root  $USER_ROOT"
scan_root "$USER_ROOT" user
echo "root  $ROOT_ROOT"
scan_root "$ROOT_ROOT" system

# --- pull script paths out of the directives ----------------------------------
# %h expands to $HOME; the character class stops at quotes, spaces, semicolons and
# redirections so `exec 9>%h/.../x.lock` and the flock prelude do not become paths.
: > "$workspace/paths"
n_skipped_units=0
while IFS=$'\t' read -r origin name directive; do
  [ -n "${directive:-}" ] || continue
  hit=0
  while IFS= read -r p; do
    p=${p//%h/$HOME}
    in_universe "$p" || continue
    hit=1
    printf '%s\t%s\t%s\n' "${origin%%:*}" "$name" "$p"
  done < <(printf '%s\n' "$directive" \
            | grep -oE '(%h[^ "'"'"';|)]*|/[A-Za-z0-9._/-]*)\.(sh|py)') \
    >> "$workspace/paths"
  case "$origin" in inst:*) [ "$hit" -eq 1 ] || n_skipped_units=$((n_skipped_units+1)) ;; esac
done < "$workspace/directives"

sort -u -t$'\t' -k3,3 "$workspace/paths" > "$workspace/paths.uniq"

# --- classify ------------------------------------------------------------------
n_paths=0 n_leg=0 n_inplace=0 n_nonleg=0 n_unwitnessed=0
while IFS=$'\t' read -r origin name p; do
  [ -n "${p:-}" ] || continue
  base=$(basename "$p")
  real=$p
  [ -e "$p" ] && real=$(readlink -f "$p")
  if printf '%s\n' "$legs" | grep -qxF "$base"; then
    state=LEG
  elif [ "$real" = "$p" ] && [ "${real#"$REPO"/}" != "$real" ]; then
    state=INPLACE-REPO
  elif reason=$(nonleg_reason "$p"); then
    state="NONLEG: $reason"
  else
    state=UNWITNESSED
  fi
  n_paths=$((n_paths+1))
  case "$state" in
    LEG)          n_leg=$((n_leg+1)) ;;
    INPLACE-REPO) n_inplace=$((n_inplace+1)) ;;
    NONLEG:*)     n_nonleg=$((n_nonleg+1)) ;;
    *)            n_unwitnessed=$((n_unwitnessed+1)); rc=1 ;;
  esac
  printf 'path  %-46s %-30s %-6s %s\n' "$p" "$name" "$origin" "$state"
done < "$workspace/paths.uniq"

# --- symmetry: table rows that no unit execs -----------------------------------
# Not a failure -- a leg can be invoked by hand or from another script -- but the
# table and the exec set are two different questions and both get printed.
n_legless=0
while IFS= read -r l; do
  [ -n "$l" ] || continue
  cut -f3 "$workspace/paths.uniq" | awk -F/ -v b="$l" '$NF==b {found=1} END{exit !found}' \
    || { echo "leg-no-executor  $l  a LEG_TABLE row, no installed unit execs it"
         n_legless=$((n_legless+1)); }
done <<< "$legs"

# --- unit set, both directions -------------------------------------------------
n_extra=0 n_orphan=0
for f in "$REPO_SYSTEMD"/*; do
  [ -f "$f" ] || continue
  name=$(basename "$f")
  if [ ! -f "$USER_ROOT/$name" ] && [ ! -f "$ROOT_ROOT/$name" ]; then
    n_orphan=$((n_orphan+1)); rc=1
    echo "unit-orphan  $name  in repo, not installed in either root"
  fi
done
# an in-universe installed unit is one whose path set reached the ledger above
cut -f2 "$workspace/paths.uniq" | sort -u > "$workspace/units.in"
while IFS= read -r name; do
  [ -n "$name" ] || continue
  [ -f "$REPO_SYSTEMD/$name" ] && continue
  for d in "$USER_ROOT" "$ROOT_ROOT"; do
    if [ -f "$d/$name" ]; then
      echo "unit-extra   $name  installed in $d, no repo copy"
      n_extra=$((n_extra+1))
      break
    fi
  done
done < "$workspace/units.in"
[ "$n_extra" -eq 0 ] || echo "note  unit-extra is the reverse hole: the unit runs an object no roster row describes"

echo "exec: repo=$REPO paths=$n_paths legs=$n_leg inplace=$n_inplace nonleg=$n_nonleg unwitnessed=$n_unwitnessed extra_units=$n_extra orphan_units=$n_orphan legless=$n_legless skipped_host_units=$n_skipped_units rc=$rc"
exit $rc
