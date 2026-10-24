#!/bin/bash
# 一键安装：检查依赖 → 拉 mibe 并打补丁 → 装 Python 依赖 → 自检 →（可选）连音箱 → 装开机自启。
# 重复执行是安全的：已拉过、已打过补丁、已装过的步骤会跳过或原样重装。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MIBE_URL="https://github.com/yihong0618/mibe.git"
# 补丁是对着这个提交做的。上游往前走了补丁可能打不上，所以钉死版本，而不是跟最新
MIBE_REF="2c736005d579762a066b186840bfdf88385f3fda"
MIBE="${ROOT}/vendor/mibe"
STATUS_URL="http://127.0.0.1:8477/"

DEVICE=""
AGENT=1

usage() {
  cat <<EOF
用法 / Usage: ./scripts/setup.sh [--device <音箱IP>] [--no-agent]

  --device IP   无线 ADB 连接音箱（IP:5555）并记住地址；不给则由后台代理自动扫描当前网段
                Connect to the speaker over wireless ADB and remember it (otherwise auto-scan)
  --no-agent    只装依赖和自检，不装开机自启（LaunchAgent）
                Install dependencies and self-test only, skip the LaunchAgents
  -h, --help    显示帮助 / Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --device) DEVICE="${2:-}"; shift 2 ;;
    --no-agent) AGENT=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数 / unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
ok() { printf '    ✓ %s\n' "$*"; }
warn() { printf '    ! %s\n' "$*" >&2; }
die() { printf '    ✗ %s\n' "$*" >&2; exit 1; }

# 和 adb-bridge.py 用同一套查找顺序，免得这里找到的和后台用的不是同一个 adb
find_adb() {
  local p
  for p in "${ROOT}/.local-tools/platform-tools/adb" /opt/homebrew/bin/adb "$(command -v adb || true)"; do
    if [[ -n "${p}" && -x "${p}" ]]; then echo "${p}"; return 0; fi
  done
  return 1
}

step "检查环境 / Checking prerequisites"
[[ "$(uname -s)" == "Darwin" ]] || die "只支持 macOS（要读本机的 Codex / Claude 日志和「照片」图库）/ macOS only"
command -v git >/dev/null || die "缺 git：xcode-select --install"
ok "git"
command -v uv >/dev/null || die "缺 uv：brew install uv（或 curl -LsSf https://astral.sh/uv/install.sh | sh）"
ok "uv（会自动准备 mibe 需要的 Python 3.12）"
[[ -x /usr/bin/python3 ]] || die "缺 /usr/bin/python3：xcode-select --install"
ok "python3（ADB 代理用系统自带的）"
ADB="$(find_adb || true)"
if [[ -n "${ADB}" ]]; then
  ok "adb：${ADB}"
elif [[ "${AGENT}" == 1 || -n "${DEVICE}" ]]; then
  die "缺 adb：brew install --cask android-platform-tools"
else
  warn "没找到 adb，只能在 Mac 浏览器里预览；连音箱前先 brew install --cask android-platform-tools"
fi

step "准备 mibe（Codex 日志跟读）/ Preparing mibe"
if [[ ! -d "${MIBE}/.git" ]]; then
  git clone --quiet "${MIBE_URL}" "${MIBE}"
  git -C "${MIBE}" checkout --quiet "${MIBE_REF}"
  ok "已拉取 ${MIBE_REF:0:7}"
else
  ok "vendor/mibe 已存在，不动它的版本"
fi
# 先看「已经打过」，再看「能不能打」：两者都不行说明 vendor/mibe 被改过或版本不对
if git -C "${MIBE}" apply --reverse --check "${ROOT}/vendor/mibe.patch" 2>/dev/null; then
  ok "补丁已打过"
elif git -C "${MIBE}" apply --check "${ROOT}/vendor/mibe.patch" 2>/dev/null; then
  git -C "${MIBE}" apply "${ROOT}/vendor/mibe.patch"
  ok "已打补丁 vendor/mibe.patch"
else
  die "补丁打不上：vendor/mibe 有别的改动或版本不对。删掉 vendor/mibe 后重跑本脚本"
fi
(cd "${MIBE}" && uv sync --quiet)
ok "依赖已装到 vendor/mibe/.venv"

step "自检 / Self-test"
(cd "${ROOT}" && "${MIBE}/.venv/bin/python" -m unittest discover -s bridge -p 'test_*.py' 2>&1 | tail -1) || die "bridge 测试没通过"
(cd "${ROOT}" && /usr/bin/python3 -m unittest discover -s scripts -p 'test_adb_bridge.py' 2>&1 | tail -1) || die "adb-bridge 测试没通过"
/usr/bin/python3 "${ROOT}/scripts/adb-bridge.py" --self-test >/dev/null || die "adb-bridge 自检没通过"
ok "测试通过"

if [[ -n "${DEVICE}" ]]; then
  step "连接音箱 / Connecting to the speaker (${DEVICE})"
  "${ADB}" connect "${DEVICE}:5555" >/dev/null || true
  model="$("${ADB}" -s "${DEVICE}:5555" shell getprop ro.product.device 2>/dev/null | tr -d '\r' || true)"
  [[ "${model}" == "mi_lx04" ]] || die "${DEVICE}:5555 连不上或不是 LX04（读到「${model:-无}」）。确认音箱已刷机并打开无线 ADB"
  ok "是 LX04"
  # 复用 adb-bridge 自己的写法：改 endpoint 并钉住这台音箱的序列号，保留 sleep / mac_ip 等已有配置
  /usr/bin/python3 - "${ROOT}/scripts/adb-bridge.py" "${DEVICE}:5555" <<'PY'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("adb_bridge", sys.argv[1])
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)
bridge.save_endpoint(sys.argv[2], repin=True)  # 用户亲手指定的这台，重新钉住序列号
PY
  ok "已记住地址"
  browser="${LX04_BROWSER_PACKAGE:-mark.via}"
  if "${ADB}" -s "${DEVICE}:5555" shell pm list packages "${browser}" 2>/dev/null | grep -q "package:${browser}"; then
    ok "浏览器 ${browser} 已安装"
  else
    warn "音箱上没装 ${browser}：下载 Via 的 APK 后 adb -s ${DEVICE}:5555 install via.apk"
  fi
fi

if [[ "${AGENT}" == 1 ]]; then
  step "安装开机自启 / Installing LaunchAgents"
  "${ROOT}/scripts/install-agent.sh" >/dev/null
  # 服务刚起来要先扫一遍会话日志，给它几秒
  for _ in $(seq 1 15); do
    if curl -fsS -o /dev/null --max-time 2 "${STATUS_URL}api/status"; then break; fi
    sleep 1
  done
  if curl -fsS -o /dev/null --max-time 2 "${STATUS_URL}api/status"; then
    ok "状态服务已启动：${STATUS_URL}"
  else
    die "状态服务没起来，看日志：tail -f ~/Library/Logs/lx04-status.log"
  fi
fi

step "完成 / Done"
cat <<EOF
    Mac 上预览 / Preview on the Mac：${STATUS_URL}
    日志 / Logs：tail -f ~/Library/Logs/lx04-status.log ~/Library/Logs/lx04-adb-bridge.log
    夜间熄屏、指定 Mac 地址等配置 / Settings：~/Library/Application Support/lx04-status/adb-endpoint.json
EOF
if [[ "${AGENT}" == 0 ]]; then
  echo "    没装自启，前台运行 / Run in the foreground：./scripts/start.sh"
fi
