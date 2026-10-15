"""财务比率计算。

下列四个函数的公式与口径取自 FinanceToolkit 2.2.3（JerBouma, MIT License），
按源文件行号对应：
  gross_margin          financetoolkit/ratios/profitability_model.py:9
  return_on_equity      financetoolkit/ratios/profitability_model.py:239
  asset_turnover        financetoolkit/ratios/efficiency_model.py:9
  dupont_analysis       financetoolkit/models/dupont_model.py:10
  平均权益/资产口径      financetoolkit/ratios/ratios_controller.py:4740, 845-847
  COGS=0 掩蔽           financetoolkit/ratios/ratios_controller.py:3892-3894

不 pip install 那个包，只搬这几个函数，理由是它要求 pandas>=3.0 且 Python>=3.11
（pyproject.toml:43,45），且 ratios 属性会无条件尝试联网取历史价
（toolkit_controller.py:597-600）。公式是无量纲除法，自己实现约 60 行。

MIT 义务：本文件头保留上述版权声明，再分发时须一并带上。
"""

from __future__ import annotations

import pandas as pd


def _zero_or_nan(v) -> bool:
    return v is None or pd.isna(v) or v == 0


def gross_margin(revenue, cost_of_goods_sold):
    """毛利率 = (营业收入 − 营业成本) / 营业收入。

    银行、保险、券商没有营业成本概念（实测 600519 有，但金融业会缺），
    此时结果无意义，置 None 而不是算出 100%——FinanceToolkit 用 mask 掩蔽，
    这里等价处理。
    """
    if _zero_or_nan(revenue) or _zero_or_nan(cost_of_goods_sold):
        return None
    return (revenue - cost_of_goods_sold) / revenue


def return_on_equity(net_income, average_equity):
    """ROE = 净利润 / 平均归母股东权益。

    分母必须用**期初期末均值**而非期末值。这是最容易被偷懒的一处：
    用期末权益会让刚大额融资或大额分红的公司 ROE 剧烈失真。
    """
    if _zero_or_nan(net_income) or _zero_or_nan(average_equity) or average_equity <= 0:
        return None
    return net_income / average_equity


def asset_turnover(revenue, average_total_assets):
    """总资产周转率 = 营业收入 / 平均总资产。"""
    if _zero_or_nan(revenue) or _zero_or_nan(average_total_assets) or average_total_assets <= 0:
        return None
    return revenue / average_total_assets


def net_profit_margin(net_income, revenue):
    """销售净利率。非外来函数，杜邦拆解需要。"""
    if _zero_or_nan(net_income) or _zero_or_nan(revenue):
        return None
    return net_income / revenue


def equity_multiplier(total_assets, average_equity):
    """权益乘数 = 平均总资产 / 平均权益。"""
    if _zero_or_nan(total_assets) or _zero_or_nan(average_equity) or average_equity <= 0:
        return None
    return total_assets / average_equity


def dupont_analysis(net_income, revenue, average_total_assets, average_equity) -> dict:
    """三层杜邦：ROE = 销售净利率 × 总资产周转率 × 权益乘数。

    返回分量而不只是 ROE——「靠什么赚钱」的信息全在分量里。
    归因（哪个分量主导了 ROE 变动）由 dupont_attribution 做，不在这里。
    """
    margin = net_profit_margin(net_income, revenue)
    turnover = asset_turnover(revenue, average_total_assets)
    multiplier = equity_multiplier(average_total_assets, average_equity)
    parts = (margin, turnover, multiplier)
    roe = None
    if not any(_zero_or_nan(p) or p is None for p in parts):
        roe = margin * turnover * multiplier
    return {
        "net_profit_margin": margin,
        "asset_turnover": turnover,
        "equity_multiplier": multiplier,
        "roe_from_dupont": roe,
    }


def dupont_attribution(prev: dict, cur: dict) -> dict:
    """连环替代法归因 ROE 变动。

    这部分是国内5号的贡献（SKILL.md L662-668 的五步算法）——FinanceToolkit 只输出
    各期分量值，不回答「ROE 变化里各因子贡献了多少」。
    教科书公式，非代码移植。
    """
    keys = ("net_profit_margin", "asset_turnover", "equity_multiplier")
    if any(prev.get(k) is None or cur.get(k) is None for k in keys):
        return {}

    m0, t0, e0 = (prev[k] for k in keys)
    m1, t1, e1 = (cur[k] for k in keys)

    # 替代顺序 M→T→E，三项贡献之和恰等于 ROE 总变动
    contributions = {
        "net_profit_margin": m1 * t0 * e0 - m0 * t0 * e0,
        "asset_turnover": m1 * t1 * e0 - m1 * t0 * e0,
        "equity_multiplier": m1 * t1 * e1 - m1 * t1 * e0,
    }
    return {
        "contributions": {k: round(v, 6) for k, v in contributions.items()},
        "total_change": round(m1 * t1 * e1 - m0 * t0 * e0, 6),
        "dominant": max(contributions, key=lambda k: abs(contributions[k])),
    }
