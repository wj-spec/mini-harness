#!/usr/bin/env bash
# 把 mini-harness 变更侧栏所需的 Ghostty quick terminal 配置写入用户配置。
# 只新增一段带注释的配置；已有 quick-terminal-position 时不会覆盖。
set -euo pipefail

config_dir="${XDG_CONFIG_HOME:-$HOME/.config}/ghostty"
config="$config_dir/config"
binary="/Applications/Ghostty.app/Contents/MacOS/ghostty"
if [ -n "${GHOSTTY_BIN_DIR:-}" ] && [ -x "${GHOSTTY_BIN_DIR}/ghostty" ]; then
  binary="${GHOSTTY_BIN_DIR}/ghostty"
fi

dry_run=0
if [ "${1:-}" = "--dry-run" ]; then
  dry_run=1
elif [ "$#" -gt 0 ]; then
  echo "用法：$0 [--dry-run]" >&2
  exit 2
fi

if [ -f "$config" ] && grep -Eq '^[[:space:]]*quick-terminal-position[[:space:]]*=' "$config"; then
  current="$(grep -E '^[[:space:]]*quick-terminal-position[[:space:]]*=' "$config" | tail -1 | sed -E 's/.*=[[:space:]]*//; s/[[:space:]]+$//')"
  if [ "$current" = "right" ]; then
    echo "Ghostty quick terminal 已配置为右侧：$config"
    exit 0
  fi
  echo "检测到现有 quick-terminal-position = ${current}；请改为 right 或删除该行后重试。" >&2
  echo "配置：$config" >&2
  exit 1
fi

block='# mini-harness Git 变更侧栏（quick terminal 停靠右侧）
quick-terminal-position = right
quick-terminal-size = 420px
quick-terminal-autohide = false
quick-terminal-animation-duration = 0'

if [ "$dry_run" = "1" ]; then
  echo "将写入 ${config}："
  printf '%s\n' "$block"
  exit 0
fi

mkdir -p "$config_dir"
printf '\n%s\n' "$block" >> "$config"

if [ -x "$binary" ]; then
  if ! "$binary" +validate-config --config-file="$config" >/dev/null 2>&1; then
    echo "警告：Ghostty 配置校验未通过，请检查 ${config}（已追加的配置块本身应为合法项）。" >&2
  fi
fi

echo "已写入 $config"
echo "请完全退出并重启 Ghostty（quick-terminal-position 需要重启才能生效），然后在工作台按 Ctrl+G 或输入 /sidebar。"
