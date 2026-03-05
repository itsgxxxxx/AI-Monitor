#!/usr/bin/env bash
set -euo pipefail

# Installs findsimilarpost into OpenClaw global skills directory.
# Usage:
#   bash skills/findsimilarpost/scripts/install_openclaw.sh
#   bash skills/findsimilarpost/scripts/install_openclaw.sh --target "$HOME/.openclaw/skills"
#   bash skills/findsimilarpost/scripts/install_openclaw.sh --no-deps

TARGET_ROOT="${HOME}/.openclaw/skills"
INSTALL_DEPS=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target)
      TARGET_ROOT="$2"
      shift 2
      ;;
    --no-deps)
      INSTALL_DEPS=0
      shift
      ;;
    *)
      echo "Unknown arg: $1" >&2
      exit 1
      ;;
  esac
done

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found. Please install Python 3 first." >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_SRC="$(cd "${SCRIPT_DIR}/.." && pwd)"
TARGET_DIR="${TARGET_ROOT}/findsimilarpost"

mkdir -p "${TARGET_ROOT}"
rm -rf "${TARGET_DIR}"
mkdir -p "${TARGET_DIR}"

if command -v rsync >/dev/null 2>&1; then
  rsync -a --exclude "__pycache__/" --exclude "*.pyc" "${SKILL_SRC}/" "${TARGET_DIR}/"
else
  cp -R "${SKILL_SRC}/." "${TARGET_DIR}/"
  find "${TARGET_DIR}" -type d -name "__pycache__" -prune -exec rm -rf {} +
  find "${TARGET_DIR}" -type f -name "*.pyc" -delete
fi

if [[ "${INSTALL_DEPS}" -eq 1 ]]; then
  python3 -m pip install --user --quiet requests PyYAML || true
fi

echo "Installed skill to: ${TARGET_DIR}"
echo "Next:"
echo "1) export TIKHUB_API_KEY='your_key'"
echo "2) python ${TARGET_DIR}/findsimilarpost.py 'https://x.com/xxx/status/yyy' --agent"
