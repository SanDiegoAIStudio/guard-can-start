"""Plant one exact bug in a copy of the checker and require the tests to fail it.

source: a checker that lost one decision, one parser, or one list rule must not
still pass. A swap that is not in the source exactly once is an error.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECKER = os.path.join(ROOT, "check-guard.sh")

MUTATIONS = [
    (
        "missing_guard_inverted",
        '[ -f "$cg_guard_path" ] || { printf \'MISSING: %s\\n\' "$cg_guard"; return 1; }',
        '[ ! -f "$cg_guard_path" ] || { printf \'MISSING: %s\\n\' "$cg_guard"; return 1; }',
    ),
    (
        "missing_input_inverted",
        '[ -f "$cg_bad" ] && [ -r "$cg_bad" ] || { printf \'MISSING INPUT: %s\\n\' "$cg_bad"; return 1; }',
        '[ ! -f "$cg_bad" ] && [ -r "$cg_bad" ] || { printf \'MISSING INPUT: %s\\n\' "$cg_bad"; return 1; }',
    ),
    (
        "order_guard_before_input",
        "  step_missing_guard || return 1\n  step_missing_inputs || return 1\n",
        "  step_missing_inputs || return 1\n  step_missing_guard || return 1\n",
    ),
    (
        "order_input_before_parse",
        "  step_missing_inputs || return 1\n  step_parse || return 1\n",
        "  step_parse || return 1\n  step_missing_inputs || return 1\n",
    ),
    (
        "order_parse_before_executable",
        "  step_parse || return 1\n  step_executable || return 1\n",
        "  step_executable || return 1\n  step_parse || return 1\n",
    ),
    (
        "order_executable_before_bad",
        "  step_executable || return 1\n  step_bad_input || return 1\n",
        "  step_bad_input || return 1\n  step_executable || return 1\n",
    ),
    (
        "order_bad_before_good",
        "  step_bad_input || return 1\n  step_good_input || return 1\n",
        "  step_good_input || return 1\n  step_bad_input || return 1\n",
    ),
    (
        "yes_before_refuse_code",
        '  [ "$cg_status" -eq 0 ] && { fail_run "SAID YES TO KNOWN-BAD INPUT: $cg_guard" "$cg_bad_out" || return 1; }\n'
        '  if [ -n "$cg_refuse" ]; then\n'
        '    [ "$cg_status" -eq "$cg_refuse" ] && return 0\n'
        '    fail_run "WRONG EXIT: $cg_guard exited $cg_status on the bad input, a refusal is $cg_refuse" "$cg_bad_out" || return 1\n'
        "  fi\n",
        '  if [ -n "$cg_refuse" ]; then\n'
        '    [ "$cg_status" -eq "$cg_refuse" ] && return 0\n'
        '    fail_run "WRONG EXIT: $cg_guard exited $cg_status on the bad input, a refusal is $cg_refuse" "$cg_bad_out" || return 1\n'
        "  fi\n"
        '  [ "$cg_status" -eq 0 ] && { fail_run "SAID YES TO KNOWN-BAD INPUT: $cg_guard" "$cg_bad_out" || return 1; }\n',
    ),
    ("parse_limit_one", "cg_parse_limit=5", "cg_parse_limit=1"),
    ("parse_limit_six", "cg_parse_limit=5", "cg_parse_limit=6"),
    ("shebang_ignored", '[ "$cg_shebang" -eq 1 ]', '[ "$cg_shebang" -eq 9 ]'),
    (
        "extension_ignored",
        'else parser_from_ext "$1"; fi',
        'else parser_from_ext "/no/ext"; fi',
    ),
    (
        "bash_shebang_parsed_as_sh",
        "    bash) cg_parser=bash ;;",
        "    bash) cg_parser=sh ;;",
    ),
    (
        "zsh_shebang_parsed_as_sh",
        "    zsh) cg_parser=zsh ;;",
        "    zsh) cg_parser=sh ;;",
    ),
    (
        "ksh_shebang_not_parsed",
        "ksh|ksh93) cg_parser=$1 ;;",
        "ksh|ksh93) cg_parser=none ;;",
    ),
    (
        "ksh93_parsed_with_ksh",
        "ksh|ksh93) cg_parser=$1 ;;",
        "ksh|ksh93) cg_parser=ksh ;;",
    ),
    (
        "ksh_extension_ignored",
        "*.ksh) cg_parser=ksh ;;",
        "*.ksh) cg_parser=none ;;",
    ),
    (
        "ksh_parsed_with_sh",
        'ksh|ksh93) "$cg_parser" -n "$cg_guard_path" >"$cg_parse_out" 2>&1 ;;',
        'ksh|ksh93) sh -n "$cg_guard_path" >"$cg_parse_out" 2>&1 ;;',
    ),
    (
        "python2_compiled_with_python3",
        "    python2|python2.*) cg_parser=none ;;\n",
        "",
    ),
    (
        "python_shebang_not_parsed",
        "python|python[0-9]*) cg_parser=python ;;",
        "python|python[0-9]*) cg_parser=none ;;",
    ),
    (
        "node_shebang_not_parsed",
        "node|node[0-9]*) cg_parser=node ;;",
        "node|node[0-9]*) cg_parser=none ;;",
    ),
    (
        "sh_extension_not_parsed",
        "*.sh) cg_parser=sh ;;",
        "*.sh) cg_parser=none ;;",
    ),
    (
        "bash_extension_not_parsed",
        "*.bash) cg_parser=bash ;;",
        "*.bash) cg_parser=none ;;",
    ),
    (
        "node_extension_not_parsed",
        "*.js|*.mjs|*.cjs) cg_parser=node ;;",
        "*.js|*.mjs|*.cjs) cg_parser=none ;;",
    ),
    (
        "python_parse_writes_a_file",
        "python) PYTHONDONTWRITEBYTECODE=1 python3 -c '\n"
        "import sys\n"
        "p = sys.argv[1]\n"
        "try:\n"
        "    with open(p, \"rb\") as fh:\n"
        "        compile(fh.read(), p, \"exec\")\n"
        "except SyntaxError as err:\n"
        "    where = \"line %s: \" % err.lineno if err.lineno else \"\"\n"
        "    sys.stdout.write(\"%s: %s%s\\n\" % (p, where, err.msg))\n"
        "    sys.exit(1)\n"
        "except Exception as err:\n"
        "    sys.stdout.write(\"%s: %s\\n\" % (p, err))\n"
        "    sys.exit(1)\n"
        "    ' \"$cg_guard_path\" >\"$cg_parse_out\" 2>&1 ;;",
        "python) python3 -c 'import os,sys,py_compile; os.environ.pop(\"PYTHONDONTWRITEBYTECODE\", None); py_compile.compile(sys.argv[1])' \"$cg_guard_path\" >\"$cg_parse_out\" 2>&1 ;;",
    ),
    (
        "python_program_indented",
        "import sys\n"
        "p = sys.argv[1]\n"
        "try:\n"
        "    with open(p, \"rb\") as fh:\n"
        "        compile(fh.read(), p, \"exec\")\n"
        "except SyntaxError as err:\n"
        "    where = \"line %s: \" % err.lineno if err.lineno else \"\"\n"
        "    sys.stdout.write(\"%s: %s%s\\n\" % (p, where, err.msg))\n"
        "    sys.exit(1)\n"
        "except Exception as err:\n"
        "    sys.stdout.write(\"%s: %s\\n\" % (p, err))\n"
        "    sys.exit(1)\n",
        "    import sys\n"
        "    p = sys.argv[1]\n"
        "    try:\n"
        "        with open(p, \"rb\") as fh:\n"
        "            compile(fh.read(), p, \"exec\")\n"
        "    except SyntaxError as err:\n"
        "        where = \"line %s: \" % err.lineno if err.lineno else \"\"\n"
        "        sys.stdout.write(\"%s: %s%s\\n\" % (p, where, err.msg))\n"
        "        sys.exit(1)\n"
        "    except Exception as err:\n"
        "        sys.stdout.write(\"%s: %s\\n\" % (p, err))\n"
        "        sys.exit(1)\n",
    ),
    (
        "parse_failure_ends_the_list",
        "  printf 'DOES NOT PARSE: %s\\n' \"$cg_guard\"\n",
        "  printf 'DOES NOT PARSE: %s\\n' \"$cg_guard\"\n  cg_list_eof=1\n",
    ),
    (
        "parse_failure_overwrites_the_list_count",
        "  done < \"$cg_parse_out\"\n",
        "  done < \"$cg_parse_out\"\n  cg_list_n=$cg_parse_n\n",
    ),
    (
        "stdin_ignored",
        '  if [ "$cg_stdin" -eq 1 ]; then\n    run_capture "$1" "$cg_run_secs" "$2" "$cg_guard_path"\n',
        '  if [ "$cg_stdin" -eq 2 ]; then\n    run_capture "$1" "$cg_run_secs" "$2" "$cg_guard_path"\n',
    ),
    (
        "stdin_forced",
        '  if [ "$cg_stdin" -eq 1 ]; then\n    run_capture "$1" "$cg_run_secs" "$2" "$cg_guard_path"\n',
        '  if [ 1 -eq 1 ]; then\n    run_capture "$1" "$cg_run_secs" "$2" "$cg_guard_path"\n',
    ),
    (
        "refuse_code_ignored",
        'if [ -n "$cg_refuse" ]; then',
        'if [ -n "" ]; then',
    ),
    (
        "refuse_code_never_matches",
        '[ "$cg_status" -eq "$cg_refuse" ] && return 0',
        '[ "$cg_status" -eq 0 ] && return 0',
    ),
    (
        "good_check_skipped",
        '[ "$cg_has_good" -eq 1 ] || return 0',
        '[ "$cg_has_good" -eq 2 ] || return 0',
    ),
    (
        "timeout_reported_as_died",
        ': > "$cg_flag"',
        ": > /dev/null",
    ),
    (
        "could_not_run_is_a_refusal",
        '[ "$cg_status" -eq 126 ] || [ "$cg_status" -eq 127 ] && { fail_run "COULD NOT RUN:',
        '[ "$cg_status" -eq 999 ] || [ "$cg_status" -eq 998 ] && { fail_run "COULD NOT RUN:',
    ),
    (
        "died_is_a_refusal",
        '[ "$cg_status" -gt 128 ] && { fail_run "DIED:',
        '[ "$cg_status" -gt 999 ] && { fail_run "DIED:',
    ),
    (
        "said_yes_is_a_refusal",
        '[ "$cg_status" -eq 0 ] && { fail_run "SAID YES TO KNOWN-BAD INPUT:',
        '[ "$cg_status" -eq 3 ] && { fail_run "SAID YES TO KNOWN-BAD INPUT:',
    ),
    (
        "it_said_dropped",
        'printf \'  it said: %s\\n\' "$cg_said_line"',
        ': "$cg_said_line"',
    ),
    (
        "ok_quotes_the_good_run",
        'show_said "$cg_bad_out"',
        'show_said "$cg_tmpdir/good.out"',
    ),
    (
        "said_no_quotes_the_bad_run",
        'fail_run "SAID NO TO KNOWN-GOOD INPUT: $cg_guard (exit $cg_status)" "$cg_tmpdir/good.out"',
        'fail_run "SAID NO TO KNOWN-GOOD INPUT: $cg_guard (exit $cg_status)" "$cg_bad_out"',
    ),
    (
        "count_columns_swapped",
        'printf \'checked %s guards: %s ok, %s failed\\n\' "$cg_list_n" "$cg_list_ok" "$cg_list_failed"',
        'printf \'checked %s guards: %s ok, %s failed\\n\' "$cg_list_n" "$cg_list_failed" "$cg_list_ok"',
    ),
    (
        "failed_list_exits_zero",
        "  [ \"$cg_list_failed\" -eq 0 ] && exit 0\n  exit 1\n",
        "  [ \"$cg_list_failed\" -eq 0 ] && exit 0\n  exit 0\n",
    ),
    (
        "ok_list_exits_one",
        '[ "$cg_list_failed" -eq 0 ] && exit 0',
        '[ "$cg_list_failed" -eq 9 ] && exit 0',
    ),
    (
        "bad_line_renamed",
        "printf 'BAD LINE %s: %s\\n'",
        "printf 'BAD ROW %s: %s\\n'",
    ),
    (
        "empty_list_exits_zero",
        "printf 'checked nothing: %s holds no entry\\n' \"$cg_list\"; exit 2",
        "printf 'checked nothing: %s holds no entry\\n' \"$cg_list\"; exit 0",
    ),
    (
        "unreadable_list_exits_zero",
        "  if [ ! -f \"$cg_list\" ] || [ ! -r \"$cg_list\" ]; then\n"
        "    printf 'checked nothing: cannot read %s\\n' \"$cg_list\"; exit 2\n"
        "  fi\n",
        "  if [ ! -f \"$cg_list\" ] || [ ! -r \"$cg_list\" ]; then\n"
        "    printf 'checked nothing: cannot read %s\\n' \"$cg_list\"; exit 0\n"
        "  fi\n",
    ),
    (
        "guard_name_searches_path",
        "*) cg_guard_path=./$cg_guard ;;",
        "*) cg_guard_path=$cg_guard ;;",
    ),
    ("list_globs_expand", "set -f\n", "set +f\n"),
    (
        "execute_bit_ignored",
        '[ -x "$cg_guard_path" ] || { printf \'NOT EXECUTABLE: %s\\n\' "$cg_guard"; return 1; }',
        '[ -e "$cg_guard_path" ] || { printf \'NOT EXECUTABLE: %s\\n\' "$cg_guard"; return 1; }',
    ),
    (
        "limit_reaches_only_the_guard",
        'cg_t=$(tree_of "$cg_gpid")',
        "cg_t=$cg_gpid",
    ),
    (
        "limit_never_sends_kill",
        'kill -KILL ${cg_t:-$cg_gpid} 2>/dev/null',
        ":",
    ),
    (
        "cut_off_guard_reads_as_an_answer",
        '[ -f "$cg_flag" ] && cg_timed=1',
        '[ -f "$cg_flag" ] && [ "$cg_status" -gt 128 ] && cg_timed=1',
    ),
    (
        "stop_leaves_the_guard",
        'kill_tree KILL "$cg_gpid"',
        ":",
    ),
    (
        "stop_leaves_the_timer",
        'kill_tree KILL "$cg_gpid"; [ -n "$cg_gpid" ] && wait "$cg_gpid" 2>/dev/null\n'
        '  kill_tree TERM "$cg_wpid"',
        'kill_tree KILL "$cg_gpid"; [ -n "$cg_gpid" ] && wait "$cg_gpid" 2>/dev/null\n'
        "  :",
    ),
    (
        "hangup_not_handled",
        "trap 'cleanup_procs; exit 129' HUP\n",
        "",
    ),
    (
        "quick_answer_leaves_the_timer",
        '[ -f "$cg_flag" ] || kill_tree TERM "$cg_wpid"',
        ":",
    ),
    (
        "python_guard_read_as_text",
        'open(p, "rb")',
        "open(p)",
    ),
    (
        "cdpath_honoured",
        "unset CDPATH",
        ":",
    ),
]


def anchors_ok(source):
    bad = False
    for name, old, new in MUTATIONS:
        count = source.count(old)
        if count != 1 or old == new:
            sys.stderr.write("mutation %s: old text found %s times\n" % (name, count))
            bad = True
    return not bad


def syntax_ok(path):
    shells = ["/bin/sh"]
    if os.path.exists("/bin/dash"):
        shells.append("/bin/dash")
    for shell in shells:
        proc = subprocess.run(
            [shell, "-n", path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
        )
        if proc.returncode != 0:
            sys.stderr.write("%s -n failed for a mutation: %s\n" % (shell, proc.stderr))
            return False
    return True


def judge(proc):
    text = proc.stdout + "\n" + proc.stderr
    found = re.search(r"failures=(\d+)", text)
    if proc.returncode == 0:
        return "survived"
    if found and int(found.group(1)) > 0:
        return "caught"
    return "crashed"


def run_one(source, old, new):
    work = tempfile.mkdtemp(prefix="guard-mutate-")
    try:
        mutated = source.replace(old, new, 1)
        checker = os.path.join(work, "check-guard.sh")
        with open(checker, "w") as handle:
            handle.write(mutated)
        os.chmod(checker, 0o755)
        if not syntax_ok(checker):
            return "syntax"
        ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
        shutil.copytree(os.path.join(ROOT, "tests"), os.path.join(work, "tests"), ignore=ignore)
        shutil.copytree(os.path.join(ROOT, "examples"), os.path.join(work, "examples"), ignore=ignore)
        proc = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-f"],
            cwd=work,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=120,
        )
        return judge(proc)
    except subprocess.TimeoutExpired:
        return "crashed"
    finally:
        shutil.rmtree(work)


def main():
    with open(CHECKER, "r") as handle:
        source = handle.read()
    if not anchors_ok(source):
        return 2
    caught = 0
    total = len(MUTATIONS)
    for name, old, new in MUTATIONS:
        status = run_one(source, old, new)
        sys.stdout.write("%s %s\n" % (name, status))
        sys.stdout.flush()
        if status == "syntax":
            return 2
        if status == "caught":
            caught += 1
    sys.stdout.write("caught %s of %s\n" % (caught, total))
    return 0 if caught == total else 1


if __name__ == "__main__":
    sys.exit(main())
