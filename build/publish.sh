#!/usr/bin/env bash
# Publish only the study itself to a `gh-pages` branch.
#
# Deploying Pages from `main` works and is the simple option, but it also serves
# build/ and analysis/ — including the key that says which code was which method.
# A participant would have to go looking, but they could. This puts just the pages
# on a separate branch so the served site carries nothing but the study.
#
#   bash build/publish.sh            # build from ./ and push gh-pages
#   bash build/publish.sh --dry-run  # stage it, show what would go, push nothing
#
# Afterwards set Settings > Pages > Deploy from branch > gh-pages / root.

set -euo pipefail

BRANCH="gh-pages"
SITE=(index.html css js data assets .nojekyll)
DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1

cd "$(git rev-parse --show-toplevel)"

for path in "${SITE[@]}"; do
  if [[ ! -e "$path" ]]; then
    echo "missing: $path" >&2
    echo "Run the build first: python build/build_study.py --root . --out ." >&2
    exit 1
  fi
done

if ! grep -q '"endpoint": *"[^"]' js/config.js 2>/dev/null && \
   ! grep -q "endpoint: *\"[^\"]" js/config.js; then
  echo "warning: js/config.js has no endpoint set — the published study will not"
  echo "         be able to collect answers. Continuing anyway."
fi

WORKTREE="$(mktemp -d)"
trap 'git worktree remove --force "$WORKTREE" 2>/dev/null || true; rm -rf "$WORKTREE"' EXIT

if git show-ref --verify --quiet "refs/heads/$BRANCH"; then
  git worktree add "$WORKTREE" "$BRANCH" >/dev/null
else
  git worktree add --detach "$WORKTREE" >/dev/null
  git -C "$WORKTREE" checkout --orphan "$BRANCH" >/dev/null
  git -C "$WORKTREE" rm -rf . >/dev/null 2>&1 || true
fi

# Replace the branch contents wholesale, so a removed asset really disappears.
find "$WORKTREE" -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
for path in "${SITE[@]}"; do
  cp -r "$path" "$WORKTREE/"
done

git -C "$WORKTREE" add -A
if git -C "$WORKTREE" diff --cached --quiet; then
  echo "gh-pages is already up to date."
  exit 0
fi

echo "Files that would be published:"
git -C "$WORKTREE" diff --cached --name-status | head -30
echo "  ($(git -C "$WORKTREE" diff --cached --name-only | wc -l) files, $(du -sh "$WORKTREE" | cut -f1))"

if [[ $DRY_RUN -eq 1 ]]; then
  echo "--dry-run: nothing committed or pushed."
  exit 0
fi

git -C "$WORKTREE" commit -q -m "Publish study site ($(date -u +%Y-%m-%dT%H:%MZ))"
git -C "$WORKTREE" push -u origin "$BRANCH"
echo "Pushed $BRANCH. Set Settings > Pages > Deploy from branch > $BRANCH / root."
