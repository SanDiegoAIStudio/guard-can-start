#!/bin/sh
# Show a working guard, the same guard broken six ways, then two lists.

set -f
demo_dir=$(CDPATH= cd "$(dirname "$0")" && pwd) || exit 1
root=$(CDPATH= cd "$demo_dir/.." && pwd) || exit 1
tmp=$(mktemp -d) || exit 1
trap 'rm -rf "$tmp"' EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

mkdir -p "$tmp/examples/fixtures"
/bin/cp -f "$root/check-guard.sh" "$tmp/check-guard.sh"
/bin/cp -f "$root/examples/no-secrets.sh" "$tmp/examples/no-secrets.sh"
/bin/cp -f "$root/examples/fixtures/bad.txt" "$tmp/examples/fixtures/bad.txt"
/bin/cp -f "$root/examples/fixtures/good.txt" "$tmp/examples/fixtures/good.txt"
/bin/cp -f "$root/examples/guards.list" "$tmp/examples/guards.list"
chmod +x "$tmp/check-guard.sh" "$tmp/examples/no-secrets.sh"
/bin/cp -f "$tmp/examples/no-secrets.sh" "$tmp/pristine.sh"
cd "$tmp" || exit 1

fail=0
out=$tmp/out.txt
err=$tmp/err.txt

restore() {
  /bin/cp -f "$tmp/pristine.sh" examples/no-secrets.sh
  chmod +x examples/no-secrets.sh
}

run_one() {
  sh check-guard.sh --good examples/fixtures/good.txt examples/no-secrets.sh examples/fixtures/bad.txt >"$out" 2>"$err"
  code=$?
}

show() {
  printf '%s\n' "$1"
  cat "$out"
  printf 'exit %s\n\n' "$2"
  case $3 in
    *"$4"*) ;;
    *) fail=1 ;;
  esac
  [ "$2" -eq "$5" ] || fail=1
  [ ! -s "$err" ] || fail=1
}

run_one
show "part 1: working guard" "$code" "$(cat "$out")" "OK:" 0

printf '%s\n' "part 2: broken six ways"

mv examples/no-secrets.sh examples/no-secrets.sh.away
run_one
show "file moved away" "$code" "$(cat "$out")" "MISSING:" 1
mv examples/no-secrets.sh.away examples/no-secrets.sh

restore
printf '\nnote="\n' >> examples/no-secrets.sh
run_one
show "quote left open" "$code" "$(cat "$out")" "DOES NOT PARSE:" 1

restore
chmod a-x examples/no-secrets.sh
run_one
show "execute bit removed" "$code" "$(cat "$out")" "NOT EXECUTABLE:" 1

restore
{
  printf '%s\n' '#!/no/such/interpreter'
  skip=1
  while IFS= read -r line || [ -n "$line" ]; do
    if [ "$skip" -eq 1 ]; then
      skip=0
      continue
    fi
    printf '%s\n' "$line"
  done < "$tmp/pristine.sh"
} > examples/no-secrets.sh
chmod +x examples/no-secrets.sh
run_one
show "interpreter missing" "$code" "$(cat "$out")" "COULD NOT RUN:" 1

restore
printf '%s\n' '#!/bin/sh' 'exit 0' > examples/no-secrets.sh
chmod +x examples/no-secrets.sh
run_one
show "body replaced by exit 0" "$code" "$(cat "$out")" "SAID YES TO KNOWN-BAD INPUT:" 1

restore
printf '%s\n' '#!/bin/sh' 'exit 1' > examples/no-secrets.sh
chmod +x examples/no-secrets.sh
run_one
show "body replaced by exit 1" "$code" "$(cat "$out")" "SAID NO TO KNOWN-GOOD INPUT:" 1

restore
/bin/cp -f examples/guards.list examples/plus.list
printf '%s\n' 'missing-guard.sh fixtures/bad.txt' >> examples/plus.list
sh check-guard.sh --list examples/plus.list >"$out" 2>"$err"
code=$?
show "part 3: list with one broken entry" "$code" "$(cat "$out")" "checked 3 guards: 2 ok, 1 failed" 1

: > examples/empty.list
sh check-guard.sh --list examples/empty.list >"$out" 2>"$err"
code=$?
show "part 4: list with no entry" "$code" "$(cat "$out")" "checked nothing:" 2

if [ "$fail" -eq 0 ]; then
  exit 0
fi
printf '%s\n' "demo: a part did not match"
exit 1
