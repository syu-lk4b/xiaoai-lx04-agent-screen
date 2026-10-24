#!/bin/bash
# 用无头 Chrome 把 backgrounds.html 渲染成 display/night.jpg 和 display/dusk.jpg。
# 用法：./scripts/render-backgrounds.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CHROME="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT

if [[ ! -x "${CHROME}" ]]; then
  echo "[backgrounds] 找不到 Chrome，用 CHROME=/path/to/chrome 指定" >&2
  exit 1
fi

for scene in night dusk; do
  # 设备像素比固定为 1：图要和 800×480 的屏一比一
  "${CHROME}" --headless=new --disable-gpu --hide-scrollbars --force-device-scale-factor=1 \
    --window-size=800,480 --allow-file-access-from-files --user-data-dir="${TMP}/profile" \
    --screenshot="${TMP}/${scene}.png" "file://${ROOT}/scripts/backgrounds.html#${scene}" 2>/dev/null
  sips -s format jpeg -s formatOptions 86 "${TMP}/${scene}.png" --out "${ROOT}/display/${scene}.jpg" >/dev/null
  echo "[backgrounds] display/${scene}.jpg"
done
