"""One guard: each quiet failure has its own line, in order."""

import os
import shutil
import signal
import subprocess
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "check-guard.sh")
SHELL = os.environ.get("CHECK_GUARD_SHELL", "/bin/sh")


class CheckGuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile_dir()
        self.addCleanup(shutil.rmtree, self.tmp)

    def write(self, name, text, executable=True):
        path = os.path.join(self.tmp, name)
        parent = os.path.dirname(path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        with open(path, "w") as handle:
            handle.write(text)
        os.chmod(path, 0o755 if executable else 0o644)
        return path

    def run_check(self, args, stdin=None, timeout=20, env=None, cwd=None):
        proc = subprocess.Popen(
            [SHELL, SCRIPT] + list(args),
            cwd=cwd or self.tmp,
            stdin=subprocess.PIPE if stdin is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            env=env,
        )
        try:
            out, err = proc.communicate(input=stdin, timeout=timeout)
        except subprocess.TimeoutExpired:
            for pid in descendant_pids(proc.pid):
                kill_one(pid)
            kill_one(proc.pid)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                kill_one(proc.pid)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
            self.fail("checker still running after %ss" % timeout)
        return proc.returncode, out, err

    def write_standin(self, name, text):
        bindir = os.path.join(self.tmp, "bin")
        if not os.path.isdir(bindir):
            os.makedirs(bindir)
        path = os.path.join(bindir, name)
        with open(path, "w") as handle:
            handle.write(text)
        os.chmod(path, 0o755)
        return bindir

    def prepend_bindir(self):
        env = os.environ.copy()
        env["PATH"] = os.path.join(self.tmp, "bin") + os.pathsep + env.get("PATH", "")
        return env

    def test_missing_guard(self):
        """source: a guard path with no regular file is MISSING, which is not a refusal."""
        self.write("bad.txt", "x\n", executable=False)
        code, out, err = self.run_check(["no-such.sh", "bad.txt"])
        self.assertEqual(err, "")
        self.assertEqual(code, 1)
        self.assertIn("MISSING: no-such.sh", out)
        self.assertNotIn("OK:", out)

    def test_name_without_a_slash_is_not_taken_from_path(self):
        """source: a guard name with no slash is a file in the current folder, never a PATH lookup."""
        self.write("bad.txt", "exit 1\n", executable=False)
        code, out, err = self.run_check(["sh", "bad.txt"])
        self.assertEqual(err, "")
        self.assertEqual(code, 1)
        self.assertIn("MISSING: sh", out)

    def test_missing_guard_wins_over_missing_input(self):
        """source: a missing guard is reported before a missing input, so the first failure is the guard itself."""
        code, out, err = self.run_check(["no-such.sh", "no-such.txt"])
        self.assertEqual(err, "")
        self.assertEqual(code, 1)
        self.assertIn("MISSING: no-such.sh", out)
        self.assertNotIn("MISSING INPUT:", out)

    def test_missing_input(self):
        """source: a bad input that is not a readable file is MISSING INPUT, so a missing file is not read as a refusal."""
        self.write("g.sh", "#!/bin/sh\nexit 1\n")
        code, out, err = self.run_check(["g.sh", "no-such.txt"])
        self.assertEqual(err, "")
        self.assertEqual(code, 1)
        self.assertIn("MISSING INPUT: no-such.txt", out)

    def test_missing_good_input(self):
        """source: a known-good file that is not readable fails as MISSING INPUT before the guard is run."""
        self.write("g.sh", "#!/bin/sh\nexit 1\n")
        self.write("bad.txt", "x\n", executable=False)
        code, out, err = self.run_check(["--good", "no-good.txt", "g.sh", "bad.txt"])
        self.assertEqual(err, "")
        self.assertEqual(code, 1)
        self.assertIn("MISSING INPUT: no-good.txt", out)

    def test_missing_input_wins_over_a_parse_error(self):
        """source: a missing input is reported before a parse error, so the guard is not blamed for a file it never saw."""
        self.write("g.sh", "#!/bin/sh\necho \"\n")
        code, out, err = self.run_check(["g.sh", "no-such.txt"])
        self.assertEqual(err, "")
        self.assertEqual(code, 1)
        self.assertIn("MISSING INPUT: no-such.txt", out)
        self.assertNotIn("DOES NOT PARSE:", out)

    def test_parse_error_is_shown_with_at_most_five_lines(self):
        """source: a syntax error prints DOES NOT PARSE and at most five lines of the parser message."""
        self.write("g.sh", "#!/bin/sh\necho \"\n")
        self.write("bad.txt", "x\n", executable=False)
        code, out, err = self.run_check(["g.sh", "bad.txt"])
        self.assertEqual(err, "")
        self.assertEqual(code, 1)
        self.assertIn("DOES NOT PARSE: g.sh", out)
        parser = subprocess.run(
            ["sh", "-n", "./g.sh"],
            cwd=self.tmp,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
        )
        parser_lines = parser.stdout.splitlines()
        self.assertGreater(len(parser_lines), 0)
        indented = [line for line in out.splitlines() if line.startswith("  ")]
        self.assertEqual(indented, ["  " + line for line in parser_lines[:5]])

    def test_parse_message_shows_the_first_five_lines(self):
        """source: the checker shows the first five lines of a long parser message and no more."""
        self.write_standin(
            "sh",
            "#!/bin/sh\n"
            "if [ \"$1\" = \"-n\" ]; then\n"
            "  printf '%s\\n' 'parse line 1' 'parse line 2' 'parse line 3' 'parse line 4' 'parse line 5' 'parse line 6' 'parse line 7' >&2\n"
            "  exit 2\n"
            "fi\n"
            "exec /bin/sh \"$@\"\n",
        )
        self.write("g.sh", "#!/bin/sh\necho ok\n")
        self.write("bad.txt", "x\n", executable=False)
        code, out, err = self.run_check(["g.sh", "bad.txt"], env=self.prepend_bindir())
        self.assertEqual(err, "")
        self.assertEqual(code, 1)
        self.assertIn("DOES NOT PARSE: g.sh", out)
        indented = [line for line in out.splitlines() if line.startswith("  ")]
        self.assertEqual(
            indented,
            [
                "  parse line 1",
                "  parse line 2",
                "  parse line 3",
                "  parse line 4",
                "  parse line 5",
            ],
        )
        self.assertNotIn("parse line 6", out)

    def test_python_parse_message_is_capped_at_five_lines(self):
        """source: a Python syntax error is one plain line, still within the five-line cap, and the parse step writes no file beside the guard."""
        self.write("g.py", "def (\n", executable=True)
        self.write("bad.txt", "x\n", executable=False)
        code, out, err = self.run_check(["g.py", "bad.txt"])
        self.assertEqual(err, "")
        self.assertEqual(code, 1)
        self.assertIn("DOES NOT PARSE: g.py", out)
        indented = [line for line in out.splitlines() if line.startswith("  ")]
        self.assertEqual(len(indented), 1)
        self.assertLessEqual(len(indented), 5)
        self.assertNotIn("Traceback", out)
        self.assertNotIn("compile(", out)
        self.assertFalse(os.path.isdir(os.path.join(self.tmp, "__pycache__")))
        self.assertEqual(pyc_count(self.tmp), 0)

    def test_parse_wins_over_a_missing_execute_bit(self):
        """source: a file that does not parse is DOES NOT PARSE even when it also is not executable."""
        self.write("g.sh", "#!/bin/sh\necho \"\n", executable=False)
        self.write("bad.txt", "x\n", executable=False)
        code, out, err = self.run_check(["g.sh", "bad.txt"])
        self.assertEqual(err, "")
        self.assertEqual(code, 1)
        self.assertIn("DOES NOT PARSE: g.sh", out)
        self.assertNotIn("NOT EXECUTABLE:", out)

    def test_not_executable_wins_over_a_yes(self):
        """source: a file with no execute bit is NOT EXECUTABLE, not a refusal and not a yes."""
        self.write("g.sh", "#!/bin/sh\nexit 0\n", executable=False)
        self.write("bad.txt", "x\n", executable=False)
        code, out, err = self.run_check(["g.sh", "bad.txt"])
        self.assertEqual(err, "")
        self.assertEqual(code, 1)
        self.assertIn("NOT EXECUTABLE: g.sh", out)
        self.assertNotIn("SAID YES", out)
        self.assertNotIn("COULD NOT RUN:", out)

    def test_bad_input_is_judged_before_the_good_input(self):
        """source: a guard that accepts the bad input is SAID YES even when it would also reject the good input."""
        self.write(
            "g.sh",
            "#!/bin/sh\ncase $1 in *good*) printf 'from-good\\n'; exit 1 ;; esac\n"
            "printf 'from-bad\\n'\nexit 0\n",
        )
        self.write("bad.txt", "x\n", executable=False)
        self.write("good.txt", "y\n", executable=False)
        code, out, err = self.run_check(["--good", "good.txt", "g.sh", "bad.txt"])
        self.assertEqual(err, "")
        self.assertEqual(code, 1)
        self.assertIn("SAID YES TO KNOWN-BAD INPUT: g.sh", out)
        self.assertNotIn("SAID NO", out)
        self.assertIn("it said: from-bad", out)

    def test_ok_without_a_good_input(self):
        """source: a guard that refuses the bad input is OK, and the line says no good input was given."""
        self.write("g.sh", "#!/bin/sh\nexit 1\n")
        self.write("bad.txt", "x\n", executable=False)
        code, out, err = self.run_check(["g.sh", "bad.txt"])
        self.assertEqual(err, "")
        self.assertEqual(code, 0)
        self.assertIn("OK: g.sh parses, starts, said no to the bad input (exit 1); no good input given", out)
        self.assertNotIn("it said:", out)

    def test_ok_with_a_good_input_shows_what_it_said_about_the_bad_one(self):
        """source: with a known-good input the OK line says the guard accepted it, and it said quotes the bad run."""
        self.write(
            "g.sh",
            "#!/bin/sh\ncase $1 in *good*) printf 'from-good\\n'; exit 0 ;; esac\n"
            "printf 'from-bad\\n'\nexit 1\n",
        )
        self.write("bad.txt", "x\n", executable=False)
        self.write("good.txt", "y\n", executable=False)
        code, out, err = self.run_check(["--good", "good.txt", "g.sh", "bad.txt"])
        self.assertEqual(err, "")
        self.assertEqual(code, 0)
        self.assertIn("said yes to the good input", out)
        self.assertIn("it said: from-bad", out)
        self.assertNotIn("it said: from-good", out)

    def test_said_no_quotes_the_good_run(self):
        """source: a guard that rejects the known-good input quotes that run, not the bad one."""
        self.write(
            "g.sh",
            "#!/bin/sh\ncase $1 in *good*) printf 'from-good\\n'; exit 1 ;; esac\n"
            "printf 'from-bad\\n'\nexit 1\n",
        )
        self.write("bad.txt", "x\n", executable=False)
        self.write("good.txt", "y\n", executable=False)
        code, out, err = self.run_check(["--good", "good.txt", "g.sh", "bad.txt"])
        self.assertEqual(err, "")
        self.assertEqual(code, 1)
        self.assertIn("SAID NO TO KNOWN-GOOD INPUT: g.sh (exit 1)", out)
        self.assertIn("it said: from-good", out)
        self.assertNotIn("from-bad", out)

    def test_said_yes(self):
        """source: exit 0 on a known-bad input is SAID YES, which is not a refusal."""
        self.write("g.sh", "#!/bin/sh\nprintf 'all clear\\n'\nexit 0\n")
        self.write("bad.txt", "x\n", executable=False)
        code, out, err = self.run_check(["g.sh", "bad.txt"])
        self.assertEqual(err, "")
        self.assertEqual(code, 1)
        self.assertIn("SAID YES TO KNOWN-BAD INPUT: g.sh", out)
        self.assertIn("it said: all clear", out)

    def test_exit_zero_stays_a_yes_when_a_refuse_code_is_set(self):
        """source: exit 0 is SAID YES even when a refuse code is set, because a yes is decided first."""
        self.write("g.sh", "#!/bin/sh\nexit 0\n")
        self.write("bad.txt", "x\n", executable=False)
        code, out, err = self.run_check(["--refuse-code", "1", "g.sh", "bad.txt"])
        self.assertEqual(err, "")
        self.assertEqual(code, 1)
        self.assertIn("SAID YES TO KNOWN-BAD INPUT: g.sh", out)

    def test_stdin_mode(self):
        """source: with --stdin the guard reads the bad file on standard input and is not given the path as an argument."""
        script = "#!/bin/sh\nif [ $# -gt 0 ]; then exit 0; fi\nIFS= read -r line || true\ncase $line in BAD*) exit 1 ;; esac\nexit 0\n"
        self.write("g.sh", script)
        self.write("bad.txt", "BAD thing\n", executable=False)
        code, out, err = self.run_check(["--stdin", "g.sh", "bad.txt"])
        self.assertEqual(err, "")
        self.assertEqual(code, 0)
        self.assertIn("OK:", out)

    def test_default_mode_passes_the_path(self):
        """source: without --stdin the guard is started with the bad file as its first argument and an empty standard input."""
        script = "#!/bin/sh\nif [ $# -gt 0 ]; then exit 0; fi\nIFS= read -r line || true\ncase $line in BAD*) exit 1 ;; esac\nexit 0\n"
        self.write("g.sh", script)
        self.write("bad.txt", "BAD thing\n", executable=False)
        code, out, err = self.run_check(["g.sh", "bad.txt"], stdin="hello\n")
        self.assertEqual(err, "")
        self.assertEqual(code, 1)
        self.assertIn("SAID YES TO KNOWN-BAD INPUT: g.sh", out)

    def test_guard_does_not_read_the_checker_stdin(self):
        """source: a guard run by the checker reads /dev/null or the input file, never the checker's own standard input."""
        self.write("g.sh", "#!/bin/sh\nIFS= read -r line || true\nif [ -n \"$line\" ]; then exit 0; fi\nexit 1\n")
        self.write("bad.txt", "x\n", executable=False)
        code, out, err = self.run_check(["g.sh", "bad.txt"], stdin="hello\n")
        self.assertEqual(err, "")
        self.assertEqual(code, 0)
        self.assertIn("OK:", out)

    def test_refuse_code_match(self):
        """source: with --refuse-code, that exact status is a refusal and any other ordinary status is WRONG EXIT."""
        self.write("g.sh", "#!/bin/sh\nexit 3\n")
        self.write("bad.txt", "x\n", executable=False)
        code, out, err = self.run_check(["--refuse-code", "3", "g.sh", "bad.txt"])
        self.assertEqual(err, "")
        self.assertEqual(code, 0)
        self.assertIn("(exit 3)", out)
        code, out, err = self.run_check(["--refuse-code", "4", "g.sh", "bad.txt"])
        self.assertEqual(err, "")
        self.assertEqual(code, 1)
        self.assertIn("WRONG EXIT: g.sh exited 3 on the bad input, a refusal is 4", out)

    def test_could_not_run(self):
        """source: a missing interpreter is COULD NOT RUN, and the shell's own complaint stays off the checker's stderr."""
        self.write("g.sh", "#!/no/such/interpreter-xyz\nexit 1\n")
        self.write("bad.txt", "x\n", executable=False)
        code, out, err = self.run_check(["g.sh", "bad.txt"])
        self.assertEqual(err, "")
        self.assertEqual(code, 1)
        self.assertIn("COULD NOT RUN: g.sh (exit ", out)
        self.assertIn("it said:", out)
        self.assertNotIn("OK:", out)

    def test_died(self):
        """source: an exit above 128 is DIED, the guard ended by a signal, not a refusal."""
        self.write("g.sh", "#!/bin/sh\nexit 130\n")
        self.write("bad.txt", "x\n", executable=False)
        code, out, err = self.run_check(["g.sh", "bad.txt"])
        self.assertEqual(err, "")
        self.assertEqual(code, 1)
        self.assertIn("DIED: g.sh (exit 130)", out)

    def test_timeout(self):
        """source: a guard that does not answer within the timeout is TIMED OUT, and the checker does not wait for the guard to finish on its own."""
        self.write("g.sh", "#!/bin/sh\nsleep 4\nexit 1\n")
        self.write("bad.txt", "x\n", executable=False)
        started = time.time()
        code, out, err = self.run_check(["--timeout", "1", "g.sh", "bad.txt"], timeout=12)
        elapsed = time.time() - started
        self.assertEqual(err, "")
        self.assertEqual(code, 1)
        self.assertIn("TIMED OUT: g.sh gave no answer in 1s", out)
        self.assertLess(elapsed, 8)

    def test_answers_inside_the_timeout(self):
        """source: a guard that answers inside the timeout is judged on its exit status, not stopped early."""
        self.write("g.sh", "#!/bin/sh\nsleep 1\nexit 1\n")
        self.write("bad.txt", "x\n", executable=False)
        code, out, err = self.run_check(["--timeout", "5", "g.sh", "bad.txt"], timeout=12)
        self.assertEqual(err, "")
        self.assertEqual(code, 0)
        self.assertIn("OK:", out)
        self.assertNotIn("TIMED OUT:", out)

    def test_fast_guard_does_not_wait_out_the_default_timeout(self):
        """source: with the default timeout, a guard that answers at once returns through a pipe in under three seconds."""
        self.write("g.sh", "#!/bin/sh\nexit 1\n")
        self.write("bad.txt", "x\n", executable=False)
        started = time.time()
        code, out, err = self.run_check(["g.sh", "bad.txt"], timeout=5)
        self.assertLess(time.time() - started, 3)
        self.assertEqual(err, "")
        self.assertEqual(code, 0)
        self.assertIn("OK:", out)

    def test_background_child_does_not_hold_the_answer(self):
        """source: a guard that backgrounds a sleeper and then exits still lets the checker finish at once."""
        self.write("g.sh", "#!/bin/sh\nsleep 64 &\necho \"$!\" > kid.pid\nexit 1\n")
        self.write("bad.txt", "x\n", executable=False)
        kid_path = os.path.join(self.tmp, "kid.pid")

        def cleanup():
            kill_one(read_pid(kid_path))

        self.addCleanup(cleanup)
        started = time.time()
        code, out, err = self.run_check(["g.sh", "bad.txt"], timeout=5)
        self.assertLess(time.time() - started, 3)
        self.assertEqual(err, "")
        self.assertEqual(code, 0)

    def test_bash_shebang_parses_bash(self):
        """source: a first line that names bash is parsed with bash, so bash syntax is not rejected by the sh parser."""
        self.write("g.sh", "#!/bin/bash\n[[ -n x ]]\nexit 1\n")
        self.write("bad.txt", "x\n", executable=False)
        code, out, err = self.run_check(["g.sh", "bad.txt"])
        self.assertEqual(err, "")
        self.assertEqual(code, 0)
        self.assertIn("parses", out)
        self.assertNotIn("DOES NOT PARSE:", out)

    def test_shebang_wins_over_the_extension(self):
        """source: a first line that names a shell selects the parser, even when the file name has another extension."""
        self.write("notes.txt", "#!/bin/sh\necho \"\n", executable=True)
        self.write("bad.txt", "x\n", executable=False)
        code, out, err = self.run_check(["notes.txt", "bad.txt"])
        self.assertEqual(err, "")
        self.assertEqual(code, 1)
        self.assertIn("DOES NOT PARSE: notes.txt", out)

    def test_extension_selects_the_parser_without_a_shebang(self):
        """source: with no first-line interpreter, the extension selects the parser."""
        self.write("g.sh", "echo \"\n")
        self.write("bad.txt", "x\n", executable=False)
        code, out, err = self.run_check(["g.sh", "bad.txt"])
        self.assertEqual(err, "")
        self.assertEqual(code, 1)
        self.assertIn("DOES NOT PARSE: g.sh", out)

    def test_python_and_node_extensions(self):
        """source: .py is parsed as Python and .js, .mjs and .cjs are parsed as JavaScript, without running the guard."""
        record = os.path.join(self.tmp, "node.args")
        self.write_standin(
            "node",
            "#!/bin/sh\nprintf '%s\\n' \"$@\" > \""
            + record
            + "\"\nprintf '%s\\n' 'syntax error'\nexit 1\n",
        )
        self.write("g.py", "def (\n")
        self.write("g.js", "function ( {\n")
        self.write("g.mjs", "function ( {\n")
        self.write("g.cjs", "function ( {\n")
        self.write("bad.txt", "x\n", executable=False)
        env = self.prepend_bindir()
        for name in ("g.py", "g.js", "g.mjs", "g.cjs"):
            code, out, err = self.run_check([name, "bad.txt"], env=env)
            self.assertEqual(err, "", name)
            self.assertEqual(code, 1, out)
            self.assertIn("DOES NOT PARSE: " + name, out)
            if name == "g.py":
                continue
            self.assertTrue(os.path.isfile(record), name)
            with open(record) as handle:
                self.assertEqual(handle.read().splitlines(), ["--check", "./" + name])
        self.assertEqual(pyc_count(self.tmp), 0)

    def test_env_python_shebang(self):
        """source: a first line that uses env to name python is parsed as Python and writes no bytecode."""
        self.write("g.txt", "#!/usr/bin/env python3\ndef (\n")
        self.write("bad.txt", "x\n", executable=False)
        code, out, err = self.run_check(["g.txt", "bad.txt"])
        self.assertEqual(err, "")
        self.assertEqual(code, 1)
        self.assertIn("DOES NOT PARSE: g.txt", out)
        self.assertEqual(pyc_count(self.tmp), 0)

    def test_valid_python_writes_no_bytecode(self):
        """source: parsing a Python guard compiles it in memory and leaves no __pycache__ or .pyc beside it."""
        self.write("g.py", "import sys\nsys.exit(1)\n")
        self.write("bad.txt", "x\n", executable=False)
        code, out, err = self.run_check(["g.py", "bad.txt"])
        self.assertEqual(err, "")
        self.assertEqual(code, 0)
        self.assertIn("parses", out)
        self.assertEqual(pyc_count(self.tmp), 0)
        self.assertFalse(os.path.isdir(os.path.join(self.tmp, "__pycache__")))

    def test_unknown_file_skips_the_parse(self):
        """source: a file with no known parser skips the parse check and says so, instead of claiming that it parses."""
        self.write("notes.txt", "exit 1\n")
        self.write("bad.txt", "x\n", executable=False)
        code, out, err = self.run_check(["notes.txt", "bad.txt"])
        self.assertEqual(err, "")
        self.assertEqual(code, 0)
        self.assertIn("parse not checked (no parser for this file)", out)
        self.assertNotIn(" parses,", out)

    def test_missing_parser_skips_the_parse(self):
        """source: when the parser named on the first line is not installed, the parse check is skipped and the reason names that parser."""
        bindir = os.path.join(self.tmp, "bin")
        os.makedirs(bindir)
        for name in ("mktemp", "rm", "sleep"):
            found = shutil.which(name)
            self.assertTrue(found, name)
            os.symlink(found, os.path.join(bindir, name))
        self.write("g.sh", "#!/bin/bash\nexit 1\n")
        self.write("bad.txt", "x\n", executable=False)
        env = os.environ.copy()
        env["PATH"] = bindir
        code, out, err = self.run_check(["g.sh", "bad.txt"], env=env)
        self.assertEqual(err, "")
        self.assertEqual(code, 0)
        self.assertIn("parse not checked (bash is not installed)", out)

    def test_ksh_shebang_parses_with_ksh(self):
        """source: a first line that names ksh is parsed by running ksh with its check flag."""
        record = os.path.join(self.tmp, "ksh.args")
        self.write_standin(
            "ksh",
            "#!/bin/sh\n"
            "if [ \"$1\" = \"-n\" ]; then\n"
            "  printf '%s\\n' \"$@\" > \"" + record + "\"\n"
            "  exit 0\n"
            "fi\n"
            "exit 1\n",
        )
        self.write("g.sh", "#!/usr/bin/env ksh\nexit 1\n")
        self.write("bad.txt", "x\n", executable=False)
        code, out, err = self.run_check(["g.sh", "bad.txt"], env=self.prepend_bindir())
        self.assertEqual(err, "")
        self.assertEqual(code, 0, out)
        self.assertIn("parses", out)
        self.assertTrue(os.path.isfile(record))
        with open(record) as handle:
            self.assertEqual(handle.read().splitlines(), ["-n", "./g.sh"])

    def test_ksh_parse_failure_is_reported(self):
        """source: a ksh parser that rejects the file is shown as one indented line."""
        record = os.path.join(self.tmp, "ksh.args")
        self.write_standin(
            "ksh",
            "#!/bin/sh\n"
            "if [ \"$1\" = \"-n\" ]; then\n"
            "  printf '%s\\n' \"$@\" > \"" + record + "\"\n"
            "  printf '%s\\n' 'g.sh: syntax error at line 2: unmatched'\n"
            "  exit 3\n"
            "fi\n"
            "exit 1\n",
        )
        self.write("g.sh", "#!/usr/bin/env ksh\nexit 1\n")
        self.write("bad.txt", "x\n", executable=False)
        code, out, err = self.run_check(["g.sh", "bad.txt"], env=self.prepend_bindir())
        self.assertEqual(err, "")
        self.assertEqual(code, 1, out)
        lines = out.splitlines()
        self.assertGreaterEqual(len(lines), 2, out)
        self.assertEqual(lines[0], "DOES NOT PARSE: g.sh")
        self.assertEqual(lines[1], "  g.sh: syntax error at line 2: unmatched")

    def test_ksh_extension_when_ksh_is_missing(self):
        """source: a ksh file whose parser is not installed skips the parse and names ksh."""
        bindir = os.path.join(self.tmp, "bin")
        os.makedirs(bindir)
        for name in ("mktemp", "rm", "sleep"):
            found = shutil.which(name)
            self.assertTrue(found, name)
            os.symlink(found, os.path.join(bindir, name))
        self.write("g.ksh", "exit 1\n")
        self.write("bad.txt", "x\n", executable=False)
        env = os.environ.copy()
        env["PATH"] = bindir
        code, out, err = self.run_check(["g.ksh", "bad.txt"], env=env)
        self.assertEqual(err, "")
        self.assertEqual(code, 0, out)
        self.assertIn("parse not checked (ksh is not installed)", out)

    def test_ksh_extension_parses_with_ksh(self):
        """source: a ksh file with no first line is parsed by running ksh."""
        record = os.path.join(self.tmp, "ksh.args")
        self.write_standin(
            "ksh",
            "#!/bin/sh\n"
            "if [ \"$1\" = \"-n\" ]; then\n"
            "  printf '%s\\n' \"$@\" > \"" + record + "\"\n"
            "  exit 0\n"
            "fi\n"
            "exit 1\n",
        )
        self.write("g.ksh", "exit 1\n")
        self.write("bad.txt", "x\n", executable=False)
        code, out, err = self.run_check(["g.ksh", "bad.txt"], env=self.prepend_bindir())
        self.assertEqual(err, "")
        self.assertEqual(code, 0, out)
        self.assertIn("parses", out)
        self.assertTrue(os.path.isfile(record))
        with open(record) as handle:
            self.assertEqual(handle.read().splitlines(), ["-n", "./g.ksh"])

    def test_ksh93_shebang_uses_its_own_name(self):
        """source: a first line that names ksh93 is parsed by ksh93, not by ksh."""
        ksh_record = os.path.join(self.tmp, "ksh.args")
        ksh93_record = os.path.join(self.tmp, "ksh93.args")
        script = (
            "#!/bin/sh\n"
            "if [ \"$1\" = \"-n\" ]; then\n"
            "  printf '%%s\\n' \"$@\" > \"%s\"\n"
            "  exit 0\n"
            "fi\n"
            "exit 1\n"
        )
        self.write_standin("ksh", script % ksh_record)
        self.write_standin("ksh93", script % ksh93_record)
        self.write("g.sh", "#!/usr/bin/env ksh93\nexit 1\n")
        self.write("bad.txt", "x\n", executable=False)
        code, out, err = self.run_check(["g.sh", "bad.txt"], env=self.prepend_bindir())
        self.assertEqual(err, "")
        self.assertEqual(code, 0, out)
        self.assertIn("parses", out)
        self.assertFalse(os.path.exists(ksh_record))
        self.assertTrue(os.path.isfile(ksh93_record))
        with open(ksh93_record) as handle:
            self.assertEqual(handle.read().splitlines(), ["-n", "./g.sh"])

    def test_python2_shebang_is_not_compiled(self):
        """source: a first line that names python2 is not compiled, so an old print is not a parse failure."""
        self.write_standin("python2", "#!/bin/sh\nexit 1\n")
        self.write("g.py", "#!/usr/bin/env python2\nprint \"refused\"\n")
        self.write("bad.txt", "x\n", executable=False)
        code, out, err = self.run_check(["g.py", "bad.txt"], env=self.prepend_bindir())
        self.assertEqual(err, "")
        self.assertEqual(code, 0, out)
        self.assertIn("parse not checked (no parser for this file)", out)
        self.assertNotIn("DOES NOT PARSE:", out)

    def test_inline_python_program_compiles_as_written(self):
        """source: the Python parse program is valid as written, so a leading indent is rejected by the compiler."""
        record = os.path.join(self.tmp, "python3.program")
        self.write_standin(
            "python3",
            "#!/bin/sh\n"
            "if [ \"$1\" = \"-c\" ]; then\n"
            "  printf '%s\\n' \"$2\" > \"" + record + "\"\n"
            "  exit 0\n"
            "fi\n"
            "exit 1\n",
        )
        self.write("g.py", "#!/usr/bin/env python3\nimport sys\nsys.exit(1)\n")
        self.write("bad.txt", "x\n", executable=False)
        code, out, err = self.run_check(["g.py", "bad.txt"], env=self.prepend_bindir())
        self.assertEqual(err, "")
        self.assertEqual(code, 0, out)
        self.assertIn("OK:", out)
        with open(record) as handle:
            program = handle.read()
        try:
            compile(program, "inline", "exec")
        except SyntaxError as err:
            self.fail("inline program does not compile: %s" % err)

    def test_zsh_shebang_parses_zsh(self):
        """source: a first line that names zsh is parsed with zsh, so zsh-only syntax is not rejected by sh."""
        self.write("g.sh", "#!/bin/zsh\nfor x (a b) true\nexit 1\n")
        self.write("bad.txt", "x\n", executable=False)
        code, out, err = self.run_check(["g.sh", "bad.txt"])
        self.assertEqual(err, "")
        self.assertEqual(code, 0)
        self.assertIn("parses", out)
        self.assertNotIn("DOES NOT PARSE:", out)

    def test_bash_extension_parses_bash(self):
        """source: a .bash file with no first-line interpreter is parsed with bash."""
        self.write("g.bash", "[[ -n x ]]\nexit 1\n")
        self.write("bad.txt", "x\n", executable=False)
        code, out, err = self.run_check(["g.bash", "bad.txt"])
        self.assertEqual(err, "")
        self.assertEqual(code, 0)
        self.assertIn("parses", out)

    def test_node_shebang_parses_node(self):
        """source: a first line that names node is parsed with node, and a syntax error is DOES NOT PARSE."""
        record = os.path.join(self.tmp, "node.args")
        self.write_standin(
            "node",
            "#!/bin/sh\nprintf '%s\\n' \"$@\" > \""
            + record
            + "\"\nprintf '%s\\n' 'syntax error'\nexit 1\n",
        )
        self.write("g.txt", "#!/usr/bin/env node\nfunction ( {\n")
        self.write("bad.txt", "x\n", executable=False)
        code, out, err = self.run_check(["g.txt", "bad.txt"], env=self.prepend_bindir())
        self.assertEqual(err, "")
        self.assertEqual(code, 1)
        self.assertIn("DOES NOT PARSE: g.txt", out)
        self.assertTrue(os.path.isfile(record))
        with open(record) as handle:
            self.assertEqual(handle.read().splitlines(), ["--check", "./g.txt"])

    def test_usage_errors(self):
        """source: a bad invocation exits 2 with one usage line on stderr and nothing on stdout."""
        cases = [
            [],
            ["only-one"],
            ["a", "b", "c"],
            ["--unknown", "a", "b"],
            ["--timeout", "0", "a", "b"],
            ["--timeout", "nope", "a", "b"],
            ["--refuse-code", "0", "a", "b"],
            ["--refuse-code", "256", "a", "b"],
            ["--refuse-code", "x", "a", "b"],
            ["--list"],
            ["--stdin", "--list", "file"],
        ]
        for args in cases:
            code, out, err = self.run_check(args)
            self.assertEqual(out, "", args)
            self.assertEqual(code, 2, args)
            self.assertTrue(err.startswith("usage:"), err)
            self.assertEqual(err.count("\n"), 1)

    def test_good_input_failure_is_not_a_timeout_when_the_guard_answers(self):
        """source: a non-zero exit on the known-good input is SAID NO, with that exit code."""
        self.write("g.sh", "#!/bin/sh\nexit 1\n")
        self.write("bad.txt", "x\n", executable=False)
        self.write("good.txt", "y\n", executable=False)
        code, out, err = self.run_check(["--good", "good.txt", "g.sh", "bad.txt"])
        self.assertEqual(err, "")
        self.assertEqual(code, 1)
        self.assertIn("SAID NO TO KNOWN-GOOD INPUT: g.sh (exit 1)", out)

    def test_cut_off_ordinary_exit_is_a_timeout(self):
        """source: a guard that exits with an ordinary status after the time limit is a timeout, not a refusal."""
        self.write(
            "g.sh",
            "#!/bin/sh\ntrap 'exit 1' TERM\necho \"$$\" > g.pid\nsleep 61 &\n"
            "echo \"$!\" > kid.pid\nwait \"$!\"\n",
        )
        self.write("bad.txt", "x\n", executable=False)
        proc, box = launch(
            self,
            ["--timeout", "1", "g.sh", "bad.txt"],
            self.tmp,
            pidfiles=[os.path.join(self.tmp, "g.pid"), os.path.join(self.tmp, "kid.pid")],
        )
        started = time.time()
        code, out, err = finish(self, proc, 8)
        self.assertLess(time.time() - started, 6)
        self.assertEqual(err, "")
        self.assertEqual(code, 1)
        self.assertTrue(out.startswith("TIMED OUT:"), out)
        del box

    def test_time_limit_stops_the_guard_and_its_child(self):
        """source: the time limit stops the guard and a child it is waiting on."""
        self.write("g.sh", GUARD_CHILD)
        self.write("bad.txt", "x\n", executable=False)
        proc, box = launch(
            self,
            ["--timeout", "1", "g.sh", "bad.txt"],
            self.tmp,
            pidfiles=[os.path.join(self.tmp, "g.pid"), os.path.join(self.tmp, "kid.pid")],
        )
        started = time.time()
        code, out, err = finish(self, proc, 8)
        self.assertLess(time.time() - started, 6)
        self.assertEqual(err, "")
        self.assertEqual(code, 1)
        self.assertTrue(out.startswith("TIMED OUT:"), out)
        gpid = read_pid(os.path.join(self.tmp, "g.pid"))
        kpid = read_pid(os.path.join(self.tmp, "kid.pid"))
        self.assertIsNotNone(gpid)
        self.assertIsNotNone(kpid)
        assert_gone(self, gpid)
        assert_gone(self, kpid)
        del box

    def test_time_limit_kills_a_guard_that_ignores_term(self):
        """source: a guard that ignores the terminate signal, and a child that ignores it too, are still stopped at the limit."""
        self.write(
            "g.sh",
            "#!/bin/sh\ntrap '' TERM\necho \"$$\" > g.pid\n"
            "sh -c 'trap \"\" TERM; exec sleep 63' &\n"
            "echo \"$!\" > kid.pid\nwait \"$!\"\n",
        )
        self.write("bad.txt", "x\n", executable=False)
        proc, box = launch(
            self,
            ["--timeout", "1", "g.sh", "bad.txt"],
            self.tmp,
            pidfiles=[os.path.join(self.tmp, "g.pid"), os.path.join(self.tmp, "kid.pid")],
        )
        started = time.time()
        code, out, err = finish(self, proc, 8)
        self.assertLess(time.time() - started, 6)
        self.assertEqual(err, "")
        self.assertEqual(code, 1)
        self.assertTrue(out.startswith("TIMED OUT:"), out)
        gpid = read_pid(os.path.join(self.tmp, "g.pid"))
        kpid = read_pid(os.path.join(self.tmp, "kid.pid"))
        self.assertIsNotNone(gpid)
        self.assertIsNotNone(kpid)
        assert_gone(self, gpid)
        assert_gone(self, kpid)
        del box

    def test_terminate_stops_the_run(self):
        """source: a terminate signal stops the guard, the processes under it, and the checker's timer, and the checker prints nothing."""
        assert_signal_stop(self, ["--timeout", "69", "g.sh", "bad.txt"], signal.SIGTERM, 143)

    def test_hangup_stops_the_run(self):
        """source: a hang-up signal stops the guard, the processes under it, and the checker's timer, and the checker prints nothing."""
        assert_signal_stop(self, ["--timeout", "69", "g.sh", "bad.txt"], signal.SIGHUP, 129)

    def test_quick_answer_leaves_no_timer(self):
        """source: a guard that answers at once does not leave the checker's timer running."""
        self.write("g.sh", "#!/bin/sh\nexit 1\n")
        self.write("bad.txt", "x\n", executable=False)
        before = exact_commands("sleep 68")
        proc, box = launch(self, ["--timeout", "68", "g.sh", "bad.txt"], self.tmp)
        box["before"] = before
        started = time.time()
        code, out, err = finish(self, proc, 8, box, "sleep 68")
        self.assertLess(time.time() - started, 3)
        self.assertEqual(err, "")
        self.assertEqual(code, 0)
        self.assertIn("OK:", out)
        deadline = started + 3
        new = exact_commands("sleep 68") - before
        while new and time.time() < deadline:
            time.sleep(0.05)
            new = exact_commands("sleep 68") - before
        box["pids"].extend(new)
        self.assertEqual(new, set())

    def test_python_syntax_error_is_one_line(self):
        """source: a Python syntax error is one plain line that names the line, with no traceback."""
        self.write("g.py", "#!/usr/bin/env python3\nx = 1\ndef broken(:\n")
        self.write("bad.txt", "x\n", executable=False)
        code, out, err = self.run_check(["g.py", "bad.txt"])
        self.assertEqual(err, "")
        self.assertEqual(code, 1)
        lines = out.splitlines()
        self.assertTrue(lines[0].startswith("DOES NOT PARSE:"), out)
        self.assertEqual(len(lines), 2, out)
        self.assertIn("line 3", lines[1])
        self.assertNotIn("Traceback", out)
        self.assertNotIn("compile(", out)

    def test_time_limit_works_when_ps_lists_nothing(self):
        """source: the time limit still stops the guard when the process list is empty."""
        bindir = os.path.join(self.tmp, "bin")
        os.makedirs(bindir)
        ps_path = os.path.join(bindir, "ps")
        with open(ps_path, "w") as handle:
            handle.write("#!/bin/sh\nexit 1\n")
        os.chmod(ps_path, 0o755)
        self.write("g.sh", "#!/bin/sh\necho \"$$\" > g.pid\nexec sleep 66\n")
        self.write("bad.txt", "x\n", executable=False)
        env = os.environ.copy()
        env["PATH"] = bindir + os.pathsep + env.get("PATH", "")
        gpath = os.path.join(self.tmp, "g.pid")
        proc, box = launch(
            self,
            ["--timeout", "1", "g.sh", "bad.txt"],
            self.tmp,
            env=env,
            pidfiles=[gpath],
        )
        started = time.time()
        code, out, err = finish(self, proc, 8)
        self.assertLess(time.time() - started, 6)
        self.assertEqual(err, "")
        self.assertEqual(code, 1)
        self.assertTrue(out.startswith("TIMED OUT:"), out)
        gpid = read_pid(gpath)
        self.assertIsNotNone(gpid)
        assert_gone(self, gpid)
        del box

    def test_latin1_python_guard_parses(self):
        """source: a Python guard stored with a declared byte encoding still parses."""
        path = os.path.join(self.tmp, "g.py")
        payload = (
            b"#!/usr/bin/env python3\n"
            b"# -*- coding: latin-1 -*-\n"
            b"import sys\n"
            b"name = \"caf\xe9\"\n"
            b"sys.exit(1)\n"
        )
        with open(path, "wb") as handle:
            handle.write(payload)
        os.chmod(path, 0o755)
        self.write("bad.txt", "x\n", executable=False)
        code, out, err = self.run_check(["g.py", "bad.txt"])
        self.assertEqual(err, "")
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("OK:"), out)


def tempfile_dir():
    import tempfile
    return tempfile.mkdtemp(prefix="guard-can-start-")


GUARD_CHILD = "#!/bin/sh\necho \"$$\" > g.pid\nsleep 62 &\necho \"$!\" > kid.pid\nwait \"$!\"\n"


def kill_one(pid):
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return
    if pid <= 1:
        return
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        return


def read_pid(path):
    try:
        with open(path) as handle:
            text = handle.read().strip()
    except OSError:
        return None
    if text.isdigit() and int(text) > 1:
        return int(text)
    return None


def ps_rows():
    proc = subprocess.run(
        ["ps", "-A", "-o", "pid=", "-o", "ppid=", "-o", "command="],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
    )
    rows = []
    for line in proc.stdout.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 2 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        cmd = parts[2].strip() if len(parts) > 2 else ""
        rows.append((int(parts[0]), int(parts[1]), cmd))
    return rows


def descendant_pids(root):
    kids = {}
    for pid, ppid, cmd in ps_rows():
        kids.setdefault(ppid, []).append(pid)
        del cmd
    found = []
    stack = list(kids.get(root, []))
    seen = set()
    while stack:
        pid = stack.pop()
        if pid in seen or pid <= 1 or pid == os.getpid():
            continue
        seen.add(pid)
        found.append(pid)
        stack.extend(kids.get(pid, []))
    return found


def exact_commands(command):
    return {pid for pid, ppid, cmd in ps_rows() if cmd == command}


def assert_gone(test, pid):
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    test.fail("pid %s still running" % pid)


def stop_checked(proc):
    if proc.poll() is None:
        kids = descendant_pids(proc.pid)
        for pid in kids:
            kill_one(pid)
        kill_one(proc.pid)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        kill_one(proc.pid)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            return


def launch(test, args, cwd, env=None, pidfiles=None):
    box = {"proc": None, "pids": [], "pidfiles": list(pidfiles or []), "before": set()}

    def cleanup():
        for path in box["pidfiles"]:
            kill_one(read_pid(path))
        for pid in list(box["pids"]):
            kill_one(pid)
        proc = box["proc"]
        if proc is not None:
            stop_checked(proc)

    test.addCleanup(cleanup)
    proc = subprocess.Popen(
        [SHELL, SCRIPT] + list(args),
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        env=env,
    )
    box["proc"] = proc
    return proc, box


def finish(test, proc, timeout, box=None, watch=None):
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        if box is not None and watch:
            box["pids"].extend(exact_commands(watch) - box.get("before", set()))
        test.fail("checker still running after %ss" % timeout)
    return proc.returncode, out, err


def wait_pid(test, path, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        pid = read_pid(path)
        if pid:
            return pid
        time.sleep(0.05)
    test.fail("pid file was not written")


def assert_signal_stop(test, args, sig, status):
    test.write("g.sh", GUARD_CHILD)
    test.write("bad.txt", "x\n", executable=False)
    checker_tmp = os.path.join(test.tmp, "checker-tmp")
    os.makedirs(checker_tmp)
    env = os.environ.copy()
    env["TMPDIR"] = checker_tmp
    before = exact_commands("sleep 69")
    pidfiles = [os.path.join(test.tmp, "g.pid"), os.path.join(test.tmp, "kid.pid")]
    proc, box = launch(test, args, test.tmp, env, pidfiles)
    box["before"] = before
    kid = wait_pid(test, os.path.join(test.tmp, "kid.pid"), 10)
    box["pids"].extend(descendant_pids(proc.pid))
    try:
        os.kill(proc.pid, sig)
    except ProcessLookupError:
        test.fail("checker exited before the signal")
    code, out, err = finish(test, proc, 10, box, "sleep 69")
    test.assertEqual(code, status)
    test.assertEqual(out, "")
    test.assertEqual(err, "")
    gpid = read_pid(os.path.join(test.tmp, "g.pid"))
    test.assertIsNotNone(gpid)
    assert_gone(test, gpid)
    assert_gone(test, kid)
    test.assertEqual(os.listdir(checker_tmp), [])
    new = exact_commands("sleep 69") - before
    box["pids"].extend(new)
    test.assertEqual(new, set())


def pyc_count(root):
    found = 0
    for dirpath, dirnames, filenames in os.walk(root):
        if os.path.basename(dirpath) == "__pycache__":
            found += 1
        for name in filenames:
            if name.endswith(".pyc"):
                found += 1
        # Keep walking. dirnames is unused on purpose.
        del dirnames
    return found


if __name__ == "__main__":
    unittest.main()
