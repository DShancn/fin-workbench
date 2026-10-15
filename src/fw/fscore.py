"""Piotroski F-Score 九条判分。

按 Piotroski (2000)「Conservatively Constructing a Stock Screen」的论文规格
自行实现，未移植任何第三方代码（国际3号仓库无 LICENSE，其 112 行逻辑不可搬运；
算法与阈值本身是公开事实，不受版权保护）。

相对国际3号实现（其 calculator/fscore.py:2-10）修正了四处口径：
  1. ROA 分母改用**平均**总资产，原实现用期末值（ratios.py:2）
  2. 杠杆改用**全部有息负债**，原实现只看长期借款，对A股系统性低估去杠杆
  3. 应计项两条比较统一分母，原实现一边期初一边期末
  4. 缺数据或分母非正时记 **N/A 并计入覆盖度**，原实现是整家公司跳过
     （main.py:27-32 的 except continue），症状与「这家没问题」不可区分
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .metric import NA, FAIL, PASS, WARN


@dataclass
class RuleResult:
    key: str
    label: str
    hit: bool | None          # None = 无资格判定
    evidence: dict = field(default_factory=dict)
    note: str = ""

    @property
    def status(self) -> str:
        return NA if self.hit is None else (PASS if self.hit else FAIL)


def _num(row, key):
    if row is None:
        return None
    v = row.get(key)
    if v is None or pd.isna(v):
        return None
    v = float(v)
    return None if pd.isna(v) else v


def _ok(*vals) -> bool:
    return all(v is not None for v in vals)


def _na(key, label, note) -> RuleResult:
    return RuleResult(key, label, None, {}, note)


def eval_fscore(cur, prev) -> list[RuleResult]:
    """九条。cur / prev 是 canonical 年报行（pd.Series）。"""
    out: list[RuleResult] = []

    def add(key, label, hit, evidence, note=""):
        out.append(RuleResult(key, label, hit, evidence, note))

    roa_c = (_num(cur, "net_profit"), _num(cur, "avg_total_assets"))
    roa_p = (_num(prev, "net_profit"), _num(prev, "avg_total_assets")) if prev is not None else (None, None)

    # 1. 总资产收益率 > 0
    if _ok(*roa_c) and roa_c[1] > 0:
        add("roa_positive", "ROA > 0", roa_c[0] / roa_c[1] > 0,
            {"净利润": roa_c[0], "平均总资产": roa_c[1]})
    else:
        out.append(_na("roa_positive", "ROA > 0", "净利润或平均总资产缺失/非正"))

    # 2. 经营现金流 > 0
    ocf = _num(cur, "ocf")
    if ocf is not None:
        add("ocf_positive", "经营现金流 > 0", ocf > 0, {"经营现金流净额": ocf})
    else:
        out.append(_na("ocf_positive", "经营现金流 > 0", "现金流量表缺该字段"))

    # 3. ΔROA > 0
    if _ok(*roa_c, *roa_p) and roa_c[1] > 0 and roa_p[1] > 0:
        c, p = roa_c[0] / roa_c[1], roa_p[0] / roa_p[1]
        add("roa_improving", "ROA 同比改善", c > p, {"本年ROA": round(c, 4), "上年ROA": round(p, 4)})
    else:
        out.append(_na("roa_improving", "ROA 同比改善", "需连续两年数据，上年缺失"))

    # 4. 应计质量：经营现金流 > 净利润（分母口径统一，原实现两边不一致）
    ni = _num(cur, "net_profit")
    if _ok(ocf, ni) and ni != 0:
        add("accruals_quality", "现金流覆盖净利（低应计）", ocf > ni,
            {"经营现金流": ocf, "净利润": ni, "现金/利润比": round(ocf / ni, 3) if ni else None})
    else:
        out.append(_na("accruals_quality", "现金流覆盖净利（低应计）", "净利润缺失或为零"))

    # 5. Δ杠杆 < 0：有息负债/平均总资产 下降
    d_c = (_num(cur, "interest_bearing_debt"), _num(cur, "avg_total_assets"))
    d_p = (_num(prev, "interest_bearing_debt"), _num(prev, "avg_total_assets")) if prev is not None else (None, None)
    if _ok(*d_c, *d_p) and d_c[1] > 0 and d_p[1] > 0:
        c, p = d_c[0] / d_c[1], d_p[0] / d_p[1]
        add("leverage_down", "有息负债率下降", c < p,
            {"本年": round(c, 4), "上年": round(p, 4), "口径": "短借+一年内到期+长借+应付债券"})
    else:
        out.append(_na("leverage_down", "有息负债率下降", "有息负债或资产数据不足"))

    # 6. Δ流动比率 > 0
    cr_c = (_num(cur, "current_assets"), _num(cur, "current_liab"))
    cr_p = (_num(prev, "current_assets"), _num(prev, "current_liab")) if prev is not None else (None, None)
    if _ok(*cr_c, *cr_p) and cr_c[1] > 0 and cr_p[1] > 0:
        c, p = cr_c[0] / cr_c[1], cr_p[0] / cr_p[1]
        add("liquidity_up", "流动比率改善", c > p, {"本年": round(c, 3), "上年": round(p, 3)})
    else:
        out.append(_na("liquidity_up", "流动比率改善", "流动资产负债数据不足"))

    # 7. 未增发：期末注册股本未扩大
    s_c, s_p = _num(cur, "share_capital"), (_num(prev, "share_capital") if prev is not None else None)
    if _ok(s_c, s_p) and s_p > 0:
        ratio = s_c / s_p
        add("no_dilution", "股本未扩大", ratio <= 1.0,
            {"股本比": round(ratio, 4), "本年股本": s_c, "上年股本": s_p},
            note="akshare 无加权平均股本口径，此处为期末注册股本" if ratio > 1.0 else "")
    else:
        out.append(_na("no_dilution", "股本未扩大", "股本数据不足"))

    # 8. Δ毛利率 > 0
    gm_c = (_num(cur, "revenue"), _num(cur, "net_cogs"))
    gm_p = (_num(prev, "revenue"), _num(prev, "net_cogs")) if prev is not None else (None, None)
    if _ok(*gm_c, *gm_p) and gm_c[0] > 0 and gm_p[0] > 0:
        c = (gm_c[0] - gm_c[1]) / gm_c[0]
        p = (gm_p[0] - gm_p[1]) / gm_p[0]
        add("margin_up", "毛利率改善", c > p, {"本年": round(c, 4), "上年": round(p, 4)})
    else:
        out.append(_na("margin_up", "毛利率改善", "营收或成本数据不足"))

    # 9. Δ资产周转率 > 0（分母统一用平均总资产）
    t_c = (_num(cur, "revenue"), _num(cur, "avg_total_assets"))
    t_p = (_num(prev, "revenue"), _num(prev, "avg_total_assets")) if prev is not None else (None, None)
    if _ok(*t_c, *t_p) and t_c[1] > 0 and t_p[1] > 0:
        c, p = t_c[0] / t_c[1], t_p[0] / t_p[1]
        add("turnover_up", "资产周转率改善", c > p, {"本年": round(c, 4), "上年": round(p, 4)})
    else:
        out.append(_na("turnover_up", "资产周转率改善", "营收或资产数据不足"))

    return out


def summarize(results: list[RuleResult]) -> dict:
    """得分 + 覆盖度。

    覆盖度必须与得分同等显眼：7/9 分和 7/7 分是完全不同的结论，
    但在只看总分的界面里二者长得一模一样。
    """
    judged = [r for r in results if r.hit is not None]
    return {
        "score": sum(1 for r in judged if r.hit),
        "judged": len(judged),
        "total": len(results),
        "na": len(results) - len(judged),
        "coverage": len(judged) / len(results) if results else 0.0,
    }
