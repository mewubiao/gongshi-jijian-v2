#!/usr/bin/env bash
# 用 Tailwind 独立 CLI 编译 daisyUI 样式。不需要 Node。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TAILWIND_VERSION="v4.3.4"
DAISYUI_VERSION="5.7.50"
BIN="${ROOT}/var/tailwindcss/${TAILWIND_VERSION}/tailwindcss"
INPUT="${ROOT}/assets/css/input.css"
OUTPUT="${ROOT}/static/css/app.css"
PLUGIN_PKG="${ROOT}/assets/css/node_modules/daisyui/package.json"

if [[ ! -f "${PLUGIN_PKG}" ]]; then
  echo "缺少 ${PLUGIN_PKG}" >&2
  exit 1
fi

locked="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["version"])' "${PLUGIN_PKG}")"
if [[ "${locked}" != "${DAISYUI_VERSION}" ]]; then
  echo "daisyUI 版本不一致：package.json 是 ${locked}，脚本锁定 ${DAISYUI_VERSION}" >&2
  exit 1
fi

mkdir -p "$(dirname "${BIN}")" "$(dirname "${OUTPUT}")"

if [[ ! -x "${BIN}" ]]; then
  os="$(uname -s)"
  arch="$(uname -m)"
  case "${os}-${arch}" in
    Linux-x86_64) asset="tailwindcss-linux-x64" ;;
    Linux-aarch64 | Linux-arm64) asset="tailwindcss-linux-arm64" ;;
    Darwin-arm64) asset="tailwindcss-macos-arm64" ;;
    Darwin-x86_64) asset="tailwindcss-macos-x64" ;;
    *)
      echo "不支持的系统：${os} ${arch}。请看 docs/FRONTEND.md。" >&2
      exit 1
      ;;
  esac
  url="https://github.com/tailwindlabs/tailwindcss/releases/download/${TAILWIND_VERSION}/${asset}"
  tmp="${BIN}.download"
  curl -fsSL --retry 3 "${url}" -o "${tmp}"
  chmod +x "${tmp}"
  mv "${tmp}" "${BIN}"
fi

# 不使用 --minify：daisyUI 的 @scope 在压缩时会报空选择器警告，未压缩产物更稳。
"${BIN}" --input "${INPUT}" --output "${OUTPUT}" --silent
