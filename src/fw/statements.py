"""原始报表 -> canonical 年报序列。

东财按报告期接口返回的是混合频率的长表（实测首行是 2026-06-30 中报），
所以第一件事是筛年报。国内4号在这点上是错的：它把累计口径的 statDate 行
当季度趋势画（data_fetcher.py:237-259），导致 Q4 = 全年值。
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .fields import AUDIT_OPINION_COLUMN, BALANCE_MAP, CASH_MAP, PROFIT_MAP


def _canonical(df: pd.DataFrame, colmap: dict[str, str], table: str) -> pd.DataFrame:
    period = pd.to_datetime(df["REPORT_DATE"], errors="coerce")
    out = pd.DataFrame(index=df.index)
    out["report_date"] = period
    out["year"] = period.dt.year
    notice = df.get(colmap.get("notice_date", "NOTICE_DATE"))
    out["notice_date"] = pd.to_datetime(notice, errors="coerce")

    available = set(df.columns)
    unmatched: list[str] = []
    for canonical, want in colmap.items():
        if canonical == "notice_date":
            continue
        candidates = want if isinstance(want, tuple) else (want,)
        col = None
        for cand in candidates:
            col = cand if cand in available else next(
                (c for c in df.columns if c.upper() == cand.upper()), None)
            if col:
                break
        if col is None:
            # 不报错：金融业没有营业成本、没有流动资产负债概念，缺失是行业差异不是故障。
            out[canonical] = pd.NA
            unmatched.append(canonical)
            continue
        out[canonical] = pd.to_numeric(df[col], errors="coerce")

    # 只有当收入、总资产、归母权益同时取不到时才判定接口变更——单科目缺失是正常现象
    if {"revenue", "total_assets", "parent_profit"} <= set(unmatched) or "parent_profit" in unmatched:
        raise KeyError(
            f"{table} 关键科目全部未命中（{unmatched}）——东财列名可能已变更，去更新 fields.py"
        )

    # 审计意见不是数值科目，单独透传，否则 canonical 帧里取不到
    if AUDIT_OPINION_COLUMN in available:
        out[AUDIT_OPINION_COLUMN] = df[AUDIT_OPINION_COLUMN].astype("string")
    return out


def annual_only(df: pd.DataFrame) -> pd.DataFrame:
    """只保留 12-31 报告期，按年份升序。

    升序是刻意的：平均权益/平均资产要算 t 与 t-1，升序下 .shift(1) 才是「上年」。
    """
    mask = df["report_date"].dt.month.eq(12) & df["report_date"].dt.day.eq(31)
    out = df.loc[mask].drop_duplicates(subset="year", keep="first")
    return out.sort_values("year").reset_index(drop=True)


def _mean_of_two(cur: pd.Series, prev: pd.Series) -> pd.Series:
    return (cur + prev) / 2


@dataclass
class Statements:
    code: str
    annual: pd.DataFrame      # index = year（int），列 = canonical 字段
    name: str = ""
    raw_opinion: pd.Series | None = None

    def year(self, y: int) -> pd.Series | None:
        if y not in self.annual.index:
            return None
        return self.annual.loc[y]

    def latest_year(self) -> int:
        return int(self.annual.index.max())

    def years(self) -> list[int]:
        return [int(v) for v in self.annual.index.tolist()]


def build(code: str, tables: dict[str, pd.DataFrame]) -> Statements:
    profit = _canonical(tables["profit"], PROFIT_MAP, "利润表")
    balance = _canonical(tables["balance"], BALANCE_MAP, "资产负债表")
    cash = _canonical(tables["cash"], CASH_MAP, "现金流量表")

    a_p, a_b, a_c = (annual_only(x) for x in (profit, balance, cash))

    ann = a_b.merge(a_p.drop(columns=["report_date", "notice_date"]), on="year", how="left")
    ann = ann.merge(a_c.drop(columns=["report_date", "notice_date"]), on="year", how="left")
    ann = ann.set_index("year").sort_index()

    # 有息负债合并口径：国内1号与国际3号都只看长期借款，A股会低估去杠杆幅度
    debt_cols = ["short_loan", "liab_1year", "long_loan", "bond_payable"]
    ann["interest_bearing_debt"] = ann[debt_cols].sum(axis=1, min_count=1)

    # 期初值：升序下 shift(1) 就是上年年末
    for col, name in (("total_assets", "avg_total_assets"), ("parent_equity", "avg_parent_equity")):
        ann[name] = _mean_of_two(ann[col], ann[col].shift(1))
    ann["beg_total_assets"] = ann["total_assets"].shift(1)

    # 自由现金流。东财表里没有现成的 FREE_CASHFLOW 列（实测未命中），必须自算
    ann["free_cash_flow"] = ann["ocf"] - ann["capex"]

    # 审计意见直接嵌在报表列里（实测 OPINION_TYPE 有「标准无保留意见」「未经审计」），
    # 不需要额外的审计接口。国内1号的 Rule 0.2 依赖 Tushare fina_audit，
    # 在 akshare 侧此前被认为无等价物——实际三表自带。
    opinion = None
    for src in (profit, balance, cash):
        if AUDIT_OPINION_COLUMN in src.columns:
            opinion = annual_only(src).set_index("year")[AUDIT_OPINION_COLUMN]
            break

    name = ""
    # 名称在原始报表里，canonical 帧不带出，所以遍历 tables 而不是 profit/balance/cash
    for raw in tables.values():
        col = next((c for c in raw.columns if c.upper() == "SECURITY_NAME_ABBR"), None)
        if col is not None and len(raw[col].dropna()):
            name = str(raw[col].dropna().iloc[0])
            break

    return Statements(
        code=str(code).zfill(6), annual=ann, name=name, raw_opinion=opinion
    )
