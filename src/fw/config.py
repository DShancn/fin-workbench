"""全局配置。

三个跨机器可移植性约束，都是 vest 项目里踩过的真实坑，不是预防性设计：
时区、数据库锚定、代理绕过。
"""

from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

# A股全部时间语义（开盘收盘、报告期、公告日切）都是北京时间。
# 裸 datetime.fromtimestamp() 按宿主机时区解释，会把日级精度的公告挪到前一天，
# 让「先后不可判定」误判成「早于开盘」。
CN_TZ = ZoneInfo("Asia/Shanghai")

# 境内金融数据源不应经由科学上网代理出去：代理会在 TLS 握手中途截断连接，
# 表现为随机 SSLEOFError / read timeout，且错误信息与真实原因毫无关系。
# macOS 的 urllib.request.getproxies() 会读系统代理，所以必须显式绕过。
CN_HOSTS = (
    "eastmoney.com",
    "emweb.securities.eastmoney.com",
    "push2his.eastmoney.com",
    "search-api-web.eastmoney.com",
    "cninfo.com.cn",
    "static.cninfo.com.cn",
    "sina.com.cn",
    "hq.sinajs.cn",
    "szse.cn",
    "sse.com.cn",
    "10jqka.com.cn",
    "zx.10jqka.com.cn",
    "dfcfw.com",
    "xueqiu.com",
    "stockdata.ironfish.cn",
)


def apply_proxy_bypass() -> None:
    for var in ("NO_PROXY", "no_proxy"):
        existing = os.environ.get(var, "")
        merged = ",".join(filter(None, [existing, *CN_HOSTS]))
        os.environ[var] = merged


def today_cn() -> str:
    from datetime import datetime

    return datetime.now(CN_TZ).strftime("%Y-%m-%d")


def data_dir() -> Path:
    root = Path(__file__).resolve().parents[2]
    if (root / "pyproject.toml").exists():
        return root / "data"
    return Path.home() / ".fin-workbench"


DB_PATH = Path(os.environ.get("FW_DB", str(data_dir() / "workbench.duckdb")))

# 自选池。与 vest 的 followup_queue 合并后构成实际取数范围。
WATCHLIST_FILE = Path(os.environ.get("FW_WATCHLIST", str(data_dir() / "watchlist.csv")))
VEST_DB = Path(os.environ.get("VEST_DB_PATH", "")) if os.environ.get("VEST_DB_PATH") else None
