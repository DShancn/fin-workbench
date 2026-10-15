"""rules/pailei.md 解析器。

规则由用户直接编辑，所以解析必须**响烈失败**：字段名写错、阈值不是数字、
check 取值非法，都直接抛错，绝不静默跳过一条规则。

静默跳过比报错危险得多——那会让「32 条全过」的界面底下其实只跑了 20 条。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

CHECKS = ("lte", "gte", "eq")
KNOWN_KEYS = {"layer", "field", "check", "warn", "fail", "allow", "learn", "drill"}
_HEADER = re.compile(r"^## (R[\d.]+)\s+(.+?)\s*$")
_BULLET = re.compile(r"^- ([a-z_]+): (.*)$")


@dataclass
class Rule:
    id: str
    label: str
    field: str
    check: str
    warn: float | None = None
    fail: float | None = None
    allow: list[str] = field(default_factory=list)
    layer: int = 1
    learn: str = ""
    drill: str = ""


def _num(raw: str, path: Path, lineno: int, key: str) -> float | None:
    raw = raw.strip()
    if raw in ("", "-", "null", "none", "None"):
        return None
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{path}:{lineno} `{key}: {raw}` 不是数字") from exc


def load_rules(path: Path) -> list[Rule]:
    text = path.read_text(encoding="utf-8")
    rules: list[Rule] = []
    current: dict | None = None
    header_line = 0

    def flush():
        if current is None:
            return
        if "field" not in current:
            raise ValueError(f"{path}:{header_line} 规则 {current.get('id')} 缺 field")
        check = current.get("check", "lte")
        if check not in CHECKS:
            raise ValueError(f"{path}:{header_line} check={check!r} 非法，可选 {CHECKS}")
        rules.append(
            Rule(
                id=current["id"],
                label=current["label"],
                field=current["field"],
                check=check,
                warn=current.get("warn"),
                fail=current.get("fail"),
                allow=current.get("allow", []),
                layer=int(current.get("layer", 1)),
                learn=current.get("learn", ""),
                drill=current.get("drill", ""),
            )
        )

    for lineno, line in enumerate(text.splitlines(), 1):
        h = _HEADER.match(line)
        if h:
            flush()
            header_line = lineno
            current = {"id": h.group(1), "label": h.group(2)}
            continue
        b = _BULLET.match(line.strip())
        if not b or current is None:
            continue
        key, raw = b.group(1), b.group(2).strip()
        if key not in KNOWN_KEYS:
            raise ValueError(
                f"{path}:{lineno} 未知字段 `{key}`。可用: {sorted(KNOWN_KEYS)}。"
                "拒绝静默忽略——那会让规则数量与你以为的不一样。"
            )
        if key in ("warn", "fail", "layer"):
            current[key] = _num(raw, path, lineno, key) if key != "layer" else int(float(raw))
        elif key == "allow":
            current[key] = [x.strip() for x in raw.split(",") if x.strip()]
        else:
            current[key] = raw
    flush()

    if not rules:
        raise ValueError(f"{path} 没解析出任何规则，检查 `## R<编号> <名称>` 标题格式")
    ids = [r.id for r in rules]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"{path} 规则编号重复: {sorted(dupes)}")
    return rules
