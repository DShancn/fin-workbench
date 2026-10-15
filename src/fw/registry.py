"""把三层输出装配成一张标注卡。

Card 是筛选/对比/标注/Agent 四个功能共同消费的唯一对象。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from . import scoring
from .derive import COMPONENTS, FIELD_META, derive
from .fscore import RuleResult, eval_fscore, summarize
from .metric import NA, PASS, WARN, Metric
from .pailei import evaluate

# 无阈值、纯展示的原始科目。放进来是为了让注释面板能回答「这个数本身是多少」，
# 而不是只有比率。
INFO_FIELDS = {
    "revenue": ("营业总收入", "cny_yi", "利润表 · 营业总收入", "成长"),
    "parent_profit": ("归母净利润", "cny_yi", "利润表 · 归属于母公司所有者的净利润", "成长"),
    "deduct_profit": ("扣非归母净利润", "cny_yi", "利润表 · 扣除非经常性损益后的净利润", "盈利质量"),
    "ocf": ("经营现金流净额", "cny_yi", "现金流量表 · 经营活动产生的现金流量净额", "现金流质量"),
    "capex": ("购建长期资产支付现金", "cny_yi", "现金流量表 · 购建固定资产等支付现金", "现金流质量"),
    "total_assets": ("资产总计", "cny_yi", "资产负债表 · 资产总计", "结构"),
    "parent_equity": ("归母权益", "cny_yi", "资产负债表 · 归属于母公司所有者权益合计", "结构"),
    "goodwill": ("商誉", "cny_yi", "资产负债表 · 商誉", "资产质量"),
    "accounts_rece": ("应收账款", "cny_yi", "资产负债表 · 应收账款", "资产质量"),
    "inventory": ("存货", "cny_yi", "资产负债表 · 存货", "资产质量"),
    "interest_bearing_debt": ("有息负债", "cny_yi", "资产负债表 · 短借+一年内到期+长借+应付债券", "杠杆"),
}


@dataclass
class Card:
    code: str
    name: str
    year: int
    years: list[int]
    metrics: list[Metric]
    fscore: list[RuleResult]
    fscore_summary: dict
    summary: dict
    trend: pd.DataFrame
    latest_row: dict
    risk: dict = field(default_factory=dict)

    @property
    def veto(self) -> list[Metric]:
        return self.summary.get("veto", [])

    def by_group(self) -> dict[str, list[Metric]]:
        out: dict[str, list[Metric]] = {}
        for m in self.metrics:
            out.setdefault(m.group, []).append(m)
        return out

    def verdict_counts(self) -> dict[str, int]:
        keys = [PASS, WARN, NA] + ["FAIL"]
        return {k: sum(1 for m in self.metrics if m.verdict == k) for k in keys}


def load_notes(path: Path) -> dict[str, str]:
    """rules/notes.md: `## <field>` + 紧随其后的段落，作为该字段的学习注解。"""
    if not path.exists():
        return {}
    notes: dict[str, str] = {}
    current = None
    buf: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            if current and buf:
                notes[current] = " ".join(buf).strip()
            current, buf = line[3:].strip(), []
        elif current and line.strip() and not line.startswith(("#", "-", ">")):
            buf.append(line.strip())
    if current and buf:
        notes[current] = " ".join(buf).strip()
    return notes


def _info_metric(field_key: str, value, period: str, note: str, cfg) -> Metric:
    label, unit, drill, group = cfg
    raw = None if value is None or pd.isna(value) else float(value)
    return Metric(
        key=field_key, label=label, period=period, engine="info",
        value=None if raw is None else (raw / 1e8 if unit == "cny_yi" else raw),
        unit=unit, formula=drill, group=group, drill=drill,
        learn=[note] if note else [],
    )


def build_card(st, rules, notes: dict[str, str] | None = None, years_back: int = 5) -> Card:
    notes = notes or {}
    years = st.years()
    if not years:
        raise ValueError(f"{st.code} 没有可用年报")
    year = years[-1]
    prev_year = years[-2] if len(years) > 1 else None

    opinion = None
    if st.raw_opinion is not None and year in st.raw_opinion.index:
        v = st.raw_opinion.loc[year]
        opinion = None if pd.isna(v) else str(v)

    derived = derive(st.year(year), st.year(prev_year) if prev_year else None, opinion)

    rule_metrics, summary = evaluate(rules, derived, str(year))
    for m in rule_metrics:
        if m.key in notes and notes[m.key] not in m.learn:
            m.learn.append(notes[m.key])

    metrics = list(rule_metrics)
    seen = {m.key for m in metrics}
    for key, cfg in INFO_FIELDS.items():
        if key in seen:
            continue
        metrics.append(_info_metric(key, derived.get(key), str(year), notes.get(key, ""), cfg))
    for key in ("return_on_equity", "asset_turnover", "net_profit_margin", "current_ratio",
                "revenue_yoy", "parent_profit_yoy", "inventory_turnover"):
        if key in seen or key not in FIELD_META:
            continue
        meta = FIELD_META[key]
        metrics.append(Metric(
            key=key, label=meta.label, period=str(year), engine="ratios",
            value=derived.get(key), unit=meta.unit, formula=meta.formula, group=meta.group,
            learn=[notes[key]] if key in notes else [],
        ))

    # 让每个指标都能展示「由哪些原值算出」，注释面板要能被人工复核
    for m in metrics:
        m.inputs = {
            k: derived.get(k)
            for k in COMPONENTS.get(m.key, ())
            if derived.get(k) is not None
        }

    results = eval_fscore(derived, _prev_derived(st, prev_year, years))
    trend = _trend(st, years[-years_back:])

    return Card(
        code=st.code, name=getattr(st, "name", "") or st.code, year=year, years=years,
        metrics=metrics, fscore=results, fscore_summary=summarize(results),
        summary=summary, trend=trend, latest_row={k: v for k, v in derived.items() if k != "dupont"},
        risk=scoring.score_metrics(metrics),
    )


def _prev_derived(st, prev_year, years):
    """F-Score 需要的「比较量」直接从上一年的派生值取，避免重复取数。"""
    if prev_year is None:
        return None
    idx = years.index(prev_year)
    before = years[idx - 1] if idx > 0 else None
    return derive(
        st.year(prev_year),
        st.year(before) if before else None,
        None,
    )


TREND_COLS = [
    ("revenue", "营业总收入", 1e8),
    ("parent_profit", "归母净利润", 1e8),
    ("ocf", "经营现金流", 1e8),
    ("gross_margin", "毛利率", None),
    ("return_on_equity", "ROE", None),
    ("ocf_to_profit", "现金流/利润", None),
    ("interest_bearing_debt_ratio", "有息负债率", None),
    ("asset_turnover", "周转率", None),
]


def _trend(st, years: list[int]) -> pd.DataFrame:
    rows = []
    for i, y in enumerate(years):
        prev_y = years[i - 1] if i > 0 else (y - 1)
        opinion = None
        if st.raw_opinion is not None and y in st.raw_opinion.index:
            v = st.raw_opinion.loc[y]
            opinion = None if pd.isna(v) else str(v)
        d = derive(st.year(y), st.year(prev_y) if prev_y in st.years() else None, opinion)
        row = {"年度": y}
        for key, label, scale in TREND_COLS:
            val = d.get(key)
            row[label] = None if val is None else (val / scale if scale else val)
        rows.append(row)
    return pd.DataFrame(rows).set_index("年度")
