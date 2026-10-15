"""分析简报：把一张卡片打包成 agent 能直接消费的结构化事实。

为什么不在界面里内嵌 LLM 调用：判定必须由代码给出且可复现，而 API key、模型绑定、
额外的网络失败都会由此进入本仓库。所以这里只负责**把事实与约束一起交出去**，
推理留给任意 agent（Qoder / Claude / 其它）。与 vest 的职责划分一致。

简报里刻意包含 `constraints` 与 `authority` 两段——它们不是文档，是随数据一起传的
行为契约：告诉接收方哪些结论已经定死、它只能做什么。少了这一层，
模型很容易拿着 verdict 又去推翻它。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from . import config, registry, rules_loader, store

CONSTRAINTS = [
    "所有 verdict / 风险分 / F-Score 均由代码算出，不可推翻、不可修改、不可重新判定。",
    "你的工作是解释与联想，不是裁决。若要指出判定不合理，请明确写成异议并给出依据，不要直接改结论。",
    "引用任何阈值时必须带 rule 与 origin；origin 指向 rules/pailei.md 的是用户规则，优先级高于教材经验值。",
    "coverage 低于 0.85 时必须在结论里声明覆盖不足，不得把该标的与其他标的直接排序比较。",
    "veto 非空时风险分已作废，不得给出可比较的分数表述。",
    "不得输出仓位、买卖点、目标价或涨跌预测。",
    "涉及原始披露的论断要落到 source_pdf 或 risk_announcements 的具体条目，不能只说「年报里写了」。",
]


def _display(m) -> str:
    """给 agent 一个可读值，而不是一串 17 位浮点。

    JSON 里保留原始 value 供精算，display 供阅读与直接引用——
    两者都给，避免模型自己去除以 1e8 或自己决定保留几位。
    """
    if m.value is None:
        return "—"
    if m.unit == "str":
        return str(m.value)
    if m.unit == "cny_yi":
        return f"{m.value:,.2f} 亿"
    if m.unit == "times":
        return f"{m.value:.2f} 次"
    return f"{m.value * 100:.2f}%"


def _threshold_text(m) -> str:
    if not m.thresholds:
        return "无阈值"
    return "；".join(
        f"{t.label or t.direction} {t.value}（{t.origin}·{'你的规则' if t.kind == 'user_rule' else '教材参考值'}）"
        for t in m.thresholds
    )


def build(code: str, rules=None) -> dict:
    rules = rules or rules_loader.load_rules(config.RULES_PATH)
    conn = store.connect(read_only=True)
    try:
        st = store.load(conn, code)
        disc = store.load_disclosures(conn, code)
    finally:
        conn.close()

    notes = registry.load_notes(config.NOTES_PATH)
    card = registry.build_card(st, rules, notes)

    return {
        "code": card.code,
        "name": card.name,
        "year": card.year,
        "generated_at": datetime.now(config.CN_TZ).strftime("%Y-%m-%d %H:%M"),
        "years_available": [card.years[0], card.years[-1]],
        "authority": {
            "verdicts_by": "code",
            "rules_file": str(config.RULES_PATH),
            "knowledge_file": str(config.NOTES_PATH),
            "note": "本简报里的 verdict / score 均为确定性计算结果",
        },
        "risk": card.risk,
        "fscore": {
            "summary": card.fscore_summary,
            "rules": [{"label": r.label, "status": r.status, "evidence": r.evidence, "note": r.note}
                      for r in card.fscore],
        },
        "metrics": [
            {
                "key": m.key, "label": m.label, "value": m.value, "display": _display(m),
                "unit": m.unit, "group": m.group,
                "verdict": m.verdict, "rule": m.verdict_by, "reason": m.verdict_reason,
                "formula": m.formula,
                "inputs": {k: v for k, v in m.inputs.items() if v is not None},
                "thresholds": [{"value": t.value, "kind": t.kind, "origin": t.origin,
                                 "label": t.label} for t in m.thresholds],
                "learn": m.learn, "drill": m.drill,
            }
            for m in card.metrics
        ],
        "dupont": {
            "current": card.latest_row.get("dupont"),
            "attribution": card.latest_row.get("dupont_attribution"),
        },
        "trend": {
            "columns": ["年度"] + list(card.trend.columns),
            "rows": [[y] + [None if pd.isna(v) else float(v) for v in row]
                     for y, row in zip(card.trend.index, card.trend.to_numpy())],
        },
        "source_pdf": _annual_pdf(disc, card.year),
        "risk_announcements": _risk_items(disc),
        "coverage_warnings": _warnings(card),
        "constraints": CONSTRAINTS,
    }


def _annual_pdf(disc, year):
    if disc.empty:
        return None
    per = disc[disc["kind"].str.contains("年度报告", na=False) & (disc["report_year"] == year)]
    if per.empty:
        return None
    r = per.iloc[0]
    return {"title": r["title"], "url": r["pdf_url"], "published": str(r["publish_ts"])[:10],
            "ts_precision": r["ts_precision"]}


def _risk_items(disc, limit: int = 12):
    if disc.empty:
        return []
    risk = disc[~disc["kind"].str.contains("报告", na=False)]
    return [
        {"date": str(r.publish_ts)[:10], "kind": r.kind, "title": r.title, "url": r.pdf_url}
        for r in risk.head(limit).itertuples()
    ]


def _warnings(card) -> list[str]:
    out = []
    cov = (card.risk or {}).get("coverage")
    if cov is not None and cov < 0.85:
        out.append(f"评分覆盖仅 {cov:.0%}，有维度算不出，不可与其他标的直接比较")
    if card.fscore_summary["na"]:
        out.append(f"F-Score 有 {card.fscore_summary['na']} 条无法判定，得分被低估")
    if (card.risk or {}).get("veto"):
        out.append("命中一票否决层，风险分已作废")
    return out


def to_markdown(b: dict) -> str:
    vetoed = bool(b["risk"].get("veto"))
    score = "已作废（命中一票否决，不予展示）" if vetoed else f"{b['risk'].get('score')}/100"
    lines = [
        f"# {b['name']}（{b['code']}）{b['year']} 年分析简报",
        "",
        f"- 风险分：**{score}**　评级：**{b['risk'].get('band')}**"
        f"　评分覆盖：{(b['risk'].get('coverage') or 0):.0%}",
        f"- F-Score：{b['fscore']['summary']['score']}/9"
        f"（判定 {b['fscore']['summary']['judged']}/9）",
    ]
    if vetoed:
        lines += ["", "## ⛔ 一票否决"] + [f"- {v['label']}（{v['rule']}）：{v['reason']}" for v in b["risk"]["veto"]]

    lines += ["", "## 判定明细"]
    for m in b["metrics"]:
        if not m["verdict"]:
            continue
        lines.append(f"- **{m['label']}** = `{m['display']}` → {m['verdict']}（{m['rule']}）")
        lines.append(f"  - 公式：{m['formula']}")
        th = "；".join(
            f"{t['label'] or '阈值'} = {t['value']}（{t['origin']}·"
            f"{'你的规则' if t['kind'] == 'user_rule' else '教材参考值'}）"
            for t in m["thresholds"]
        ) or "无阈值"
        lines.append(f"  - 阈值：{th}")
        lines.append(f"  - 理由：{m['reason']}")
        # 不截断：这份东西是给 agent 读的，截在半句上只会让它去猜
        for note in m["learn"]:
            lines.append(f"  - 解读：{note}")

    att = b["dupont"].get("attribution")
    if att:
        lines += ["", "## 杜邦归因",
                  f"- ROE 变动 {att.get('total_change', 0):+.4f}，主导因子：{att.get('dominant')}"]
        for k, v in (att.get("contributions") or {}).items():
            lines.append(f"  - {k}: {v:+.4f}")

    if b["source_pdf"]:
        sp = b["source_pdf"]
        lines += ["", "## 原始披露", f"- 本期年报：[{sp['title']}]({sp['url']})（{sp['published']}）"]
    else:
        lines += ["", "## 原始披露", "- 未索引到本期年报正文，重跑 fw-fetch 可补齐"]
    if b["risk_announcements"]:
        lines.append(f"- 风险类公告 {len(b['risk_announcements'])} 条：")
        for a in b["risk_announcements"]:
            lines.append(f"  - {a['date']} [{a['kind']}] {a['title']}")

    if b["coverage_warnings"]:
        lines += ["", "## ⚠️ 使用限制"] + [f"- {w}" for w in b["coverage_warnings"]]
    lines += ["", "## 交给你的约束"] + [f"- {c}" for c in b["constraints"]]
    return "\n".join(lines)


def main(argv=None) -> None:
    import argparse

    p = argparse.ArgumentParser(prog="fw.brief", description="导出分析简报供 agent 消费")
    p.add_argument("code", help="6 位股票代码")
    p.add_argument("--markdown", action="store_true", help="输出可读 markdown，默认 JSON")
    p.add_argument("--out", help="写入文件而不是 stdout")
    args = p.parse_args(argv)

    try:
        b = build(args.code)
    except FileNotFoundError as exc:
        # 这是新人 clone 后第一个会撞上的错误：给出可执行的下一步，而不是 traceback
        p.error(str(exc))
    except KeyError as exc:
        p.error(f"该标的尚未入库：{exc}")

    text = to_markdown(b) if args.markdown else json.dumps(b, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"已写入 {args.out}")
    else:
        print(text)


if __name__ == "__main__":
    main()
