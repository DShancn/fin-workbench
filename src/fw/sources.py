"""akshare 取数层。

成本约束（实测）：东财按报告期接口内部按年分页，单家公司一张表约 21 个子请求、
耗时 40 秒级。三表一家约 2 分钟。所以取数必须落库、增量更新，
绝不能让 Streamlit 页面直接调用这里——那会让每次点击都卡两分钟。
"""

from __future__ import annotations

import sys
import time

import pandas as pd

from . import config

config.apply_proxy_bypass()

import akshare as ak  # noqa: E402  必须在代理绕过之后导入

_ENDPOINTS = {
    "profit": ak.stock_profit_sheet_by_report_em,
    "balance": ak.stock_balance_sheet_by_report_em,
    "cash": ak.stock_cash_flow_sheet_by_report_em,
}

_RETRIES = 3


def to_symbol(code: str) -> str:
    """6 位代码 -> 东财带市场前缀符号。"""
    code = str(code).strip().zfill(6)
    if code[0] == "6":
        return f"sh{code}"
    if code[0] in "03":
        return f"sz{code}"
    if code[0] in "48":
        return f"bj{code}"
    return f"sh{code}"


def fetch_statement(statement: str, code: str) -> pd.DataFrame:
    """拉一张表。失败时显式抛错，不返回空表。

    返回空 DataFrame 与「这家公司真的没有该表」在下游不可区分——
    那会让规则引擎安静地给出「全部通过」的错误结论。
    """
    if statement not in _ENDPOINTS:
        raise ValueError(f"未知报表类型 {statement!r}，可选 {list(_ENDPOINTS)}")
    symbol = to_symbol(code)
    last: Exception | None = None
    for attempt in range(_RETRIES):
        try:
            df = _ENDPOINTS[statement](symbol=symbol)
        except Exception as exc:  # 网络层异常类型多，代理截断表现为 SSLEOFError
            last = exc
            time.sleep(2 * (attempt + 1))
            continue
        if df is None or df.empty:
            raise RuntimeError(f"{symbol} {statement} 返回空表，疑似接口变更或该代码无数据")
        return df
    raise RuntimeError(f"{symbol} {statement} 连续 {_RETRIES} 次取数失败: {last}")


def fetch_all(code: str) -> dict[str, pd.DataFrame]:
    """取三表。任一失败整体抛错——半套数据算出来的比率会误导。"""
    return {name: fetch_statement(name, code) for name in _ENDPOINTS}


def fetch_industry_members(industry: str) -> list[str]:
    """取某行业成分股代码，供同业对比用。"""
    config.apply_proxy_bypass()
    df = ak.stock_board_industry_cons_em(symbol=industry)
    return [str(c).zfill(6) for c in df["代码"].tolist()]
