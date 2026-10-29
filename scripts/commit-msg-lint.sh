#!/usr/bin/env bash
# Conventional Commits check. Invoked by pre-commit's commit-msg stage with the
# path to the commit message file as $1.
# Format: type(optional-scope)!: subject   (e.g. "feat(guard): add normalize flag")
set -euo pipefail

msg_file="$1"
# First non-comment, non-empty line is the subject.
subject="$(grep -vE '^\s*#' "$msg_file" | grep -vE '^\s*$' | head -n1 || true)"

# Allow merge/revert/fixup commits through untouched.
case "$subject" in
  "Merge "*|"Revert "*|"fixup!"*|"squash!"*) exit 0 ;;
esac

types='feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert'
pattern="^(${types})(\([a-z0-9._-]+\))?!?: .+"

if [[ ! "$subject" =~ $pattern ]]; then
  echo "✖ Commit message does not follow Conventional Commits." >&2
  echo "  Got:    $subject" >&2
  echo "  Expect: <type>(<scope>): <subject>" >&2
  echo "  Types:  ${types//|/, }" >&2
  echo "  e.g.:   feat(guard): add opt-in unicode normalization" >&2
  exit 1
fi
