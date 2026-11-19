#!/bin/sh
# Can this guard still run, and does it still refuse?

set -f
unset CDPATH
usage() { printf 'usage: %s\n' "$1" >&2; exit 2; }
strip_zeros() {
  cg_num=$1
  while :; do case $cg_num in 0*) cg_num=${cg_num#0} ;; *) break ;; esac; done
}
is_pos_whole() {
  case $1 in ''|*[!0-9]*) return 1 ;; esac
  strip_zeros "$1"; [ -n "$cg_num" ]
}
is_refuse_code() {
  case $1 in ''|*[!0-9]*) return 1 ;; esac
  strip_zeros "$1"
  case $cg_num in
    [1-9]|[1-9][0-9]|1[0-9][0-9]|2[0-4][0-9]|25[0-5]) return 0 ;;
    *) return 1 ;;
  esac
}
# Options in "$@". cg_strict=1 exits on a bad option; otherwise the function returns 1.
parse_opts() {
  cg_stdin=0; cg_has_good=0; cg_good=; cg_refuse=
  while [ $# -gt 0 ]; do
    case $1 in
      --stdin) cg_stdin=1; shift ;;
      --good)
        [ $# -ge 2 ] || { [ "$cg_strict" -eq 1 ] && usage "a file must follow --good"; return 1; }
        cg_has_good=1; cg_good=$2; shift 2 ;;
      --refuse-code)
        [ $# -ge 2 ] && is_refuse_code "$2" || { [ "$cg_strict" -eq 1 ] && usage "--refuse-code needs a whole number from 1 to 255"; return 1; }
        cg_refuse=$cg_num; shift 2 ;;
      --timeout)
        [ $# -ge 2 ] && is_pos_whole "$2" || { [ "$cg_strict" -eq 1 ] && usage "--timeout needs a positive whole number"; return 1; }
        cg_timeout=$cg_num; shift 2 ;;
      --list)
        [ "$cg_strict" -eq 1 ] && [ $# -ge 2 ] || { [ "$cg_strict" -eq 1 ] && usage "check-guard.sh --list FILE [--timeout SEC]"; return 1; }
        cg_list=$2; shift 2 ;;
      -*) [ "$cg_strict" -eq 1 ] && usage "unknown option: $1"; return 1 ;;
      *) break ;;
    esac
  done
  cg_left=$#; cg_guard=$1; cg_bad=$2
  return 0
}
cg_timeout=10; cg_list=; cg_strict=1
parse_opts "$@" || exit 2
if [ -n "$cg_list" ]; then
  if [ "$cg_stdin" -eq 1 ] || [ "$cg_has_good" != 0 ] || [ -n "${cg_refuse}" ] || [ "$cg_left" -ne 0 ]; then
    usage "check-guard.sh --list FILE [--timeout SEC]"
  fi
else
  [ "$cg_left" -eq 2 ] || usage "check-guard.sh [--stdin] [--good GOOD_INPUT] [--refuse-code N] [--timeout SEC] GUARD BAD_INPUT"
fi
cg_tmpdir=$(mktemp -d) || usage "could not make a temp folder"
cg_gpid=; cg_wpid=
# tree_of PID: PID and every process under it, one per line
tree_of() {
  ps -A -o pid= -o ppid= 2>/dev/null | awk -v root="$1" '
    $1 ~ /^[0-9]+$/ && $2 ~ /^[0-9]+$/ { kids[$2] = kids[$2] " " $1 }
    END {
      n = 1; q[1] = root; seen[root] = 1
      for (i = 1; i <= n; i++) {
        m = split(kids[q[i]], a, " ")
        for (j = 1; j <= m; j++) if (!(a[j] in seen)) { seen[a[j]] = 1; q[++n] = a[j] }
      }
      for (i = 1; i <= n; i++) print q[i]
    }' 2>/dev/null
}
# kill_tree SIGNAL PID
kill_tree() {
  case $2 in ''|0|1|"$$") return 0 ;; esac
  cg_kt=$(tree_of "$2")
  kill "-$1" ${cg_kt:-$2} 2>/dev/null
  return 0
}
cleanup_procs() {
  kill_tree KILL "$cg_gpid"; [ -n "$cg_gpid" ] && wait "$cg_gpid" 2>/dev/null
  kill_tree TERM "$cg_wpid"
}
trap 'cleanup_procs; exit 129' HUP
trap 'cleanup_procs; exit 130' INT
trap 'cleanup_procs; exit 143' TERM
trap 'rm -rf "$cg_tmpdir"' EXIT
# run_capture OUTFILE SECS STDIN CMD...
run_capture() {
  cg_capture=$1; cg_run_secs=$2; cg_stdin_file=$3; shift 3
  cg_flag=$cg_tmpdir/flag
  rm -f "$cg_flag" "$cg_capture"
  cg_timed=0
  /bin/sh -c 'exec "$@"' sh "$@" >"$cg_capture" 2>&1 <"$cg_stdin_file" &
  cg_gpid=$!
  (
    sleep "$cg_run_secs"
    if kill -0 "$cg_gpid" 2>/dev/null; then
      : > "$cg_flag"
      cg_t=$(tree_of "$cg_gpid")
      kill -TERM ${cg_t:-$cg_gpid} 2>/dev/null
      sleep 1
      kill -KILL ${cg_t:-$cg_gpid} 2>/dev/null
    fi
  ) >/dev/null 2>&1 &
  cg_wpid=$!
  wait "$cg_gpid" 2>/dev/null
  cg_status=$?
  cg_gpid=
  if [ "$cg_status" -eq 1 ]; then
    cg_n=0; cg_a=; cg_b=
    while IFS= read -r cg_pline || [ -n "$cg_pline" ]; do
      cg_n=$((cg_n + 1)); [ "$cg_n" -eq 1 ] && cg_a=$cg_pline; [ "$cg_n" -eq 2 ] && cg_b=$cg_pline; [ "$cg_n" -gt 2 ] && break
    done < "$cg_capture"
    [ "$cg_n" -eq 2 ] && case $cg_a in *'bad interpreter:'*) case $cg_b in *'Undefined error: 0')
      cg_status=126; cg_abs=$(CDPATH= cd "$(dirname "$1")" && pwd)/$(basename "$1"); [ -n "$cg_abs" ] && case $cg_a in *"$cg_abs"*) cg_a=${cg_a%%"$cg_abs"*}$1${cg_a#*"$cg_abs"} ;; esac; printf '%s\n' "$cg_a" > "$cg_capture" ;; esac ;; esac
  fi
  [ -f "$cg_flag" ] || kill_tree TERM "$cg_wpid"
  wait "$cg_wpid" 2>/dev/null
  cg_wpid=
  [ -f "$cg_flag" ] && cg_timed=1
  return 0
}
show_said() {
  if [ -s "$1" ]; then
    IFS= read -r cg_said_line < "$1" || true
    printf '  it said: %s\n' "$cg_said_line"
  fi
}
name_to_parser() {
  case $1 in
    sh|dash|ash) cg_parser=sh ;;
    ksh|ksh93) cg_parser=$1 ;;
    bash) cg_parser=bash ;;
    zsh) cg_parser=zsh ;;
    python2|python2.*) cg_parser=none ;;
    python|python[0-9]*) cg_parser=python ;;
    node|node[0-9]*) cg_parser=node ;;
    *) cg_parser=none ;;
  esac
}
parser_from_shebang() {
  cg_sb_rest=${cg_first#\#\!}; cg_sb_ifs=$IFS; IFS=$(printf ' \t'); set -- $cg_sb_rest; IFS=$cg_sb_ifs
  cg_base=${1##*/}
  if [ "$cg_base" = env ]; then
    shift; while [ -n "$1" ] && [ "${1#-}" != "$1" ]; do shift; done
    cg_base=${1##*/}
  fi
  name_to_parser "$cg_base"
}
parser_from_ext() {
  case $1 in
    *.sh) cg_parser=sh ;;
    *.bash) cg_parser=bash ;;
    *.zsh) cg_parser=zsh ;;
    *.ksh) cg_parser=ksh ;;
    *.py) cg_parser=python ;;
    *.js|*.mjs|*.cjs) cg_parser=node ;;
    *) cg_parser=none ;;
  esac
}
classify() {
  cg_parser=none; cg_parse_reason="no parser for this file"; cg_first=; cg_shebang=0
  IFS= read -r cg_first < "$1" || true
  case $cg_first in "#!"*) cg_shebang=1 ;; esac
  if [ "$cg_shebang" -eq 1 ]; then parser_from_shebang; else parser_from_ext "$1"; fi
  [ "$cg_parser" = none ] && return
  case $cg_parser in python) cg_bin=python3 ;; *) cg_bin=$cg_parser ;; esac
  command -v "$cg_bin" >/dev/null 2>&1 || { cg_parse_reason="$cg_bin is not installed"; cg_parser=none; }
}
step_missing_guard() {
  [ -f "$cg_guard_path" ] || { printf 'MISSING: %s\n' "$cg_guard"; return 1; }
}
step_missing_inputs() {
  [ -f "$cg_bad" ] && [ -r "$cg_bad" ] || { printf 'MISSING INPUT: %s\n' "$cg_bad"; return 1; }
  if [ "$cg_has_good" -ne 0 ]; then
    [ -f "$cg_good" ] && [ -r "$cg_good" ] || { printf 'MISSING INPUT: %s\n' "$cg_good"; return 1; }
  fi
}
step_parse() {
  classify "$cg_guard_path"
  if [ "$cg_parser" = none ]; then
    cg_parse_phrase="parse not checked ($cg_parse_reason)"; return 0
  fi
  cg_parse_out=$cg_tmpdir/parse.out; : > "$cg_parse_out"
  case $cg_parser in
    sh) sh -n "$cg_guard_path" >"$cg_parse_out" 2>&1 ;;
    bash) bash -n "$cg_guard_path" >"$cg_parse_out" 2>&1 ;;
    zsh) zsh -n "$cg_guard_path" >"$cg_parse_out" 2>&1 ;;
    ksh|ksh93) "$cg_parser" -n "$cg_guard_path" >"$cg_parse_out" 2>&1 ;;
    node) node --check "$cg_guard_path" >"$cg_parse_out" 2>&1 ;;
    python) PYTHONDONTWRITEBYTECODE=1 python3 -c '
import sys
p = sys.argv[1]
try:
    with open(p, "rb") as fh:
        compile(fh.read(), p, "exec")
except SyntaxError as err:
    where = "line %s: " % err.lineno if err.lineno else ""
    sys.stdout.write("%s: %s%s\n" % (p, where, err.msg))
    sys.exit(1)
except Exception as err:
    sys.stdout.write("%s: %s\n" % (p, err))
    sys.exit(1)
    ' "$cg_guard_path" >"$cg_parse_out" 2>&1 ;;
  esac
  [ "$?" -eq 0 ] && { cg_parse_phrase="parses"; return 0; }
  printf 'DOES NOT PARSE: %s\n' "$cg_guard"
  cg_parse_limit=5; cg_parse_n=0; cg_parse_eof=0
  while [ "$cg_parse_eof" -eq 0 ]; do
    cg_parse_line=
    if IFS= read -r cg_parse_line; then :; else cg_parse_eof=1; [ -n "$cg_parse_line" ] || break; fi
    cg_parse_n=$((cg_parse_n + 1))
    [ "$cg_parse_n" -le "$cg_parse_limit" ] && printf '  %s\n' "$cg_parse_line"
  done < "$cg_parse_out"
  return 1
}
step_executable() {
  [ -x "$cg_guard_path" ] || { printf 'NOT EXECUTABLE: %s\n' "$cg_guard"; return 1; }
}
launch_guard() {
  if [ "$cg_stdin" -eq 1 ]; then
    run_capture "$1" "$cg_run_secs" "$2" "$cg_guard_path"
  else
    run_capture "$1" "$cg_run_secs" /dev/null "$cg_guard_path" "$2"
  fi
}
fail_run() { printf '%s\n' "$1"; show_said "$2"; return 1; }
step_bad_input() {
  cg_run_secs=$cg_timeout
  launch_guard "$cg_tmpdir/bad.out" "$cg_bad"
  cg_bad_status=$cg_status; cg_bad_out=$cg_tmpdir/bad.out
  [ "$cg_timed" -eq 1 ] && { fail_run "TIMED OUT: $cg_guard gave no answer in ${cg_timeout}s" "$cg_bad_out" || return 1; }
  [ "$cg_status" -eq 0 ] && { fail_run "SAID YES TO KNOWN-BAD INPUT: $cg_guard" "$cg_bad_out" || return 1; }
  if [ -n "$cg_refuse" ]; then
    [ "$cg_status" -eq "$cg_refuse" ] && return 0
    fail_run "WRONG EXIT: $cg_guard exited $cg_status on the bad input, a refusal is $cg_refuse" "$cg_bad_out" || return 1
  fi
  [ "$cg_status" -eq 126 ] || [ "$cg_status" -eq 127 ] && { fail_run "COULD NOT RUN: $cg_guard (exit $cg_status)" "$cg_bad_out" || return 1; }
  [ "$cg_status" -gt 128 ] && { fail_run "DIED: $cg_guard (exit $cg_status)" "$cg_bad_out" || return 1; }
  return 0
}
step_good_input() {
  [ "$cg_has_good" -eq 1 ] || return 0
  cg_run_secs=$cg_timeout
  launch_guard "$cg_tmpdir/good.out" "$cg_good"
  [ "$cg_timed" -eq 1 ] && { fail_run "TIMED OUT: $cg_guard gave no answer in ${cg_timeout}s" "$cg_tmpdir/good.out" || return 1; }
  [ "$cg_status" -eq 0 ] && return 0
  fail_run "SAID NO TO KNOWN-GOOD INPUT: $cg_guard (exit $cg_status)" "$cg_tmpdir/good.out" || return 1
}
step_ok() {
  if [ "$cg_has_good" = 1 ]; then
    printf 'OK: %s %s, starts, said no to the bad input (exit %s), said yes to the good input\n' "$cg_guard" "$cg_parse_phrase" "$cg_bad_status"
  else
    printf 'OK: %s %s, starts, said no to the bad input (exit %s); no good input given\n' "$cg_guard" "$cg_parse_phrase" "$cg_bad_status"
  fi
  show_said "$cg_bad_out"
  return 0
}
check_one() {
  case $cg_guard in */*) cg_guard_path=$cg_guard ;; *) cg_guard_path=./$cg_guard ;; esac
  step_missing_guard || return 1
  step_missing_inputs || return 1
  step_parse || return 1
  step_executable || return 1
  step_bad_input || return 1
  step_good_input || return 1
  step_ok
}
run_list() {
  if [ ! -f "$cg_list" ] || [ ! -r "$cg_list" ]; then
    printf 'checked nothing: cannot read %s\n' "$cg_list"; exit 2
  fi
  case $cg_list in */*) cg_list_dir=${cg_list%/*} ;; *) cg_list_dir=. ;; esac
  [ -n "$cg_list_dir" ] || cg_list_dir=/
  if ! exec 3< "$cg_list"; then
    printf 'checked nothing: cannot read %s\n' "$cg_list"; exit 2
  fi
  cd "$cg_list_dir" || { printf 'checked nothing: cannot read %s\n' "$cg_list"; exit 2; }
  cg_list_n=0; cg_list_ok=0; cg_list_failed=0; cg_list_lineno=0; cg_list_eof=0; cg_list_timeout=$cg_timeout
  while [ "$cg_list_eof" -eq 0 ]; do
    cg_list_line=
    if IFS= read -r cg_list_line <&3; then :; else cg_list_eof=1; [ -n "$cg_list_line" ] || break; fi
    cg_list_lineno=$((cg_list_lineno + 1))
    case $cg_list_line in ''|'#'*) continue ;; esac
    cg_list_ifs=$IFS; IFS=$(printf ' \t'); set -- $cg_list_line; IFS=$cg_list_ifs
    [ $# -eq 0 ] && continue
    cg_list_n=$((cg_list_n + 1)); cg_timeout=$cg_list_timeout; cg_strict=0
    if parse_opts "$@" && [ "$cg_left" -eq 2 ]; then
      if check_one; then cg_list_ok=$((cg_list_ok + 1)); else cg_list_failed=$((cg_list_failed + 1)); fi
    else
      printf 'BAD LINE %s: %s\n' "$cg_list_lineno" "$cg_list_line"
      cg_list_failed=$((cg_list_failed + 1))
    fi
  done
  [ "$cg_list_n" -eq 0 ] && { printf 'checked nothing: %s holds no entry\n' "$cg_list"; exit 2; }
  printf 'checked %s guards: %s ok, %s failed\n' "$cg_list_n" "$cg_list_ok" "$cg_list_failed"
  [ "$cg_list_failed" -eq 0 ] && exit 0
  exit 1
}
if [ -n "$cg_list" ]; then run_list; fi
check_one
exit $?
