set -euo pipefail
cp "$BENCH_REPO_ROOT/tui/tests/app.test.tsx" tui/tests/app.test.tsx
cp "$BENCH_TASK_DIR/hidden/bench-footer.test.tsx" tui/tests/bench-footer.test.tsx
npm --prefix tui run build --silent
node --test tui/dist/tests/bench-footer.test.js tui/dist/tests/app.test.js
