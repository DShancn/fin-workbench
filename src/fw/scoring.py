"""排雷分层评分：把逐条判定合成一个可解释的风险分。

国内1号把算分交给 LLM 心算（其 SKILL.md Phase 4）。这里代码化——分数必须可复现，
而且必须能拆开给界面看：只给一个「72 分」的黑箱数字，等于把判断权又交还给猜测。

一个刻意的设计：**按实际覆盖到的权重重新归一**。
若直接把 N/A 当满分或当零分，都会让「缺数据的公司」得到虚高或虚低的分数——
前者尤其危险，因为它看起来像结论。这里 N/A 的组不参与加权，只用覆盖到的权重
做分母，同时把覆盖率本身作为一等公民输出，界面上与分数并列展示。
"""

from __future__ import annotations

from .derive import FIELD_META
from .metric import FAIL, NA, PASS, WARN

# 组权重合计 100。报表可信度与盈利质量权重最高：
# 前者决定其余数字能不能信，后者决定这些数字是不是真的来自主业。
LAYER_WEIGHTS = {
    "报表可信度": 10,
    "盈利质量": 22,
    "现金流质量": 20,
    "资产质量": 18,
    "杠杆": 15,
    "盈利": 8,
    "成长": 4,
    "营运": 2,
    "偿债": 1,
    "结构": 0,
    "其他": 0,
}

# 单条判定的得分。WARN 给一半而不是 0.9——关注线本身就是「还没坏但方向不对」。
RULE_SCORE = {PASS: 1.0, WARN: 0.5, FAIL: 0.0}

BANDS = (
    (85, "优"),
    (70, "良"),
    (55, "弱"),
    (40, "差"),
    (0, "危险"),
)


def band(score: float) -> str:
    for cut, name in BANDS:
        if score >= cut:
            return name
    return "危险"


def score_metrics(metrics: list) -> dict:
    """按组加权。metrics 为 pailei 产出的 Metric 列表（engine == 'pailei'）。"""
    rules = [m for m in metrics if m.engine == "pailei"]

    groups: dict[str, list] = {}
    for m in rules:
        groups.setdefault(m.group or "其他", []).append(m)

    covered_weight = 0.0
    weighted = 0.0
    per_group: dict[str, dict] = {}

    for name, items in groups.items():
        weight = LAYER_WEIGHTS.get(name, 0)
        judged = [i for i in items if i.verdict != NA]
        if weight == 0 or not judged:
            per_group[name] = {
                "weight": weight,
                "score": None,
                "judged": len(judged),
                "total": len(items),
                "reason": "该组指标全部缺数据，不参与加权" if not judged else "权重为 0，仅作参考",
            }
            continue

        gscore = sum(RULE_SCORE.get(i.verdict, 0.0) for i in judged) / len(judged)
        weighted += weight * gscore
        covered_weight += weight
        per_group[name] = {
            "weight": weight,
            "score": round(gscore * 100, 1),
            "judged": len(judged),
            "total": len(items),
        }

    # 只用未标注权重的组做分母：新增指标忘记配权重时不该稀释总分
    total_weight = sum(
        w for name, w in LAYER_WEIGHTS.items()
        if name in groups and groups[name] and LAYER_WEIGHTS.get(name, 0) > 0
    )

    if covered_weight == 0:
        return {
            "score": None, "band": "无法评级", "coverage": 0.0,
            "covered_weight": 0, "total_weight": total_weight,
            "groups": per_group, "veto": [],
            "note": "没有任何一组的指标可判定，不给分数——空数据不等于中等质量",
        }

    raw = weighted / covered_weight * 100.0
    veto = [m for m in rules if m.verdict == FAIL and m.verdict_by.startswith("R0")]
    return {
        "score": round(raw, 1),
        "band": "排除" if veto else band(raw),
        "coverage": round(covered_weight / total_weight, 3) if total_weight else 1.0,
        "covered_weight": int(covered_weight),
        "total_weight": int(total_weight),
        "groups": per_group,
        "veto": [{"label": m.label, "rule": m.verdict_by, "reason": m.verdict_reason} for m in veto],
        "note": "" if veto else "分数只反映规则覆盖到的维度，不等于投资建议",
    }


def worst_group(result: dict) -> str | None:
    """最需要看哪一组。给界面当提示用。"""
    scored = {k: v["score"] for k, v in result["groups"].items() if v.get("score") is not None}
    if not scored:
        return None
    return min(scored, key=scored.get)
