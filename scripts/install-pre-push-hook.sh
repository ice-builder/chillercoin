#!/usr/bin/env bash
# Install pre-push hook: runs public-push-audit and blocks push on BLOCK findings.
# You still make the final product decision; hook prevents accidental push of private/trading code.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOOK="$ROOT/.git/hooks/pre-push"
mkdir -p "$ROOT/.git/hooks"
cat > "$HOOK" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"
echo "==> ChillerCoin public push gate"
set +e
node "$ROOT/scripts/public-push-audit.mjs" --root "$ROOT"
code=$?
set -e
echo ""
echo "Report: $ROOT/scripts/reports/LATEST.md"
if [[ "$code" -eq 0 ]]; then
  echo "Audit clean. Proceeding with push."
  exit 0
fi
if [[ "$code" -eq 2 ]]; then
  if [[ "${PUBLIC_PUSH_ALLOW_WARN:-}" == "1" ]]; then
    echo "Warnings only — PUBLIC_PUSH_ALLOW_WARN=1 set. Proceeding after your explicit override."
    exit 0
  fi
  echo "WARNINGS found. Read the report, then either fix copy or push with:"
  echo "  PUBLIC_PUSH_ALLOW_WARN=1 git push"
  exit 1
fi
echo "BLOCK findings — do not push. Fix the tree, then re-run audit."
echo "(Emergency only: git push --no-verify)"
exit 1
EOF
chmod +x "$HOOK"
chmod +x "$ROOT/scripts/public-push-audit.mjs" "$ROOT/scripts/install-pre-push-hook.sh"
echo "Installed: $HOOK"
echo "Run manually: node scripts/public-push-audit.mjs"
