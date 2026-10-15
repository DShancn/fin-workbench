"""指标契约：整个工具的唯一数据结构。

筛选、对比、标注卡、Agent 四个功能都是这个对象流的投影，而不是各写一套。

「点击某处能给注释、顺便学到东西」在这里不是 UI 层的 tooltip，而是数据结构的要求：
每个数值都必须自带公式、参与计算的原值、阈值及其出处、代码给出的判定、以及解读方法。
缺任何一项，注释就只能靠模型编。

阈值分两类并强制标注 kind，这是防止「教材通用结论覆盖个人规则」的结构性做法：
优先级写在数据里，不依赖提示词去约束模型的注意力。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

USER_RULE = "user_rule"    # 来自 rules/ 下我自己定的阈值
TEXTBOOK = "textbook"      # 来自教材经验值，仅作解释参考

PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"
NA = "N/A"                 # 数据不足，判定不成立。与国内1号区分 SKIP 的意义不同：
                           # N/A 表示「这次没资格下结论」，必须计入覆盖度而非被忽略


@dataclass
class Threshold:
    value: Any
    kind: str
    origin: str            # 具体到规则编号或书名章节，注释要能回指
    direction: str = "gte"  # gte / lte / eq / band
    label: str = ""

    @property
    def authority(self) -> int:
        """代码裁决只认 user_rule。"""
        return 0 if self.kind == USER_RULE else 1


@dataclass
class Metric:
    key: str
    label: str
    period: str
    engine: str                       # ratios / fscore / pailei / derived
    value: float | None = None
    unit: str = "ratio"               # ratio / pct / times / cny / cny_yi / str
    formula: str = ""
    inputs: dict[str, float | None] = field(default_factory=dict)
    thresholds: list[Threshold] = field(default_factory=list)
    verdict: str | None = None
    verdict_by: str = ""              # 哪条规则判的，可回指
    verdict_reason: str = ""
    learn: list[str] = field(default_factory=list)   # 知识层解读，无权改判定
    drill: str = ""                   # 溯源：PDF 章节 / 科目 / 来源
    group: str = "其他"
    note: str = ""

    @property
    def decisive_threshold(self) -> Threshold | None:
        """判定只依据用户规则；无用户规则则不产生命令性判定。"""
        for t in sorted(self.thresholds, key=lambda x: x.authority):
            if t.kind == USER_RULE:
                return t
        return None

    @property
    def comparable_value(self) -> float | None:
        """pct 与 ratio 混用是注释面板最容易出错的地方，统一成 ratio 量纲比较。"""
        if self.value is None:
            return None
        return self.value / 100.0 if self.unit == "pct" else self.value

    def to_display(self) -> str:
        if self.value is None:
            return "—"
        if self.unit == "str":
            return str(self.value)
        if self.unit == "cny_yi":
            return f"{self.value:,.2f} 亿"
        if self.unit == "cny":
            return f"{self.value:,.0f}"
        if self.unit == "times":
            return f"{self.value:.2f} 次"
        # ratio 一律按百分比展示：阈值文件里写的是小数（0.30 表示 30%），
        # 但人读注释时看百分数才不会把 0.0001 的有息负债率误读成 1%。
        return f"{self.value * 100:.2f}%"


@dataclass
class Coverage:
    """判定覆盖度。

    国际3号在缺数据时是「整家公司跳过」（main.py:27-32 的 except continue），
    A股字段缺失是常态而非异常，静默丢公司会让人以为「这家没问题」。
    这里把「判不了」显式化为一个数，UI 必须展示它。
    """

    total: int = 0
    judged: int = 0
    na: int = 0

    @property
    def ratio(self) -> float:
        return self.judged / self.total if self.total else 0.0
