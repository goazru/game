#!/usr/bin/env bash
set -euo pipefail

REPO="goazru/game"
BRANCH="gh-pages"
DIST_DIR="$(cd "$(dirname "$0")/../dist" && pwd)"
PAGES_URL="https://goazru.github.io/game/"

# 1. gh-pages ブランチへ dist/ を push（worktree 方式、master を汚さない）
WORKTREE_DIR="$(mktemp -d)"
trap 'git -C "$(dirname "$0")/.." worktree remove --force "$WORKTREE_DIR" 2>/dev/null || true; rm -rf "$WORKTREE_DIR"' EXIT

cd "$(dirname "$0")/.."

# gh-pages ブランチが存在しない場合は孤立ブランチとして作成
if git ls-remote --exit-code origin "$BRANCH" >/dev/null 2>&1; then
  git worktree add "$WORKTREE_DIR" "$BRANCH"
  # 既存ファイルをすべて削除して置き換え
  find "$WORKTREE_DIR" -mindepth 1 -not -path '*/.git*' -delete
else
  git worktree add --orphan -b "$BRANCH" "$WORKTREE_DIR"
fi

cp -r "$DIST_DIR"/. "$WORKTREE_DIR/"

cd "$WORKTREE_DIR"
git add -A
if git diff --cached --quiet; then
  echo "変更なし。スキップします。"
else
  git commit -m "deploy: $(date '+%Y-%m-%d %H:%M:%S')"
  git push origin "$BRANCH"
  echo "gh-pages ブランチへ push 完了"
fi

cd "$(dirname "$0")/.."

# 2. Pages を有効化（409 = すでに有効、正常扱い）
echo "GitHub Pages を有効化中..."
HTTP_STATUS=$(gh api "repos/$REPO/pages" -X POST \
  --field "source[branch]=$BRANCH" \
  --field "source[path]=/" \
  --silent --include 2>&1 | grep -m1 "^HTTP" | awk '{print $2}' || true)

# gh api はエラー時に exit 1 するので別途捕捉
set +e
gh api "repos/$REPO/pages" -X POST \
  --field "source[branch]=$BRANCH" \
  --field "source[path]=/" \
  --silent 2>&1
API_EXIT=$?
set -e

if [ $API_EXIT -eq 0 ]; then
  echo "Pages を新規有効化しました"
elif gh api "repos/$REPO/pages" --silent >/dev/null 2>&1; then
  echo "Pages はすでに有効 (409 正常)"
else
  echo "Pages 有効化に失敗しました" >&2
  exit 1
fi

# 3. URL が 200 を返すまで最大 5 分ポーリング
echo "公開URL確認中: $PAGES_URL"
TIMEOUT=300
INTERVAL=15
ELAPSED=0

while [ $ELAPSED -lt $TIMEOUT ]; do
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$PAGES_URL" || true)
  if [ "$STATUS" = "200" ]; then
    echo ""
    echo "デプロイ完了！"
    echo "URL: $PAGES_URL"
    exit 0
  fi
  printf "  %ds 経過 (HTTP %s) ...\n" "$ELAPSED" "$STATUS"
  sleep $INTERVAL
  ELAPSED=$((ELAPSED + INTERVAL))
done

echo ""
echo "5分経過しましたがまだ反映中の可能性があります。"
echo "URL: $PAGES_URL"
echo "しばらく後にアクセスしてください。"
