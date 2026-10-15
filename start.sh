#!/usr/bin/env bash
# 一键启动财报标注工作台：首次会自动建环境、装依赖、取数，然后开服务并弹浏览器。
# 之后每次运行只做「起服务 + 打开页面」，秒级。
#
#   ./start.sh              正常启动
#   ./start.sh --fetch      启动前先增量取数
#   ./start.sh --report     不起服务，只导出离线 HTML 报告到 data/reports/
set -euo pipefail
cd "$(dirname "$0")"

PY_MIN=311   # 需要 Python >= 3.11
PORT="${FW_PORT:-8501}"
VENV=.venv
# 裸 python3 在 macOS 上常是系统 3.9，装不上本项目；优先用找得到的新版本
for c in python3.13 python3.12 python3.11 python3; do
  if command -v "$c" >/dev/null 2>&1 \
     && "$c" -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3,11) else 1)" 2>/dev/null; then
    PY=$c; break
  fi
done
if [ -z "${PY:-}" ]; then
  echo "找不到 Python >= 3.11（只找到：$(command -v python3 || echo 无)）" >&2
  echo "用 uv 装一个最省事：curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
  exit 1
fi

if [ ! -d "$VENV" ]; then
  echo "首次运行：创建虚拟环境并安装依赖（约 1-2 分钟）"
  "$PY" -m venv "$VENV"
  "$VENV/bin/pip" install -q --upgrade pip
  "$VENV/bin/pip" install -q -e .
fi

case "${1:-}" in
  --report)
    mkdir -p data
    "$VENV/bin/python" -m fw.report --all
    echo "报告目录：$(pwd)/data/reports   双击 index.html 即可离线浏览"
    exit 0 ;;
esac

if [ "${1:-}" = "--fetch" ]; then
  echo "增量取数中（每家约 25 秒）"
  "$VENV/bin/python" -m fw.fetch --queue
fi

# data/ 是 gitignore 的，首次 clone 后并不存在；下面所有重定向和写库都依赖它
mkdir -p data

if [ ! -f data/workbench.duckdb ]; then
  echo "库里还没有数据，先取自选池（watchlist.csv 不存在时用示例）"
  [ -f watchlist.csv ] || cp watchlist.example.csv watchlist.csv
  "$VENV/bin/python" -m fw.fetch --queue || true
fi

URL="http://localhost:$PORT"
HEALTH="$URL/_stcore/health"

open_url() {
  case "$(uname -s)" in
    Darwin) open "$1" ;;
    Linux)  command -v xdg-open >/dev/null 2>&1 && xdg-open "$1" || echo "手动打开 $1" ;;
    *)      echo "手动打开 $1（Windows 下直接在浏览器输入）" ;;
  esac
}

# 已经在跑就不要再起一个：streamlit 撞端口会直接退出，但旧进程仍会答 health，
# 那时脚本报「已启动」而新进程其实死了——所以先探活，再决定要不要 spawn。
# -f 很重要：不带它的话 curl 对 404/503 也返回 0，探活等于没探。
# 故意不加 -S：轮询期间连不上是预期内的，不该每次都往终端喷错误。
if curl -fs -o /dev/null "$HEALTH"; then
  echo "界面已经在 $URL 运行，直接打开。重启请先：pkill -f 'streamlit run app/main.py'"
  open_url "$URL"
  exit 0
fi

echo "启动界面 → $URL"
nohup "$VENV/bin/streamlit" run app/main.py --server.port "$PORT" --server.headless true \
  > data/streamlit.log 2>&1 &
SERVER=$!

# 等服务真的能接连接，而不是假设它 2 秒内一定起来
for _ in $(seq 1 40); do
  curl -fs -o /dev/null "$HEALTH" && break
  sleep 0.5
done
if ! curl -fs -o /dev/null "$HEALTH"; then
  echo "20 秒内没起来，看日志：data/streamlit.log" >&2
  tail -5 data/streamlit.log >&2 || true
  exit 1
fi

open_url "$URL"

echo "已启动（进程 $SERVER）。停止：pkill -f 'streamlit run app/main.py'"
echo "日志：data/streamlit.log"
