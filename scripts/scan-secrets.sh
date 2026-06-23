#!/usr/bin/env bash
#
# scan-secrets.sh — a dependency-free, pre-commit/pre-push secrets + leak scan.
#
# WHY THIS EXISTS (plain terms): mcs_egress.py guards data leaving to a *model*.
# Nothing guarded data leaving to a *public git remote*. This is that guard: it
# greps the tracked files (or the whole working tree) for the leak shapes that
# must NEVER reach a public repo — API keys, tokens, private-key blocks, the
# operator's home path, and the private company's real names — and exits non-zero
# if it finds any, so CI or a git hook can block the push.
#
# It deliberately reuses the same secret/credential shapes as the egress gate
# (mcs_egress.py) so "what counts as a secret" is consistent across the project.
#
# USAGE:
#   ./scripts/scan-secrets.sh            # scan git-tracked files (preferred)
#   ./scripts/scan-secrets.sh --all      # scan the whole working tree
#   ./scripts/scan-secrets.sh path ...   # scan only the given paths
#
# EXIT: 0 = clean, 1 = potential leak found, 2 = usage / environment error.
# No dependencies beyond bash + grep + (git, only for the default mode).

set -euo pipefail

# Resolve the repo root as the parent of this script's dir (portable; no
# hardcoded home path).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ----------------------------------------------------------------------------
# Patterns. Each is "label|ERE". Keep these in sync with mcs_egress.py.
# These intentionally err toward catching too much; a false positive is cheap to
# whitelist, a leaked key on a public remote is forever.
# ----------------------------------------------------------------------------
PATTERNS=(
  "OpenAI-style key|sk-[A-Za-z0-9]{16,}"
  "AWS access key id|AKIA[0-9A-Z]{16}"
  "GitHub PAT|ghp_[A-Za-z0-9]{20,}"
  "GitHub fine-grained PAT|github_pat_[A-Za-z0-9_]{20,}"
  "Slack token|xox[baprs]-[A-Za-z0-9-]{10,}"
  "Google API key|AIza[0-9A-Za-z_-]{30,}"
  "PEM private key block|-----BEGIN [A-Z ]*PRIVATE KEY-----"
  "Generic secret assignment|(api[_-]?key|secret|password|passwd|access[_-]?token|client[_-]?secret|bearer)[\"' ]*[:=][\"' ]*[A-Za-z0-9/+_-]{8,}"
  "US SSN shape|[0-9]{3}-[0-9]{2}-[0-9]{4}"
  # De-weld leaks: the private origin's identity must not ship.
  "Operator home path|/Users/danyalpanjwani"
  "Private company/person name|[Vv]italiti|[Vv]ivian|[Ss]ophiafay|[Ss]ophia Fay"
)

# Paths the scan should ignore (binary/cache/this-script-itself, which by
# necessity contains the patterns above as literals).
EXCLUDE_RE='(^|/)(\.git/|__pycache__/|\.pytest_cache/|\.mypy_cache/|.*\.pyc$|.*\.png$|.*\.jpg$|.*\.jpeg$|.*\.gif$|.*\.pdf$|.*\.woff2$|scripts/scan-secrets\.sh$)'

# ----------------------------------------------------------------------------
# Build the file list.
# ----------------------------------------------------------------------------
mode="tracked"
explicit_paths=()
case "${1:-}" in
  --all) mode="all"; shift ;;
  --help|-h)
    grep -E '^#( |$)' "$0" | sed -E 's/^# ?//'
    exit 0 ;;
  "") : ;;
  *) mode="explicit"; explicit_paths=("$@") ;;
esac

files=()
if [[ "${mode}" == "explicit" ]]; then
  files=("${explicit_paths[@]}")
elif [[ "${mode}" == "tracked" ]]; then
  if command -v git >/dev/null 2>&1 && git -C "${REPO_ROOT}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    while IFS= read -r f; do files+=("${REPO_ROOT}/${f}"); done \
      < <(git -C "${REPO_ROOT}" ls-files)
  else
    echo "note: not a git repo yet (or git missing) — scanning the whole tree instead." >&2
    mode="all"
  fi
fi
if [[ "${mode}" == "all" ]]; then
  while IFS= read -r f; do files+=("${f}"); done \
    < <(find "${REPO_ROOT}" -type f)
fi

if [[ "${#files[@]}" -eq 0 ]]; then
  echo "scan-secrets: no files to scan." >&2
  exit 2
fi

# ----------------------------------------------------------------------------
# Scan.
# ----------------------------------------------------------------------------
hits=0
for f in "${files[@]}"; do
  rel="${f#"${REPO_ROOT}/"}"
  [[ "${rel}" =~ ${EXCLUDE_RE} ]] && continue
  [[ -f "${f}" ]] || continue
  # Skip obvious binaries (grep -I treats them as non-matching anyway, but this
  # avoids noise).
  for entry in "${PATTERNS[@]}"; do
    label="${entry%%|*}"
    pat="${entry#*|}"
    if matched="$(grep -nIE "${pat}" "${f}" 2>/dev/null)"; then
      while IFS= read -r line; do
        # A line may explicitly waive a finding with an inline marker, e.g. a
        # test fixture that *deliberately* contains a fake secret to prove the
        # gate blocks it. Convention (matches detect-secrets): the comment
        # `pragma: allowlist secret` on the same line.
        if [[ "${line}" == *"pragma: allowlist secret"* ]]; then
          continue
        fi
        echo "LEAK[${label}] ${rel}:${line}"
        hits=$((hits + 1))
      done <<< "${matched}"
    fi
  done
done

echo "----------------------------------------"
if [[ "${hits}" -gt 0 ]]; then
  echo "scan-secrets: ${hits} potential leak(s) found. FIX or whitelist before committing/pushing." >&2
  echo "(A real false positive? Narrow the pattern or move the value out of the tracked file.)" >&2
  exit 1
fi
echo "scan-secrets: clean — no secret/credential/de-weld leaks found in ${#files[@]} files."
exit 0
