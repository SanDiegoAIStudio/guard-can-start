"""Many guards: the list counts real entries, and a run that checked nothing is not a pass."""

import os
import shutil
import signal
import subprocess
import time
import unittest

from test_check_guard import (
    assert_gone,
    descendant_pids,
    exact_commands,
    finish,
    launch,
    read_pid,
    wait_pid,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "check-guard.sh")
SHELL = os.environ.get("CHECK_GUARD_SHELL", "/bin/sh")


class ListTest(unittest.TestCase):
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

    def run_check(self, args, stdin=None, timeout=20, cwd=None, env=None):
        proc = subprocess.run(
            [SHELL, SCRIPT] + list(args),
            cwd=cwd or self.tmp,
            input=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=timeout,
            env=env,
        )
        return proc.returncode, proc.stdout, proc.stderr

    def test_all_ok_exits_zero_with_the_count_line(self):
        """source: a list whose every guard refuses exits 0 and the count line names how many were ok."""
        self.write("g.sh", "#!/bin/sh\nexit 1\n")
        self.write("bad.txt", "x\n", executable=False)
        path = self.write("guards.list", "g.sh bad.txt\ng.sh bad.txt\n", executable=False)
        code, out, err = self.run_check(["--list", path])
        self.assertEqual(err, "")
        self.assertEqual(code, 0)
        self.assertIn("checked 2 guards: 2 ok, 0 failed", out)
        self.assertEqual(out.strip().splitlines()[-1], "checked 2 guards: 2 ok, 0 failed")

    def test_a_failed_guard_exits_one(self):
        """source: one failed guard makes the list exit 1, and that guard is counted in both the total and the failed number."""
        self.write("g.sh", "#!/bin/sh\nexit 1\n")
        self.write("bad.txt", "x\n", executable=False)
        path = self.write(
            "guards.list",
            "g.sh bad.txt\nmissing.sh bad.txt\n",
            executable=False,
        )
        code, out, err = self.run_check(["--list", path])
        self.assertEqual(err, "")
        self.assertEqual(code, 1)
        self.assertIn("MISSING: missing.sh", out)
        self.assertEqual(out.strip().splitlines()[-1], "checked 2 guards: 1 ok, 1 failed")

    def test_bad_line_counts_as_failed(self):
        """source: a list line that is not a valid check prints BAD LINE with its line number and counts as failed."""
        self.write("g.sh", "#!/bin/sh\nexit 1\n")
        self.write("bad.txt", "x\n", executable=False)
        path = self.write(
            "guards.list",
            "# note\n--nope one two\ng.sh bad.txt\n",
            executable=False,
        )
        code, out, err = self.run_check(["--list", path])
        self.assertEqual(err, "")
        self.assertEqual(code, 1)
        self.assertIn("BAD LINE 2: --nope one two", out)
        self.assertEqual(out.strip().splitlines()[-1], "checked 2 guards: 1 ok, 1 failed")

    def test_empty_list_checks_nothing(self):
        """source: a list with no entry exits 2 and says it checked nothing, which is never a pass."""
        path = self.write("guards.list", "", executable=False)
        code, out, err = self.run_check(["--list", path])
        self.assertEqual(err, "")
        self.assertEqual(code, 2)
        self.assertTrue(out.startswith("checked nothing:"), out)
        self.assertIn("holds no entry", out)
        self.assertEqual(out.count("\n"), 1)

    def test_comments_and_blank_lines_are_not_entries(self):
        """source: comment lines, blank lines, and lines with no fields are skipped, so they are not guards and not failures."""
        self.write("g.sh", "#!/bin/sh\nexit 1\n")
        self.write("bad.txt", "x\n", executable=False)
        path = self.write(
            "guards.list",
            "# only a note\n\n   \n\tg.sh\tbad.txt\n",
            executable=False,
        )
        code, out, err = self.run_check(["--list", path])
        self.assertEqual(err, "")
        self.assertEqual(code, 0)
        self.assertNotIn("BAD LINE", out)
        self.assertEqual(out.strip().splitlines()[-1], "checked 1 guards: 1 ok, 0 failed")

    def test_spaces_only_file_checks_nothing(self):
        """source: a file whose lines have no fields holds no entry, so the run exits 2."""
        path = self.write("guards.list", " \n\t\n", executable=False)
        code, out, err = self.run_check(["--list", path])
        self.assertEqual(err, "")
        self.assertEqual(code, 2)
        self.assertIn("holds no entry", out)

    def test_unreadable_list_checks_nothing(self):
        """source: a list file that cannot be read exits 2 with a checked-nothing line and does not look like a pass."""
        code, out, err = self.run_check(["--list", "no-such.list"])
        self.assertEqual(err, "")
        self.assertEqual(code, 2)
        self.assertEqual(out, "checked nothing: cannot read no-such.list\n")

    def test_paths_are_relative_to_the_list_directory(self):
        """source: paths on a list line are relative to the folder the list file is in, not the caller's current folder."""
        self.write("sub/g.sh", "#!/bin/sh\nexit 1\n")
        self.write("sub/bad.txt", "x\n", executable=False)
        self.write("sub/guards.list", "g.sh bad.txt\n", executable=False)
        parent = os.path.dirname(self.tmp)
        code, out, err = self.run_check(
            ["--list", os.path.join(self.tmp, "sub", "guards.list")],
            cwd=parent,
        )
        self.assertEqual(err, "")
        self.assertEqual(code, 0)
        self.assertIn("OK: g.sh", out)

    def test_list_lines_do_not_expand_wildcards(self):
        """source: a list line is split on spaces only, so a wildcard is a literal path and is not expanded."""
        self.write("only.sh", "#!/bin/sh\nexit 1\n")
        self.write("bad.txt", "x\n", executable=False)
        path = self.write("guards.list", "*.sh bad.txt\n", executable=False)
        code, out, err = self.run_check(["--list", path])
        self.assertEqual(err, "")
        self.assertEqual(code, 1)
        self.assertIn("MISSING: *.sh", out)
        self.assertNotIn("OK:", out)

    def test_list_does_not_feed_the_checker_stdin_to_a_guard(self):
        """source: in the list form a guard still does not read the checker's own standard input."""
        self.write(
            "g.sh",
            "#!/bin/sh\nIFS= read -r line || true\nif [ -n \"$line\" ]; then exit 0; fi\nexit 1\n",
        )
        self.write("bad.txt", "x\n", executable=False)
        path = self.write("guards.list", "g.sh bad.txt\n", executable=False)
        code, out, err = self.run_check(["--list", path], stdin="hello\n")
        self.assertEqual(err, "")
        self.assertEqual(code, 0)
        self.assertIn("OK:", out)

    def test_list_timeout_is_the_default_and_a_line_can_override_it(self):
        """source: --timeout beside --list is the default for a line that sets none, and a line can set its own."""
        self.write("slow.sh", "#!/bin/sh\nsleep 4\nexit 1\n")
        self.write("quick.sh", "#!/bin/sh\nsleep 1\nexit 1\n")
        self.write("bad.txt", "x\n", executable=False)
        path = self.write(
            "guards.list",
            "slow.sh bad.txt\n--timeout 5 quick.sh bad.txt\n",
            executable=False,
        )
        started = time.time()
        code, out, err = self.run_check(["--list", path, "--timeout", "1"], timeout=20)
        elapsed = time.time() - started
        self.assertEqual(err, "")
        self.assertEqual(code, 1)
        self.assertIn("TIMED OUT: slow.sh gave no answer in 1s", out)
        self.assertIn("OK: quick.sh", out)
        self.assertEqual(out.strip().splitlines()[-1], "checked 2 guards: 1 ok, 1 failed")
        self.assertLess(elapsed, 12)

    def test_options_after_the_guard_are_not_options(self):
        """source: options on a list line are read only before the guard and the bad input."""
        self.write("g.sh", "#!/bin/sh\nexit 1\n")
        self.write("bad.txt", "x\n", executable=False)
        path = self.write("guards.list", "g.sh --stdin bad.txt\n", executable=False)
        code, out, err = self.run_check(["--list", path])
        self.assertEqual(err, "")
        self.assertEqual(code, 1)
        self.assertIn("BAD LINE 1: g.sh --stdin bad.txt", out)

    def test_terminate_stops_a_list(self):
        """source: a terminate signal during a list stops that guard and leaves nothing running."""
        self.write(
            "g.sh",
            "#!/bin/sh\necho \"$$\" > g.pid\nsleep 62 &\necho \"$!\" > kid.pid\nwait \"$!\"\n",
        )
        self.write("bad.txt", "x\n", executable=False)
        self.write("guards.list", "g.sh bad.txt\n", executable=False)
        checker_tmp = os.path.join(self.tmp, "checker-tmp")
        os.makedirs(checker_tmp)
        env = os.environ.copy()
        env["TMPDIR"] = checker_tmp
        before = exact_commands("sleep 69")
        pidfiles = [os.path.join(self.tmp, "g.pid"), os.path.join(self.tmp, "kid.pid")]
        proc, box = launch(
            self,
            ["--timeout", "69", "--list", "guards.list"],
            self.tmp,
            env,
            pidfiles,
        )
        box["before"] = before
        kid = wait_pid(self, os.path.join(self.tmp, "kid.pid"), 10)
        box["pids"].extend(descendant_pids(proc.pid))
        try:
            os.kill(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            self.fail("checker exited before the signal")
        code, out, err = finish(self, proc, 10, box, "sleep 69")
        self.assertEqual(code, 143)
        self.assertEqual(out, "")
        self.assertEqual(err, "")
        gpid = read_pid(os.path.join(self.tmp, "g.pid"))
        self.assertIsNotNone(gpid)
        assert_gone(self, gpid)
        assert_gone(self, kid)
        self.assertEqual(os.listdir(checker_tmp), [])
        new = exact_commands("sleep 69") - before
        box["pids"].extend(new)
        self.assertEqual(new, set())

    def test_directory_search_path_is_not_used(self):
        """source: a relative list is read from the working folder when a directory search path names another folder."""
        self.write("examples/g.sh", "#!/bin/sh\nexit 1\n")
        self.write("examples/bad.txt", "x\n", executable=False)
        self.write("examples/guards.list", "g.sh bad.txt\n", executable=False)
        other = tempfile_dir()
        self.addCleanup(shutil.rmtree, other)
        os.makedirs(os.path.join(other, "examples"))
        env = os.environ.copy()
        env["CDPATH"] = other
        code, out, err = self.run_check(["--list", "examples/guards.list"], env=env)
        self.assertEqual(err, "")
        self.assertEqual(code, 0, out)
        self.assertIn("OK:", out)
        self.assertNotIn("MISSING", out)
        self.assertNotIn(other, out)
        self.assertEqual(
            [line for line in out.splitlines() if line.startswith("/")],
            [],
        )

    def write_broken(self, name):
        self.write(name, "#!/bin/sh\nif true; then\n")

    def test_broken_first_of_two_still_checks_the_second(self):
        """source: a guard that does not parse does not stop the list, and the count line names every entry."""
        self.write_broken("broken.sh")
        self.write("good.sh", "#!/bin/sh\nexit 1\n")
        self.write("bad.txt", "x\n", executable=False)
        path = self.write(
            "guards.list",
            "broken.sh bad.txt\ngood.sh bad.txt\n",
            executable=False,
        )
        code, out, err = self.run_check(["--list", path])
        self.assertEqual(err, "")
        self.assertEqual(code, 1)
        self.assertIn("DOES NOT PARSE: broken.sh", out)
        self.assertIn("OK: good.sh", out)
        self.assertEqual(out.strip().splitlines()[-1], "checked 2 guards: 1 ok, 1 failed")

    def test_broken_second_of_four_counts_every_entry(self):
        """source: a guard that does not parse in the middle of a list leaves the later entries checked."""
        self.write("good.sh", "#!/bin/sh\nexit 1\n")
        self.write_broken("broken.sh")
        self.write("bad.txt", "x\n", executable=False)
        path = self.write(
            "guards.list",
            "good.sh bad.txt\nbroken.sh bad.txt\ngood.sh bad.txt\ngood.sh bad.txt\n",
            executable=False,
        )
        code, out, err = self.run_check(["--list", path])
        self.assertEqual(err, "")
        self.assertEqual(code, 1)
        ok_lines = [line for line in out.splitlines() if line.startswith("OK:")]
        self.assertEqual(len(ok_lines), 3)
        self.assertEqual(out.strip().splitlines()[-1], "checked 4 guards: 3 ok, 1 failed")

    def test_broken_last_of_two_counts_both(self):
        """source: a guard that does not parse at the end of a list is still one entry in the count."""
        self.write("good.sh", "#!/bin/sh\nexit 1\n")
        self.write_broken("broken.sh")
        self.write("bad.txt", "x\n", executable=False)
        path = self.write(
            "guards.list",
            "good.sh bad.txt\nbroken.sh bad.txt\n",
            executable=False,
        )
        code, out, err = self.run_check(["--list", path])
        self.assertEqual(err, "")
        self.assertEqual(code, 1)
        self.assertEqual(out.strip().splitlines()[-1], "checked 2 guards: 1 ok, 1 failed")

    def test_two_broken_and_one_good_are_all_counted(self):
        """source: every guard that does not parse is a failed entry, and a later good guard is still checked."""
        self.write_broken("broken-a.sh")
        self.write_broken("broken-b.sh")
        self.write("good.sh", "#!/bin/sh\nexit 1\n")
        self.write("bad.txt", "x\n", executable=False)
        path = self.write(
            "guards.list",
            "broken-a.sh bad.txt\ngood.sh bad.txt\nbroken-b.sh bad.txt\n",
            executable=False,
        )
        code, out, err = self.run_check(["--list", path])
        self.assertEqual(err, "")
        self.assertEqual(code, 1)
        self.assertEqual(out.strip().splitlines()[-1], "checked 3 guards: 1 ok, 2 failed")

    def parser_on_path(self, name, text):
        self.write("bin/" + name, text)
        env = os.environ.copy()
        env["PATH"] = os.path.join(self.tmp, "bin") + os.pathsep + env.get("PATH", "")
        return env

    def test_silent_parser_failure_does_not_end_the_list(self):
        """source: a parser that prints nothing does not end the list or zero the entry count."""
        env = self.parser_on_path("zsh", "#!/bin/sh\nexit 1\n")
        self.write("first.sh", "#!/bin/zsh\nexit 1\n")
        self.write("second.sh", "#!/bin/sh\nexit 1\n")
        self.write("bad.txt", "x\n", executable=False)
        path = self.write(
            "guards.list",
            "first.sh bad.txt\nsecond.sh bad.txt\n",
            executable=False,
        )
        code, out, err = self.run_check(["--list", path], env=env)
        self.assertEqual(err, "")
        self.assertEqual(code, 1)
        lines = out.splitlines()
        index = lines.index("DOES NOT PARSE: first.sh")
        self.assertTrue(lines[index + 1].startswith("OK: second.sh"), out)
        self.assertEqual(lines[-1], "checked 2 guards: 1 ok, 1 failed")

    def test_long_parser_message_stays_capped_and_the_list_continues(self):
        """source: a long parser message stays inside the cap while the list continues to the next guard."""
        env = self.parser_on_path(
            "zsh",
            "#!/bin/sh\nprintf '%s\\n' 1 2 3 4 5 6 7\nexit 1\n",
        )
        self.write("first.sh", "#!/bin/zsh\nexit 1\n")
        self.write("second.sh", "#!/bin/sh\nexit 1\n")
        self.write("bad.txt", "x\n", executable=False)
        path = self.write(
            "guards.list",
            "first.sh bad.txt\nsecond.sh bad.txt\n",
            executable=False,
        )
        code, out, err = self.run_check(["--list", path], env=env)
        self.assertEqual(err, "")
        self.assertEqual(code, 1, out)
        lines = out.splitlines()
        self.assertEqual(lines[0], "DOES NOT PARSE: first.sh")
        self.assertEqual(lines[1:6], ["  1", "  2", "  3", "  4", "  5"])
        self.assertTrue(lines[6].startswith("OK: second.sh"), out)
        self.assertNotIn("  6", lines)
        self.assertNotIn("  7", lines)
        self.assertEqual(lines[-1], "checked 2 guards: 1 ok, 1 failed")

    def test_one_entry_of_every_failing_kind(self):
        """source: one list counts a missing file, a parse failure, a missing execute bit, a bad line, a yes, and the guards that refuse."""
        self.write("good.sh", "#!/bin/sh\nexit 1\n")
        self.write_broken("broken.sh")
        self.write("noexec.sh", "#!/bin/sh\nexit 1\n", executable=False)
        self.write("yes.sh", "#!/bin/sh\nexit 0\n")
        self.write("bad.txt", "x\n", executable=False)
        path = self.write(
            "guards.list",
            "\n".join(
                [
                    "good.sh bad.txt",
                    "missing.sh bad.txt",
                    "broken.sh bad.txt",
                    "noexec.sh bad.txt",
                    "good.sh",
                    "yes.sh bad.txt",
                    "good.sh bad.txt",
                    "",
                ]
            ),
            executable=False,
        )
        code, out, err = self.run_check(["--list", path])
        self.assertEqual(err, "")
        self.assertEqual(code, 1, out)
        lines = out.splitlines()

        def starting(prefix):
            return [line for line in lines if line.startswith(prefix)]

        self.assertEqual(len(starting("MISSING:")), 1)
        self.assertEqual(len(starting("DOES NOT PARSE:")), 1)
        self.assertEqual(len(starting("NOT EXECUTABLE:")), 1)
        self.assertEqual(len(starting("BAD LINE 5:")), 1)
        self.assertEqual(len(starting("SAID YES TO KNOWN-BAD INPUT:")), 1)
        self.assertEqual(len(starting("OK:")), 2)
        self.assertEqual(lines[-1], "checked 7 guards: 2 ok, 5 failed")


def tempfile_dir():
    import tempfile
    return tempfile.mkdtemp(prefix="guard-can-start-")


if __name__ == "__main__":
    unittest.main()
