"""标注卡界面。

核心交互：每个数字都可点开，展开后是「公式 → 原值 → 阈值及出处 → 判定理由 →
为什么这个指标重要 → 去年报哪一科核对」。所以点下去学到的不是这个数好不好，
而是这个数该怎么读。

判定由 rules/pailei.md 的代码引擎给出，界面只展示；知识注解不参与判定。
AI 分析不在这里做——导出结构化卡片交给 agent 层，与 vest 的职责划分一致。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

from fw import registry, rules_loader, store  # noqa: E402
from fw.metric import FAIL, NA, PASS, WARN  # noqa: E402

RULES_FILE = ROOT / "rules" / "pailei.md"
NOTES_FILE = ROOT / "rules" / "notes.md"

VERDICT_STYLE = {
    PASS: ("🟢", "通过"),
    WARN: ("🟡", "关注"),
    FAIL: ("🔴", "警示"),
    NA: ("⚪", "无法判定"),
    None: ("⚪", "未判定"),
}


@st.cache_resource(show_spinner=False)
def load_rules():
    return rules_loader.load_rules(RULES_FILE)


@st.cache_resource(show_spinner=False)
def load_notes():
    return registry.load_notes(NOTES_FILE)


@st.cache_data(show_spinner=False)
def load_card(code: str):
    conn = store.connect(read_only=True)
    try:
        st_ = store.load(conn, code)
        return registry.build_card(st_, load_rules(), load_notes())
    finally:
        conn.close()


def yi(v):
    return None if v is None else v / 1e8


def render_metric(m) -> None:
    icon, word = VERDICT_STYLE.get(m.verdict, ("⚪", ""))
    header = f"{icon} **{m.label}** — `{m.to_display()}` · {word}"
    if m.verdict_by:
        header += f" ({m.verdict_by})"
    with st.expander(header, expanded=(m.verdict == FAIL)):
        if m.formula:
            st.markdown(f"**怎么算的** {m.formula}")
        if m.inputs:
            rows = [{"项目": k, "金额(亿元)" : round(yi(v), 3) if abs(v) > 1e6 else round(v, 4)}
                    for k, v in m.inputs.items()]
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

        if m.thresholds:
            for t in m.thresholds:
                badge = "📌 你的规则" if t.kind == "user_rule" else "📖 教材参考值"
                st.markdown(f"{badge} `{t.origin}` · {t.label or t.direction} = **{t.value}**")

        if m.verdict_reason:
            st.markdown(f"**判定理由** {m.verdict_reason}")
        for line in m.learn:
            st.markdown(f"> {line}")
        if m.drill:
            st.caption(f"🔍 去年报核对：{m.drill}")


def trend_chart(trend: pd.DataFrame):
    fig = go.Figure()
    for col, side in (("营业总收入", 1), ("归母净利润", 1), ("经营现金流", 1)):
        if col in trend:
            fig.add_trace(go.Bar(x=trend.index, name=col, y=trend[col]))
    for col in ("毛利率", "ROE", "现金流/利润"):
        if col in trend:
            fig.add_trace(
                go.Scatter(x=trend.index, y=trend[col] * 100, name=f"{col}%", yaxis="y2", mode="lines+markers")
            )
    fig.update_layout(
        height=420, barmode="group", yaxis=dict(title="亿元"),
        yaxis2=dict(title="%", overlaying="y", side="right"),
        legend=dict(orientation="h", y=-0.25), margin=dict(t=30, b=10),
    )
    st.plotly_chart(fig, use_container_width=True)


def main() -> None:
    st.set_page_config(page_title="财报标注工作台", layout="wide")
    st.title("财报标注工作台")

    conn = store.connect(read_only=True)
    try:
        codes = store.list_codes(conn)
    finally:
        conn.close()

    if codes.empty:
        st.warning(
            "库是空的。这个界面不联网，取数由写进程负责：\n\n"
            "```\npython -m fw.fetch --queue\n```\n\n"
            "一家公司约 2 分钟。DuckDB 是单写者，所以取数和看报告必须分进程。"
        )
        st.stop()

    left, right = st.sidebar.columns([1])
    options = {f"{r['code']} {r['name']}": r["code"] for _, r in codes.iterrows()}
    picked = st.sidebar.selectbox("标的", list(options))
    code = options[picked]

    card = load_card(code)

    st.sidebar.markdown(f"**最新年报**：{card.year} 年")
    st.sidebar.caption(f"库内年度：{card.years[0]}–{card.years[-1]}，共 {len(card.years())} 年")

    if card.veto:
        st.error(
            "🚫 **一票否决层命中**：" + "、".join(m.label for m in card.veto)
            + " —— 后续所有比率都建立在不可信的报表上，其余判定仅供参考。"
        )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("规则判定", f"{card.summary['pass']} 通过 / {card.summary['total']}")
    c2.metric("关注", card.summary["warn"])
    c3.metric("警示", card.summary["fail"])
    c4.metric("无法判定", card.summary["na"], help="缺数据即记 N/A，不当成通过")

    s = card.fscore_summary
    st.subheader("Piotroski F-Score")
    st.markdown(f"### {s['score']} / {s['total']}　·　判定覆盖度 {s['judged']}/{s['total']}")
    if s["na"]:
        st.warning(f"{s['na']} 条因数据不足无法判定，得分已被低估")
    fs = pd.DataFrame(
        [{"规则": r.label, "结果": r.status, "证据": r.evidence, "备注": r.note} for r in card.fscore]
    )
    st.dataframe(fs, hide_index=True, use_container_width=True)

    st.subheader("逐年趋势")
    trend_chart(card.trend)
    st.dataframe(card.trend.round(3), use_container_width=True)

    st.subheader("指标标注卡")
    st.caption("点任意一行展开：公式、原值、阈值出处、判定理由、以及这个指标该怎么读")
    groups = card.by_group()
    tabs = st.tabs(list(groups))
    for name, tab in zip(groups, tabs):
        with tab:
            for m in groups[name]:
                render_metric(m)

    st.divider()
    st.markdown("**交给 AI 分析**")
    st.caption(
        "本工具不做 AI 调用——判定必须由代码给出，可复现。"
        "把结构化卡片交给 agent 层做解读，与归因管线的职责划分一致。"
    )
    st.download_button(
        "下载卡片 JSON",
        data=pd.DataFrame(
            [
                {
                    "engine": m.engine, "key": m.key, "label": m.label, "value": m.value,
                    "unit": m.unit, "verdict": m.verdict, "verdict_by": m.verdict_by,
                    "reason": m.verdict_reason, "formula": m.formula, "drill": m.drill,
                    "learn": " ".join(m.learn), "period": m.period,
                }
                for m in card.metrics
            ]
        ).to_json(orient="records", force_ascii=False).encode(),
        file_name=f"{card.code}_{card.year}_card.json",
        mime="application/json",
    )


if __name__ == "__main__":
    main()
