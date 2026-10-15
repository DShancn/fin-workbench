"""规则引擎：把 rules/pailei.md 应用到派生指标上。

判定全部在这里发生，代码是唯一裁决者。知识层（rules/notes.md）只往 Metric 里
填 learn/drill，无权修改 verdict——这是把「教材通用结论覆盖个人规则」
这件事从概率问题变成结构问题。
"""

from __future__ import annotations

from .derive import FIELD_META
from .metric import FAIL, NA, PASS, WARN, Metric, Threshold
from .rules_loader import Rule


def _cmp_lte(value, rule: Rule):
    if rule.fail is not None and value > rule.fail:
        return FAIL, f"{value:.4f} > 红线 {rule.fail}"
    if rule.warn is not None and value > rule.warn:
        return WARN, f"{value:.4f} > 关注线 {rule.warn}"
    return PASS, f"{value:.4f} ≤ {rule.warn if rule.warn is not None else rule.fail}"


def _cmp_gte(value, rule: Rule):
    if rule.fail is not None and value < rule.fail:
        return FAIL, f"{value:.4f} < 红线 {rule.fail}"
    if rule.warn is not None and value < rule.warn:
        return WARN, f"{value:.4f} < 关注线 {rule.warn}"
    return PASS, f"{value:.4f} ≥ {rule.warn if rule.warn is not None else rule.fail}"


def _cmp_eq(value, rule: Rule):
    text = str(value).strip()
    if text in rule.allow:
        return PASS, f"「{text}」在许可清单内"
    if text in ("未披露", "nan", ""):
        return NA, "审计意见未取到，无资格判定"
    return FAIL, f"「{text}」不在许可清单 {rule.allow}"


_CMP = {"lte": _cmp_lte, "gte": _cmp_gte, "eq": _cmp_eq}


def apply_rule(rule: Rule, derived: dict, period: str) -> Metric:
    meta = FIELD_META.get(rule.field)
    value = derived.get(rule.field)
    unit = meta.unit if meta else "ratio"
    m = Metric(
        key=rule.field,
        label=meta.label if meta else rule.field,
        period=period,
        engine="pailei",
        unit=unit,
        formula=meta.formula if meta else "",
        group=meta.group if meta else "其他",
        drill=rule.drill,
        learn=[rule.learn] if rule.learn else [],
        note=rule.label,
    )
    m.value = value if unit == "str" else (None if value is None else float(value))
    if rule.warn is not None:
        m.thresholds.append(Threshold(rule.warn, "user_rule", f"pailei.md {rule.id}", rule.check, "关注线"))
    if rule.fail is not None:
        m.thresholds.append(Threshold(rule.fail, "user_rule", f"pailei.md {rule.id}", rule.check, "红线"))
    if rule.allow:
        m.thresholds.append(Threshold(",".join(rule.allow), "user_rule", f"pailei.md {rule.id}", "eq", "许可值"))

    if value is None:
        m.verdict, m.verdict_by, m.verdict_reason = NA, rule.id, "派生指标缺数据，判定不成立"
        return m

    m.verdict, m.verdict_reason = _CMP[rule.check](value, rule)
    m.verdict_by = rule.id
    return m


def evaluate(rules: list[Rule], derived: dict, period: str) -> tuple[list[Metric], dict]:
    metrics = [apply_rule(r, derived, period) for r in rules]
    layer0 = [m for m in metrics if _layer(m) == 0]
    summary = {
        "total": len(metrics),
        "judged": sum(1 for m in metrics if m.verdict != NA),
        "pass": sum(1 for m in metrics if m.verdict == PASS),
        "warn": sum(1 for m in metrics if m.verdict == WARN),
        "fail": sum(1 for m in metrics if m.verdict == FAIL),
        "na": sum(1 for m in metrics if m.verdict == NA),
        "veto": [m for m in layer0 if m.verdict == FAIL],
    }
    return metrics, summary


def _layer(m: Metric) -> int:
    rid = m.verdict_by or ""
    return 0 if rid.startswith("R0") else 1
