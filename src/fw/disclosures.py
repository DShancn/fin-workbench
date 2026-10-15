"""巨潮公告检索：把「指标注释」接到「原始披露文件」上。

这个模块存在的意义是让工具可被证伪——每个判定旁边都应该能翻到公司自己
公告的原文，而不是只能相信工具算出来的数。

实测（2026-10-15）：
- orgId 必须用 topSearch 解析，不能按代码前缀猜。600519 -> gssh0600519，
  但 603533 -> 9900033063 是纯数字；猜错会 HTTP 200 且 announcements 为空，
  不报错。
- announcementTime 是 Unix 毫秒，但对多数公告只表示日级（当地零点），
  必须带 ts_precision，否则跨日/同日的先后判断会失真。
- adjunctUrl 拼 static 域名即 PDF 直链；akshare 的同类封装只给详情页链接，
  要多跳一次且丢掉时间精度。
"""

from __future__ import annotations

import re
import time
from datetime import datetime

import requests

from . import config

config.apply_proxy_bypass()

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/117.0.0.0 Safari/537.36"
QUERY = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
TOPSEARCH = "http://www.cninfo.com.cn/new/information/topSearch/query"
PDF_BASE = "https://static.cninfo.com.cn/"

# 巨潮的板块列：沪市用 sse，深市用 szse，北交所用 bj。取值不影响命中，
# 但缺失会让部分接口返回空，所以按代码前缀显式给一个。
_COLUMN = {"6": "sse", "9": "sse", "0": "szse", "3": "szse", "4": "bj", "8": "bj"}

# 定期报告类别
PERIODIC = {
    "年度报告": "category_ndbg_szsh",
    "半年度报告": "category_bndbg_szsh",
    "一季度报告": "category_yjdbg_szsh",
    "三季度报告": "category_sjdbg_szsh",
}

# 风险信号关键词。这些不是「类别」而是标题特征，靠 searchkey 命中。
# 选它们的理由：都是产业资本或监管对基本面的反向表态，与二级市场情绪经常相反。
RISK_KEYWORDS = ("减持", "异常波动", "问询", "立案", "处罚", "业绩预告", "澄清")

_org_cache: dict[str, str] = {}


def _post(url: str, data: dict, referer: str) -> dict:
    last: Exception | None = None
    for i in range(3):
        try:
            r = requests.post(
                url,
                data=data,
                headers={
                    "User-Agent": UA,
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Referer": referer,
                    "Origin": "https://www.cninfo.com.cn",
                },
                timeout=20,
            )
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            last = exc
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"巨潮请求失败（重试 3 次）: {last}")


def org_id(code: str) -> str:
    code = str(code).zfill(6)
    if code in _org_cache:
        return _org_cache[code]
    rows = _post(
        TOPSEARCH,
        {"keyWord": code, "maxSecNum": 10, "maxListNum": 5},
        "https://www.cninfo.com.cn/new/index",
    )
    hit = next(
        (x.get("orgId") for x in rows if str(x.get("code", "")).zfill(6) == code and x.get("orgId")),
        "",
    )
    if not hit:
        raise RuntimeError(f"巨潮 topSearch 未返回 {code} 的 orgId，拒绝用前缀猜测")
    _org_cache[code] = hit
    return hit


_TITLE_YEAR = re.compile(r"(20\d{2})\s*年")


def _report_year(title: str):
    """从标题取报告期年份。

    不能用「披露年 − 1」推断：年报确实次年披露，但半年报与三季报是当年披露，
    预告类更是可能提前一年。标题里的「YYYY年」才是报告期本身。
    """
    m = _TITLE_YEAR.search(title or "")
    return int(m.group(1)) if m else None


def _rows(code: str, raw: dict) -> list[dict]:
    out = []
    for a in raw.get("announcements") or []:
        ms = a.get("announcementTime")
        dt = datetime.fromtimestamp(ms / 1000, tz=config.CN_TZ) if ms else None
        ts = dt.strftime("%Y-%m-%d %H:%M:%S") if dt else ""
        precision = "unknown"
        if dt:
            precision = "day" if (dt.hour, dt.minute, dt.second) == (0, 0, 0) else "second"
        adjunct = a.get("adjunctUrl") or ""
        out.append(
            {
                "code": code,
                "anno_id": str(a.get("announcementId", "")),
                "title": str(a.get("announcementTitle", "")).replace("<em>", "").replace("</em>", ""),
                "publish_ts": ts,
                "ts_precision": precision,
                "pdf_url": f"{PDF_BASE}{adjunct}" if adjunct else "",
                "report_year": None,
            }
        )
    return out


def _query(code: str, oid: str, category: str = "", searchkey: str = "",
           se_date: str = "", page_size: int = 30) -> list[dict]:
    return _rows(
        code,
        _post(
            QUERY,
            {
                "stock": f"{code},{oid}",
                "tabName": "fulltext",
                "pageSize": str(page_size),
                "pageNum": "1",
                "column": _COLUMN.get(code[0], "szse"),
                "category": category,
                "plate": "",
                "seDate": se_date,
                "searchkey": searchkey,
                "secid": "",
                "sortName": "",
                "sortType": "",
                "isHLtitle": "true",
            },
            "https://www.cninfo.com.cn/new/disclosure",
        ),
    )


def fetch(code: str, years_back: int = 5) -> dict[str, list[dict]]:
    """取定期报告 + 风险信号公告。

    返回 {"periodic": [...], "risk": [...]}。取不到就抛错——
    返回空列表与「这家公司确实没有减持公告」在界面上是两种完全不同的结论。
    """
    code = str(code).zfill(6)
    oid = org_id(code)
    now = datetime.now(config.CN_TZ).year

    periodic: list[dict] = []
    for label, cat in PERIODIC.items():
        for y in range(now - years_back, now + 1):
            rows = _query(code, oid, category=cat, se_date=f"{y}-01-01~{y}-12-31", page_size=20)
            for r in rows:
                # 只留正文全文：摘要与英文版是同一份信息的重复载体，占位不增值
                if "摘要" in r["title"] or "英文版" in r["title"] or "已取消" in r["title"]:
                    continue
                r["kind"] = label
                r["report_year"] = _report_year(r["title"])
                periodic.append(r)
                break

    risk: list[dict] = []
    start = now - min(years_back, 3)
    for kw in RISK_KEYWORDS:
        rows = _query(code, oid, searchkey=kw, se_date=f"{start}-01-01~{now}-12-31", page_size=15)
        for r in rows:
            r["kind"] = kw
        risk.extend(rows)

    seen, deduped = set(), []
    for r in sorted(risk, key=lambda x: x["publish_ts"], reverse=True):
        if r["anno_id"] in seen:
            continue
        seen.add(r["anno_id"])
        deduped.append(r)

    return {"periodic": periodic, "risk": deduped}
