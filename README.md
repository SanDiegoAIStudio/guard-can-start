# guard-can-start

A guard is a script whose job is to stop a bad thing, and a guard that prints nothing has not told you whether it ran. `check-guard.sh` asks the question a quiet result skips: is the file there, does it parse, can it start, and does it still say no to an input known to be bad.

## What you get

- `check-guard.sh` checks one guard, or every guard named in a list.
- `examples/no-secrets.sh` is a small guard. It reads a file, or standard input when it has no argument, and refuses a line that starts with `SECRET=`. It also refuses when it cannot read its input.
- `examples/fixtures/bad.txt` and `examples/fixtures/good.txt` are made-up inputs for that guard.
- `examples/guards.list` names the same guard twice, once by argument and once by standard input.
- `examples/demo.sh` runs the working guard, then the same guard broken six ways, then two lists.
- `tests/` drives the checker through temporary files.

## Quick start

From this folder:

```
sh examples/demo.sh
```

The demo copies the checker and the examples into a temporary folder, removes that folder when it exits, and prints four parts. This is a run from this folder on macOS. The indented lines under `DOES NOT PARSE` and `COULD NOT RUN` are the shell's own messages, so their wording differs on other systems:

```
part 1: working guard
OK: examples/no-secrets.sh parses, starts, said no to the bad input (exit 1), said yes to the good input
  it said: refused: line 2 holds a secret
exit 0

part 2: broken six ways
file moved away
MISSING: examples/no-secrets.sh
exit 1

quote left open
DOES NOT PARSE: examples/no-secrets.sh
  examples/no-secrets.sh: line 25: unexpected EOF while looking for matching `"'
  examples/no-secrets.sh: line 26: syntax error: unexpected end of file
exit 1

execute bit removed
NOT EXECUTABLE: examples/no-secrets.sh
exit 1

interpreter missing
COULD NOT RUN: examples/no-secrets.sh (exit 126)
  it said: sh: examples/no-secrets.sh: /no/such/interpreter: bad interpreter: No such file or directory
exit 1

body replaced by exit 0
SAID YES TO KNOWN-BAD INPUT: examples/no-secrets.sh
exit 1

body replaced by exit 1
SAID NO TO KNOWN-GOOD INPUT: examples/no-secrets.sh (exit 1)
exit 1

part 3: list with one broken entry
OK: no-secrets.sh parses, starts, said no to the bad input (exit 1), said yes to the good input
  it said: refused: line 2 holds a secret
OK: no-secrets.sh parses, starts, said no to the bad input (exit 1), said yes to the good input
  it said: refused: line 2 holds a secret
MISSING: missing-guard.sh
checked 3 guards: 2 ok, 1 failed
exit 1

part 4: list with no entry
checked nothing: examples/empty.list holds no entry
exit 2
```

The demo exits 0 only when every part printed what it should. Otherwise it prints `demo: a part did not match` and exits 1.

## The checks

The first check that fails prints its line and the checker exits 1. After a run of the guard, and after the OK line, the first line the guard printed, on standard output or standard error, is shown as `it said:`, so a refusal for the wrong reason is visible.

| Line | What it means | What to do |
| --- | --- | --- |
| `MISSING: GUARD` | No regular file is at that path. A name with no slash is a file in the current folder. | Put the file back. |
| `MISSING INPUT: PATH` | The bad input, or the good input, is not a readable regular file. | Give the checker a real file. The guard was not blamed for a file it never saw. |
| `DOES NOT PARSE: GUARD` | The parser reported a syntax error. Up to five lines of its message follow, indented two spaces. | Fix the syntax. The guard was not run. |
| `NOT EXECUTABLE: GUARD` | The current user has no execute bit on the file. | Restore the execute bit. |
| `TIMED OUT: GUARD gave no answer in SECs` | The guard did not exit in time. The guard and every process under it are sent TERM, then KILL one second later. | Find why it waits. |
| `SAID YES TO KNOWN-BAD INPUT: GUARD` | The guard exited 0 on the input it must refuse. | It let the bad input through. |
| `WRONG EXIT: GUARD exited M on the bad input, a refusal is N` | `--refuse-code N` is set and the guard exited with some other status. | Make the refusal use N, or drop the option if any non-zero exit is the refusal. |
| `COULD NOT RUN: GUARD (exit M)` | Exit 126 or 127. The shell could not start the guard. | The interpreter on the first line is missing, or the file could not be executed. |
| `DIED: GUARD (exit M)` | The exit is above 128. A signal ended the guard. | Find what ended it. An ordinary refusal does not look like this. |
| `SAID NO TO KNOWN-GOOD INPUT: GUARD (exit M)` | With `--good`, the guard exited non-zero on the clean input. | It refused an input it should accept. |
| `OK: ...` | The file is there, it parses (or the parse check was skipped), it starts, it refused the bad input, and it accepted the good input when one was given. | Keep the known-bad file beside it. |
| `it said: TEXT` | The first line the guard printed, on either output stream, on the run just judged. Nothing is printed when the guard printed nothing. | Read it before trusting the exit code. |
| `BAD LINE n: TEXT` | That line of the list is not a valid check. | Fix the line. It counts as failed. |
| `checked N guards: K ok, F failed` | The list finished. | Exit 0 when F is 0. Exit 1 otherwise. |
| `checked nothing: ...` | The list file cannot be read, or it holds no entry. | Exit 2. This run checked nothing. |
| `usage: ...` | The command line is wrong. This line is on stderr. | Exit 2. Fix the arguments. |

When the parse check is skipped, the OK line says `parse not checked (REASON)` where it would otherwise say `parses`. The reason is `no parser for this file`, or `NAME is not installed` when the file's parser is missing, such as `parse not checked (zsh is not installed)`.

## Check one guard

```
sh check-guard.sh [--stdin] [--good GOOD_INPUT] [--refuse-code N] [--timeout SEC] GUARD BAD_INPUT
```

By argument, the guard is started as `GUARD BAD_INPUT` and its standard input is empty:

```
sh check-guard.sh --good examples/fixtures/good.txt examples/no-secrets.sh examples/fixtures/bad.txt
```

With `--stdin`, the guard is started with no arguments and the input file is its standard input:

```
sh check-guard.sh --stdin --good examples/fixtures/good.txt examples/no-secrets.sh examples/fixtures/bad.txt
```

Leave out `--good` when you only have a known-bad file. The OK line then ends `no good input given`:

```
sh check-guard.sh examples/no-secrets.sh examples/fixtures/bad.txt
```

A runner that blocks only on one exit code can name it, as a whole number from 1 to 255. Exit 0 is still a yes, and it is judged before the refuse code. Any other exit is `WRONG EXIT`:

```
sh check-guard.sh --refuse-code 1 examples/no-secrets.sh examples/fixtures/bad.txt
```

`--timeout` is a positive whole number of seconds for each run of the guard. The default is 10. The checker returns as soon as the guard answers. At the limit, the guard and every process under it are sent TERM, then KILL one second later.

Stopping the checker with Ctrl-C, a terminate signal or a hang-up stops the guard and everything under it, and the checker exits 130, 143 or 129.

## Check all your guards

```
sh check-guard.sh --list FILE [--timeout SEC]
```

Each line that is not empty and does not start with `#` is one check, written the same way you would type it, split on spaces and tabs. Paths are relative to the folder the list file is in. `examples/guards.list` is:

```
# Two ways to ask the sample guard to refuse a known-bad file.
--good fixtures/good.txt no-secrets.sh fixtures/bad.txt
--stdin --good fixtures/good.txt no-secrets.sh fixtures/bad.txt
```

The last line is `checked N guards: K ok, F failed`. The checker exits 0 when F is 0 and exits 1 when F is anything else. It exits 2, and prints one line that starts with `checked nothing:`, when the file cannot be read or holds no entry. `--timeout` beside `--list` is the default for lines that carry none.

A scheduled job is one line, and a non-zero exit means a guard was off:

```
sh check-guard.sh --list /path/to/guards.list
```

A CI step is the same command. The paths inside the list are read from the list file's folder, so only the paths to the checker and to the list depend on where the step runs.

## Why quiet is not a pass

Quiet has two causes. The guard ran and had nothing bad to report, or the guard never ran and so had nothing to say. From the outside those two look the same.

A guard goes quiet in a few ordinary ways. The file was moved. An edit broke its syntax. The execute bit was lost. Its interpreter went away. Or it says yes to everything, including the input it was written to stop. The first four never reach a real decision. The fifth does run, and it accepts the bad input.

A non-zero exit on its own is not a refusal. Exit 126 and exit 127 are the shell saying it could not run the thing: the interpreter is missing, or the file could not be executed. An exit above 128 means a signal ended the guard. The checker names each of those. Any other non-zero exit, when you did not set `--refuse-code`, is the refusal.

## Make it a habit

Keep one known-bad input and one known-good input beside each guard. Run the checker after any edit to a guard, and run the list once a day. Treat a failing check as the guard having been off since its last good run.

## Tests

From this folder, the checks are:

```
python3 -m unittest discover -s tests -v
python3 tests/mutate.py
```

They need `sh`, `bash`, `zsh`, Python 3.9 or newer, and the tools the checker itself calls: `ps`, `awk`, `mktemp` and `sleep`. `node` and `ksh` are not needed, because the tests write stand-ins for both. The tests run the checker with `/bin/sh`. Set `CHECK_GUARD_SHELL` to another shell's path, such as `/bin/dash`, to run them with that shell.

`python3 tests/mutate.py` plants one bug at a time in a copy of the checker and runs the tests against each one, which took about seven minutes on the Mac it was tested on. It counts a bug as caught only when a test fails on an assertion. A real run ends with:

```
caught 58 of 58
```

The run exits 0 when every planted bug is caught. It exits 1 when the caught count is short of the total. It exits 2 when a planted edit is not in the checker exactly once or changes nothing, or when the edited script fails `/bin/sh -n` (and `/bin/dash -n` when `/bin/dash` exists).

The mutation run does not first run the tests on the unchanged checker, so run the first command before it: when a test already fails there, every planted bug reads as caught.

## Related

The same author also publishes [harness-guard](https://github.com/LucasCooperBey/harness-guard), a set of safety hooks for a coding agent, whose hooks read their input on standard input.

## Limits

The checker runs your guard for real on the two inputs, so both must be files that are safe to feed it. A guard that fails for another reason with an ordinary exit code still reads as a refusal unless `--refuse-code` or `--good` catches it, which is why the `it said` line is shown. An exit above 128 is read as the guard having been ended by a signal. With `--refuse-code` set to 126, 127 or a number above 128, an exit with that number reads as the refusal, although the shell uses those numbers for a guard that could not start or that a signal ended. The parse check covers sh, bash, zsh, ksh, Python 3 and JavaScript. It runs the parser of that name found on your PATH (`python3` for Python, `node` for JavaScript), so a guard is judged by the versions installed where the check runs. A first line that names `python2` is not parse-checked, and neither is a compiled guard. A guard called any other way than a path as its first argument, or the input on standard input, needs a two-line wrapper:

```
#!/bin/sh
exec /path/to/the-real-guard --flag "$1"
```

List lines cannot hold a path with a space. The checker is a POSIX shell script and does not run in a Windows command prompt.

A process the guard left running after it answered is not touched. A process whose own parent had already exited is out of reach of the time limit. The process list comes from `ps`; where `ps` is missing or lists nothing, the limit stops only the guard's own process, and the checker's timer may sit idle until the limit passes. The guard runs with the interrupt and quit signals ignored, as any background command of a shell without job control does. It was tested on macOS with Python 3.9 and Python 3.14, under `/bin/sh` and `/bin/dash`.

## License

MIT. The full text is in `LICENSE`.

## Built at San Diego AI Studio

This tool comes out of San Diego AI Studio, the builders' community in San Diego, where builders show the systems they run. More projects: https://sandiegoaistudio.com/projects
