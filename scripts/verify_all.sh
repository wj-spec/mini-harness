#!/usr/bin/env bash
# 一次性本地验收：Python 全量 + TUI + 长任务 fake benchmark + doctor
# 用法：./scripts/verify_all.sh [--with-real [--real-runs N]]
#   --with-real    额外跑真实模型六模式 suite（需要 .env 或环境变量里的 key）
#   --real-runs N  与 --with-real 搭配：重复跑 N 轮 suite（稳定性样本，1-5）
#   --with-real-bench 额外跑真实模型长任务场景（bench_long --real，消耗 token）
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 2
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

WITH_REAL=0
WITH_REAL_BENCH=0
REAL_RUNS=1
while [ "$#" -gt 0 ]; do
  case "$1" in
    --with-real) WITH_REAL=1 ;;
    --with-real-bench) WITH_REAL_BENCH=1 ;;
    --real-runs)
      shift
      if [ "${1:-}" = "" ]; then
        echo "--real-runs 需要次数（1-5）" >&2
        exit 2
      fi
      REAL_RUNS="$1"
      ;;
    *)
      echo "未知参数：$1（支持 --with-real / --real-runs N / --with-real-bench）" >&2
      exit 2
      ;;
  esac
  shift
done

PYTHON=".venv/bin/python3"
if [ ! -x "$PYTHON" ]; then
  echo "缺少 $PYTHON（先创建 .venv）" >&2
  exit 2
fi

FAILED=0
declare -a RESULTS=()

report() {
  local name=$1 status=$2 detail=${3:-}
  RESULTS+=("$name|$status|$detail")
  if [ "$status" != "PASS" ] && [ "$status" != "EXPECTED" ]; then
    FAILED=1
  fi
}

echo "== Python 全量测试 =="
if ./run_tests.sh > /tmp/verify-python.log 2>&1; then
  line=$(grep -E "passed" /tmp/verify-python.log | tail -1)
  report "Python" PASS "$line"
else
  line=$(grep -E "passed|failed" /tmp/verify-python.log | tail -1)
  report "Python" FAIL "$line"
fi

echo "== TUI 测试 =="
if (cd tui && npm test > /tmp/verify-tui.log 2>&1); then
  line=$(grep -E "ℹ pass" /tmp/verify-tui.log | tail -1)
  report "TUI" PASS "$line"
elif grep -q "out of pty devices" /tmp/verify-tui.log && grep -qE "ℹ fail 1" /tmp/verify-tui.log; then
  line=$(grep -E "ℹ pass|ℹ fail" /tmp/verify-tui.log | tr '\n' ' ')
  report "TUI" EXPECTED "无 pty 环境下预期失败：$line"
else
  line=$(grep -E "ℹ pass|ℹ fail" /tmp/verify-tui.log | tr '\n' ' ')
  report "TUI" FAIL "$line"
fi

echo "== 长任务 fake benchmark（14 场景） =="
if "$PYTHON" scripts/bench_long.py --runs 1 > /tmp/verify-bench.log 2>&1; then
  passed=$(grep -c "PASS" /tmp/verify-bench.log)
  report "bench-long" PASS "$passed scenarios PASS"
else
  passed=$(grep -c "PASS" /tmp/verify-bench.log)
  report "bench-long" FAIL "$passed scenarios PASS"
fi

echo "== doctor =="
if "$PYTHON" scripts/doctor.py --doctor --workspace "$ROOT" > /tmp/verify-doctor.log 2>&1; then
  report "doctor" PASS "$(tail -1 /tmp/verify-doctor.log)"
else
  report "doctor" FAIL "$(tail -1 /tmp/verify-doctor.log)"
fi

echo "== 无人值守红队检查 =="
if "$PYTHON" -m app --redteam --workspace "$ROOT" > /tmp/verify-redteam.log 2>&1; then
  report "redteam" PASS "默认拒绝矩阵通过"
else
  report "redteam" FAIL "见 /tmp/verify-redteam.log"
fi

if [ "$WITH_REAL_BENCH" = "1" ]; then
  echo "== 真实模型长任务 bench（--real） =="
  if "$PYTHON" scripts/bench_long.py --real --runs 1 > /tmp/verify-real-bench.log 2>&1; then
    passed=$(grep -c "PASS" /tmp/verify-real-bench.log)
    report "real-bench" PASS "$passed real scenarios PASS"
  else
    passed=$(grep -c "PASS" /tmp/verify-real-bench.log)
    report "real-bench" FAIL "$passed real scenarios PASS"
  fi
fi

if [ "$WITH_REAL" = "1" ]; then
  echo "== 真实模型 suite（六模式 × ${REAL_RUNS}） =="
  if VIBE_TASK_RUNTIME=1 "$PYTHON" scripts/real_p1_acceptance.py --suite --suite-runs "$REAL_RUNS" --keep > /tmp/verify-real.log 2>&1; then
    report "real-suite" PASS "六模式 × ${REAL_RUNS} 通过"
  else
    report "real-suite" FAIL "见 /tmp/verify-real.log"
  fi
fi

echo
echo "==== 验收汇总 ===="
for row in "${RESULTS[@]}"; do
  IFS='|' read -r name status detail <<< "$row"
  echo "[$status] $name — $detail"
done
echo
if [ "$FAILED" = "1" ]; then
  echo "结论：有失败项，见 /tmp/verify-*.log" >&2
  exit 1
fi
echo "结论：全部通过（TUI 无 pty 环境失败按预期记录）"
