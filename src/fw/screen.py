"""筛选与对比的共同底座：把每家公司的卡片压成一行。

筛选和对比不是两套逻辑，是同一张矩阵的两种用法——
筛选是「按列排序 + 过滤行」，对比是「选定行子集后看列」。
所以这里只产出矩阵，界面不复算任何指标。
"""

from __future__ import annotations

import pandas as pd

from . import registry, scoring, store

# (列名, 指标 key, 是否按亿缩放)
RATIO_COLS = [
    ("毛利率", "gross_margin", False),
    ("净利率", "net_profit_margin", False),
    ("ROE", "return_on_equity", False),
    ("周转率", "asset_turnover", False),
    ("权益乘数", "equity_multiplier", False),
    ("现金流/利润", "ocf_to_profit", False),
    ("资产负债率", "debt_to_assets", False),
    ("有息负债率", "interest_bearing_debt_ratio", False),
    ("商誉/净资产", "goodwill_ratio", False),
    ("扣非/归母", "deducted_gap_ratio", False),
    ("流动比率", "current_ratio", False),
    ("应收-营收差", "receivable_revenue_gap", False),
]

AMOUNT_COLS = [("营收(亿)", "revenue"), ("归母净利(亿)", "parent_profit"), ("经营现金流(亿)", "ocf")]


def _value(card, key):
    for m in card.metrics:
        if m.key == key:
            return m.value if m.unit != "str" else None
    return None


def one_row(conn, code: str, rules, notes) -> dict | None:
    try:
        card = registry.build_card(store.load(conn, code), rules, notes)
    except (KeyError, ValueError):
        return None

    risk = card.risk or {}
    vetoed = bool(risk.get("veto"))
    row = {
        "代码": card.code,
        "名称": card.name,
        "年度": card.year,
        # 命中一票否决时分数作废：被排除的公司与健康的公司共用一把 0-100 的尺子，
        # 排序会把 76 分的「排除」放到 77 分的「良」旁边，等于把否决结论稀释掉了。
        "风险分": None if vetoed else risk.get("score"),
        "评级": risk.get("band", "—"),
        "评分覆盖": risk.get("coverage") if not vetoed else None,
        "短板": None if vetoed else (scoring.worst_group(risk) if risk else None),
        "通过": card.summary["pass"],
        "关注": card.summary["warn"],
        "警示": card.summary["fail"],
        "无法判定": card.summary["na"],
        "一票否决": "是" if card.veto else "",
        "否决项": "、".join(m.label for m in card.veto),
        "F-Score": card.fscore_summary["score"],
        "F覆盖": f"{card.fscore_summary['judged']}/9",
    }
    for label, key in AMOUNT_COLS:
        row[label] = _value(card, key)
    for label, key, _ in RATIO_COLS:
        row[label] = _value(card, key)
    return row


def matrix(conn, rules, notes, codes: list[str] | None = None,
           groups: dict[str, list[str]] | None = None) -> pd.DataFrame:
    if codes is None:
        codes = store.list_codes(conn)["code"].tolist()

    group_of = {c: g for g, lst in (groups or {}).items() for c in lst}
    rows = [r for r in (one_row(conn, c, rules, notes) for c in codes) if r]
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df.insert(2, "分组", df["代码"].map(lambda c: group_of.get(str(c), "未分组")))
    # 金额列在 Metric 里已按亿存储，比率列保持小数
    return df.set_index("代码")
