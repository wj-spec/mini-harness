set -euo pipefail
npm --prefix tui run build --silent
node --test --test-name-pattern "footer shows one outcome" tui/dist/tests/app.test.js
