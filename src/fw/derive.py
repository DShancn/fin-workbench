"""派生指标。

规则文件引用的 field 全部在这里定义，一次算清，规则层不做算术。
这样规则文件里的每个阈值都能追溯到唯一一个公式，注释面板可以直接展示。
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from . import ratios


@dataclass(frozen=True)
class FieldMeta:
    label: str
    unit: str        # ratio / pct / times / cny / str
    formula: str
    group: str


# 注释面板的「这个数怎么来的」全部来自这里，规则文件只出阈值不出公式。
FIELD_META: dict[str, FieldMeta] = {
    "audit_opinion": FieldMeta("审计意见", "str", "审计报告 · 审计意见段", "报表可信度"),
    "deducted_gap_ratio": FieldMeta(
        "扣非/归母净利比", "ratio", "扣非归母净利润 ÷ 归母净利润", "盈利质量"),
    "goodwill_ratio": FieldMeta("商誉/归母净资产", "ratio", "商誉 ÷ 归母权益", "资产质量"),
    "debt_to_assets": FieldMeta("资产负债率", "ratio", "负债合计 ÷ 资产总计", "杠杆"),
    "interest_bearing_debt_ratio": FieldMeta(
        "有息负债率", "ratio",
        "(短期借款+一年内到期非流动负债+长期借款+应付债券) ÷ 资产总计", "杠杆"),
    "equity_multiplier": FieldMeta(
        "权益乘数", "times", "平均总资产 ÷ 平均归母权益", "杠杆"),
    "ocf_to_profit": FieldMeta(
        "经营现金流/归母净利", "ratio", "经营活动现金流量净额 ÷ 归母净利润", "现金流质量"),
    "free_cash_flow": FieldMeta(
        "自由现金流", "cny", "经营现金流净额 − 购建长期资产支付现金", "现金流质量"),
    "gross_margin": FieldMeta("毛利率", "ratio", "(营业总收入 − 营业成本) ÷ 营业总收入", "盈利"),
    "net_profit_margin": FieldMeta("销售净利率", "ratio", "净利润 ÷ 营业总收入", "盈利"),
    "asset_turnover": FieldMeta("总资产周转率", "times", "营业总收入 ÷ 平均总资产", "营运"),
    "return_on_equity": FieldMeta("ROE", "ratio", "净利润 ÷ 平均归母权益", "盈利"),
    "current_ratio": FieldMeta("流动比率", "times", "流动资产 ÷ 流动负债", "偿债"),
    "inventory_turnover": FieldMeta("存货周转率", "times", "营业成本 ÷ 存货", "营运"),
    "revenue_yoy": FieldMeta("营收同比", "ratio", "本年营收 ÷ 上年营收 − 1", "成长"),
    "parent_profit_yoy": FieldMeta("归母净利同比", "ratio", "本年归母 ÷ 上年归母 − 1", "成长"),
    "receivable_revenue_gap": FieldMeta(
        "应收−营收增速差", "ratio", "应收账款同比 − 营业总收入同比", "资产质量"),
    "inventory_revenue_gap": FieldMeta(
        "存货−营收增速差", "ratio", "存货同比 − 营业总收入同比", "资产质量"),
}

# 注释面板展示「这个数由哪些原值算出来」，让人能自己复核而不只是相信工具。
COMPONENTS: dict[str, tuple[str, ...]] = {
    "deducted_gap_ratio": ("deduct_profit", "parent_profit"),
    "goodwill_ratio": ("goodwill", "parent_equity"),
    "debt_to_assets": ("total_liab", "total_assets"),
    "interest_bearing_debt_ratio": ("interest_bearing_debt", "total_assets"),
    "equity_multiplier": ("avg_total_assets", "avg_parent_equity"),
    "ocf_to_profit": ("ocf", "parent_profit"),
    "gross_margin": ("revenue", "net_cogs"),
    "net_profit_margin": ("net_profit", "revenue"),
    "asset_turnover": ("revenue", "avg_total_assets"),
    "return_on_equity": ("net_profit", "avg_parent_equity"),
    "current_ratio": ("current_assets", "current_liab"),
    "inventory_turnover": ("net_cogs", "inventory"),
    "receivable_revenue_gap": ("accounts_rece", "revenue"),
    "inventory_revenue_gap": ("inventory", "revenue"),
}


def _num(v):
    """NaN 一律归为 None。

    这是本模块最容易出错的一处：pandas 的 NaN 不是 None，会照常通过
    `value is None` 的缺失检查，然后 `nan > 0.5` 恒为假，于是**缺数据的指标
    被判成 PASS**。商誉科目对不做大并购的公司就是空值，正好命中这条。
    """
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else f


def _safe_div(a, b):
    a, b = _num(a), _num(b)
    if a is None or b is None or b == 0:
        return None
    out = a / b
    return None if pd.isna(out) else out


def _yoy(cur, prev):
    """同比增速。基期为 0 或负时无意义，返回 None 而不是硬算出 999%。"""
    cur, prev = _num(cur), _num(prev)
    if cur is None or prev is None or prev <= 0:
        return None
    return cur / prev - 1


# 透传给规则引擎与 F-Score 的原始额，避免下游各自再去取报表行
PASSTHROUGH = (
    "revenue", "net_cogs", "net_profit", "parent_profit", "deduct_profit",
    "operate_profit", "total_profit",
    "total_assets", "avg_total_assets", "beg_total_assets", "total_liab",
    "parent_equity", "avg_parent_equity", "current_assets", "current_liab",
    "interest_bearing_debt", "share_capital", "goodwill", "accounts_rece",
    "inventory", "fixed_asset", "undistributed_profit",
    "ocf", "icf", "fcf_fin", "capex", "end_cash", "free_cash_flow",
    "rd_expense", "sell_expense", "manage_expense", "finance_expense",
    "basic_eps", "diluted_eps",
)


def derive(cur, prev, opinion: str | None = None) -> dict:
    """cur / prev 为 canonical 年报行。prev 可为 None（最早一年）。"""
    def g(k):
        return _num(cur[k]) if k in cur else None

    def gp(k):
        return _num(prev[k]) if prev is not None and k in prev else None

    revenue, cogs = g("revenue"), g("net_cogs")
    parent, deduct = g("parent_profit"), g("deduct_profit")
    assets, equity = g("total_assets"), g("parent_equity")
    avg_assets, avg_equity = g("avg_total_assets"), g("avg_parent_equity")

    d = {k: g(k) for k in PASSTHROUGH}
    d.update({
        # 盈利质量
        "audit_opinion": opinion or "未披露",
        "deducted_gap_ratio": _safe_div(deduct, parent),
        # 杠杆与结构
        "goodwill_ratio": _safe_div(g("goodwill"), equity),
        "debt_to_assets": _safe_div(g("total_liab"), assets),
        "interest_bearing_debt_ratio": _safe_div(g("interest_bearing_debt"), assets),
        "equity_multiplier": _safe_div(avg_assets, avg_equity),
        # 现金流质量
        "ocf_to_profit": _safe_div(g("ocf"), parent),
        "free_cash_flow": g("free_cash_flow"),
        # 营运效率
        "gross_margin": ratios.gross_margin(revenue, cogs),
        "net_profit_margin": ratios.net_profit_margin(g("net_profit"), revenue),
        "asset_turnover": ratios.asset_turnover(revenue, avg_assets),
        "return_on_equity": ratios.return_on_equity(g("net_profit"), avg_equity),
        "current_ratio": _safe_div(g("current_assets"), g("current_liab")),
        "inventory_turnover": _safe_div(cogs, g("inventory")),
        # 增速与背离
        "revenue_yoy": _yoy(revenue, gp("revenue")),
        "parent_profit_yoy": _yoy(parent, gp("parent_profit")),
        "receivable_revenue_gap": None,
        "inventory_revenue_gap": None,
    })

    rev_yoy = d["revenue_yoy"]
    for src, dst in (("accounts_rece", "receivable_revenue_gap"), ("inventory", "inventory_revenue_gap")):
        item_yoy = _yoy(g(src), gp(src))
        d[dst] = None if (item_yoy is None or rev_yoy is None) else item_yoy - rev_yoy

    d["dupont"] = ratios.dupont_analysis(g("net_profit"), revenue, avg_assets, avg_equity)
    d["dupont_prev"] = (
        ratios.dupont_analysis(gp("net_profit"), gp("revenue"), gp("avg_total_assets"), gp("avg_parent_equity"))
        if prev is not None
        else {}
    )
    if d["dupont_prev"] and d["dupont"]:
        d["dupont_attribution"] = ratios.dupont_attribution(d["dupont_prev"], d["dupont"])
    return d
