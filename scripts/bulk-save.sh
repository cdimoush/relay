#!/usr/bin/env bash
# bulk-save.sh — commit and push all dirty repos
# Usage: ./scripts/bulk-save.sh ["optional commit message"]

set -euo pipefail

MSG="${1:-bulk save $(date '+%Y-%m-%d %H:%M')}"

REPOS=(
  /home/ubuntu/aura
  /home/ubuntu/clone
  /home/ubuntu/cyborg
  /home/ubuntu/gtc_wingman
  /home/ubuntu/isaac_research
  /home/ubuntu/memories
  /home/ubuntu/relay
)

saved=0
clean=0
failed=0
total=${#REPOS[@]}

for repo in "${REPOS[@]}"; do
  name=$(basename "$repo")

  if [[ ! -d "$repo/.git" ]]; then
    echo "[$name] skipped — not a git repo"
    ((failed++)) || true
    continue
  fi

  cd "$repo"
  if [[ -z $(git status --porcelain 2>/dev/null) ]]; then
    echo "[$name] clean"
    ((clean++)) || true
    continue
  fi

  echo "[$name] dirty — saving..."
  git add -A
  git commit -m "$MSG" --quiet 2>&1 || { echo "[$name] commit failed"; ((failed++)) || true; continue; }

  if git remote get-url origin &>/dev/null; then
    git push --quiet 2>&1 || echo "[$name] push failed (committed locally)"
  fi

  ((saved++)) || true
done

echo ""
echo "Saved $saved/$total repos, $clean clean, $failed failed"
