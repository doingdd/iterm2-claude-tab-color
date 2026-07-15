#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BURNKIT="$REPO_ROOT/bin/burnkit"

fail() {
    printf 'FAIL %s\n' "$1" >&2
    exit 1
}

bash -n "$BURNKIT"
"$BURNKIT" --help >/dev/null
projects_output="$($BURNKIT projects)"
for project in "Claude Lanes" "Coding Usage Bar" "iTerm2 AI Tab Color"; do
    printf '%s\n' "$projects_output" | grep -Fq "$project" || fail "projects output is missing: $project"
done
printf '%s\n' "$projects_output" | grep -Fq 'Command:    c' || fail "Claude Lanes command is not the supported c entrypoint"

temp_home="$(mktemp -d "${TMPDIR:-/tmp}/burnkit-thin.XXXXXX")"
trap 'rm -rf "$temp_home"' EXIT
if HOME="$temp_home" "$BURNKIT" install all --dry-run >"$temp_home/stdout" 2>"$temp_home/stderr"; then
    fail "removed install command unexpectedly succeeded"
fi
grep -Fq 'was removed' "$temp_home/stderr" || fail "removed install command lacks migration guidance"
[ "$(find "$temp_home" -mindepth 1 -maxdepth 1 ! -name stdout ! -name stderr | wc -l | tr -d ' ')" = "0" ] || fail "removed command modified isolated HOME"

[ ! -d "$REPO_ROOT/tools" ] || fail "legacy tools directory still exists"

printf 'PASS BurnKit is a read-only directory with no bundled tool implementations\n'
