#!/bin/sh
# Refuse a line that starts with SECRET=. Read a file, or standard input.

n=0
scan() {
  while IFS= read -r line || [ -n "$line" ]; do
    n=$((n + 1))
    case $line in
      SECRET=*)
        printf 'refused: line %s holds a secret\n' "$n"
        exit 1
        ;;
    esac
  done
}

if [ $# -eq 0 ]; then
  scan
else
  [ -f "$1" ] && [ -r "$1" ] || { printf 'refused: cannot read the input\n'; exit 1; }
  scan < "$1"
fi
exit 0
