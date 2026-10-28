#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

echo "============================================================"
echo "  bilibili-cheese-downloader  打包脚本 (macOS / Linux)"
echo "============================================================"
echo

PY="${PYTHON:-python3}"

if ! command -v "$PY" >/dev/null 2>&1; then
    echo "[!] 未检测到 Python，请先安装 Python 3.9-3.11"
    echo "    macOS:  brew install python@3.11"
    echo "    Linux:  sudo apt install python3 python3-pip"
    exit 1
fi

"$PY" -c "import sys; sys.exit(0 if (3,9) <= sys.version_info < (3,13) else 1)" 2>/dev/null || {
    echo "[!] 推荐 Python 3.9~3.12，当前版本："
    "$PY" --version
    echo "    3.13+ 或 3.7- 可能出现兼容问题，是否继续？ [y/N]"
    read -r ans
    [[ "$ans" =~ ^[Yy]$ ]] || exit 1
}

if ! command -v pyinstaller >/dev/null 2>&1; then
    echo "[*] 未检测到 PyInstaller，正在安装依赖..."
    "$PY" -m pip install --upgrade pip
    "$PY" -m pip install tqdm aiohttp bilibili-api-python pyinstaller
fi

echo
echo "[*] 开始打包（首次约 1-3 分钟）..."
echo
"$PY" build_exe.py

if [ -x "dist/bilibili-cheese-downloader" ]; then
    sz=$(ls -l "dist/bilibili-cheese-downloader" | awk '{print $5}')
    echo
    echo "[+] 打包成功！"
    echo "    文件大小: $sz 字节"
    echo "    文件位置: $(pwd)/dist/bilibili-cheese-downloader"
    file "dist/bilibili-cheese-downloader"
else
    echo "[!] 未找到输出文件，请检查上方日志"
    exit 1
fi
