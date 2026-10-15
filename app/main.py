"""财报标注工作台。

三个视图共用同一份 Card / 矩阵数据，不各自复算：
  标注卡  单公司，点数字看公式、原值、阈值出处、判定理由、怎么读、去哪核对
  筛选    全库矩阵，按维度排序与过滤
  对比    自选分组内横向比较，附组内均值与分位

判定全部来自 rules/pailei.md 的代码引擎；界面只展示，不判断。
AI 分析不在这里做，导出结构化卡片交给 agent 层。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd  # noqa: E402
import plotly.express as px  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

from fw import queue as queue_mod, registry, rules_loader, screen, store  # noqa: E402
from fw.metric import FAIL, NA, PASS, WARN  # noqa: E402

RULES_FILE = ROOT / "rules" / "pailei.md"
NOTES_FILE = ROOT / "rules" / "notes.md"

VERDICT_STYLE = {
    PASS: ("🟢", "通过"), WARN: ("🟡", "关注"),
    FAIL: ("🔴", "警示"), NA: ("⚪", "无法判定"), None: ("⚪", "未判定"),
}
PCT_COLS = ["毛利率", "净利率", "ROE", "现金流/利润", "资产负债率", "有息负债率",
            "商誉/净资产", "扣非/归母", "应收-营收差"]


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
        return registry.build_card(store.load(conn, code), load_rules(), load_notes())
    finally:
        conn.close()


@st.cache_data(show_spinner=False)
def load_matrix():
    conn = store.connect(read_only=True)
    try:
        return screen.matrix(conn, load_rules(), load_notes(), groups=queue_mod.groups())
    finally:
        conn.close()


def refresh() -> None:
    load_card.clear()
    load_matrix.clear()


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
            st.dataframe(
                pd.DataFrame(
                    [{"项目": k, "数值": f"{v/1e8:,.3f} 亿" if abs(v) > 1e6 else round(v, 4)}
                     for k, v in m.inputs.items()]
                ),
                hide_index=True, use_container_width=True,
            )
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
    for col in ("营业总收入", "归母净利润", "经营现金流"):
        if col in trend:
            fig.add_trace(go.Bar(x=trend.index, name=col, y=trend[col]))
    for col in ("毛利率", "ROE", "现金流/利润"):
        if col in trend:
            fig.add_trace(go.Scatter(
                x=trend.index, y=trend[col] * 100, name=f"{col}%",
                yaxis="y2", mode="lines+markers"))
    fig.update_layout(
        height=420, barmode="group", yaxis=dict(title="亿元"),
        yaxis2=dict(title="%", overlaying="y", side="right"),
        legend=dict(orientation="h", y=-0.25), margin=dict(t=30, b=10),
    )
    st.plotly_chart(fig, use_container_width=True)


@st.cache_data(show_spinner=False)
def load_disclosures(code: str):
    conn = store.connect(read_only=True)
    try:
        if not store.has_data(conn, code):
            return pd.DataFrame()
        return store.load_disclosures(conn, code)
    finally:
        conn.close()


def page_card(conn) -> None:
    codes = store.list_codes(conn)
    options = {f"{r['code']} {r['name']}": r["code"] for _, r in codes.iterrows()}
    labels = list(options)
    # ?code=603533 直达某只标的，便于分享与复现
    want = st.query_params.get("code", "")
    idx = next((i for i, l in enumerate(labels) if options[l] == want), 0)
    picked = st.selectbox("标的", labels, index=idx, key="card_code")
    card = load_card(options[picked])
    st.query_params["code"] = card.code

    if card.veto:
        st.error(
            "🚫 **一票否决层命中**：" + "、".join(m.label for m in card.veto)
            + " —— 后续所有比率都建立在不可信的报表上，其余判定仅供参考。"
        )

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("通过", card.summary["pass"])
    c2.metric("关注", card.summary["warn"])
    c3.metric("警示", card.summary["fail"])
    c4.metric("无法判定", card.summary["na"], help="缺数据即记 N/A，不当成通过")
    s = card.fscore_summary
    c5.metric("F-Score", f"{s['score']}/9", help=f"判定覆盖 {s['judged']}/9")

    st.subheader("Piotroski F-Score")
    if s["na"]:
        st.caption(f"其中 {s['na']} 条因数据不足无法判定，得分已被低估")
    st.dataframe(
        pd.DataFrame([{"规则": r.label, "结果": r.status, "证据": r.evidence, "备注": r.note}
                      for r in card.fscore]),
        hide_index=True, use_container_width=True,
    )

    st.subheader("逐年趋势")
    trend_chart(card.trend)
    st.dataframe(card.trend.round(3), use_container_width=True)

    st.subheader("指标标注卡")
    st.caption("点任意一行展开：公式、原值、阈值出处、判定理由，以及这个指标该怎么读")
    groups_ = card.by_group()
    for name, tab in zip(groups_, st.tabs(list(groups_))):
        with tab:
            for m in groups_[name]:
                render_metric(m)

    st.subheader("原始披露核对")
    st.caption("每个判定都应该能翻回公司自己公告的原文。下面是巨潮索引与 PDF 直链。")
    disc = load_disclosures(card.code)
    if disc.empty:
        st.info(f"尚无公告索引。重跑 `python -m fw.fetch {card.code}` 会一并抓取。")
    else:
        periodic = disc[disc["kind"].str.contains("报告", na=False) & disc["report_year"].notna()]
        cur = periodic[periodic["report_year"] == card.year]
        if not cur.empty:
            r0 = cur.iloc[0]
            note = "　⚠️ 该记录仅日级时间精度，无法判定具体时点" if r0["ts_precision"] == "day" else ""
            st.markdown(
                f"📄 **{card.year} 年报原文**：[{r0['title']}]({r0['pdf_url']})　"
                f"披露于 {str(r0['publish_ts'])[:10]}{note}"
            )
        else:
            st.caption(f"未索引到 {card.year} 年年报正文（可能只有摘要，或公告索引尚未更新）")

        risk = disc[~disc["kind"].str.contains("报告", na=False)].copy()
        if not risk.empty:
            heads = "、".join(sorted(set(risk["kind"].dropna()))[:6])
            with st.expander(f"⚠️ 风险与异动类公告 {len(risk)} 条（{heads}）", expanded=True):
                st.caption(
                    "这些是产业资本与监管方的表态，与二级市场情绪经常相反——"
                    "股价上涨期间出现减持计划或问询函，是价值判断里分量很重的一条。"
                )
                st.dataframe(
                    risk[["publish_ts", "kind", "title", "pdf_url"]].rename(columns={
                        "publish_ts": "披露时间", "kind": "类型",
                        "title": "公告标题", "pdf_url": "PDF 直链"}),
                    use_container_width=True, hide_index=True, height=280,
                    column_config={"PDF 直链": st.column_config.LinkColumn(display_text="打开原文")},
                )
        else:
            st.caption("近窗口内没有减持、异常波动、问询、立案、处罚或业绩预告类公告。")

    st.divider()
    st.markdown("**交给 AI 分析**")
    st.caption("本工具不做 AI 调用——判定必须由代码给出、可复现。把结构化卡片交给 agent 做解读。")
    payload = pd.DataFrame([
        {"engine": m.engine, "key": m.key, "label": m.label, "value": m.value, "unit": m.unit,
         "verdict": m.verdict, "verdict_by": m.verdict_by, "reason": m.verdict_reason,
         "formula": m.formula, "drill": m.drill, "learn": " ".join(m.learn), "period": m.period}
        for m in card.metrics
    ]).to_json(orient="records", force_ascii=False)
    st.download_button("下载卡片 JSON", data=payload.encode(),
                       file_name=f"{card.code}_{card.year}_card.json", mime="application/json")


def page_screen() -> None:
    m = load_matrix()
    if m.empty:
        st.warning("库里还没有数据，先跑 `python -m fw.fetch --queue`")
        return

    st.caption(f"全库 {len(m)} 只。表头点击可排序；行内比率为小数，已按百分比渲染")

    with st.expander("过滤条件", expanded=False):
        f1, f2, f3, f4 = st.columns(4)
        min_roe = f1.slider("ROE ≥", -0.5, 0.6, -0.5, 0.01, format="%.0f%%")
        max_debt = f2.slider("有息负债率 ≤", 0.0, 1.0, 1.0, 0.05, format="%.0f%%")
        min_f = f3.slider("F-Score ≥", 0, 9, 0)
        veto_only = f4.checkbox("排除一票否决的", value=True)
        strict = st.checkbox(
            "严格模式：缺该指标的也一并排除",
            value=False,
            help="默认关闭。银行没有营业成本与流动资产负债概念，有息负债率取不到；"
                 "「未知」不等于「不合格」，把它当否定结论会静默把整类公司挤出结果。",
        )

    def keep(series, threshold, mode):
        """mode='gte' 保留 >= 阈值；'lte' 保留 <= 阈值。NaN 默认通过。"""
        if mode == "gte":
            ok = series.fillna(threshold) >= threshold if strict else (series.isna() | (series >= threshold))
        else:
            ok = series.fillna(threshold) <= threshold if strict else (series.isna() | (series <= threshold))
        return ok

    view = m.copy()
    view = view[keep(view["ROE"], min_roe, "gte")]
    view = view[keep(view["有息负债率"], max_debt, "lte")]
    view = view[keep(view["F-Score"], min_f, "gte")]
    if veto_only:
        view = view[view["一票否决"] != "是"]

    unknown = int(view[["ROE", "有息负债率"]].isna().any(axis=1).sum())

    shown = ["名称", "分组", "年度", "营收(亿)", "归母净利(亿)", "毛利率", "净利率", "ROE",
             "周转率", "权益乘数", "现金流/利润", "资产负债率", "有息负债率",
             "商誉/净资产", "扣非/归母", "流动比率", "F-Score", "通过", "关注", "警示",
             "无法判定", "一票否决", "否决项"]
    cols = [c for c in shown if c in view]
    cfg = {c: st.column_config.NumberColumn(format="%.2f%%") for c in cols if c in PCT_COLS}
    for c in ("营收(亿)", "归母净利(亿)", "经营现金流(亿)"):
        if c in cols:
            cfg[c] = st.column_config.NumberColumn(format="%,.1f")
    st.dataframe(view[cols], use_container_width=True, column_config=cfg, height=560)

    note = f"过滤后 **{len(view)} / {len(m)}** 只"
    if unknown and not strict:
        note += f"　其中 {unknown} 只因行业报表结构不同而缺 ROE/有息负债率，已保留（未知≠不合格）"
    st.caption(note)

    st.subheader("一眼看质地")
    plot = view.dropna(subset=["ROE", "毛利率"]).copy()
    if len(plot) >= 2:
        fig = px.scatter(
            plot, x="毛利率", y="ROE", size="营收(亿)", color="分组",
            hover_name="名称", text="名称",
            labels={"毛利率": "毛利率（定价权）", "ROE": "ROE（资本回报）", "营收(亿)": "营收(亿)"},
        )
        fig.update_traces(textposition="top center", textfont_size=9)
        fig.add_hline(y=0, line_dash="dot", annotation_text="ROE=0 亏损线")
        fig.update_layout(height=520, margin=dict(t=30, b=10))
        st.plotly_chart(fig, use_container_width=True)
        st.caption("横轴毛利率看定价权，纵轴 ROE 看资本回报；右下角是「有毛利但赚不到钱」，左上角多为高杠杆撑起的回报，要回去看权益乘数")
    else:
        st.info("有效样本不足，放宽过滤条件后再看")


def page_compare() -> None:
    m = load_matrix()
    if m.empty:
        st.warning("库里还没有数据")
        return

    groups_ = queue_mod.groups()
    names = [g for g, lst in groups_.items() if any(c in m.index for c in lst)]
    if not names:
        st.info("watchlist.csv 里没有 group 列或该组无数据。格式：code,name,group")
        return

    gname = st.selectbox(
        "对比分组", names,
        index=(names.index(qg) if (qg := st.query_params.get("group", "")) in names else 0),
        key="cmp_group",
    )
    st.query_params["group"] = gname
    codes = [c for c in groups_[gname] if c in m.index]
    sub = m.loc[codes]

    st.caption(f"{gname}：{len(codes)} 只已入库（组内共 {len(groups_[gname])} 只）")
    if len(codes) < 2:
        st.warning("组内不足两只，无法对比。先把该组标的取数入库。")
        return

    metric_cols = [c for c in ["营收(亿)", "归母净利(亿)", "毛利率", "净利率", "ROE",
                              "周转率", "权益乘数", "现金流/利润", "资产负债率",
                              "有息负债率", "商誉/净资产", "扣非/归母", "流动比率", "F-Score"]
                   if c in sub.columns]

    table = sub[["名称"] + metric_cols].copy()
    avg = sub[metric_cols].mean(numeric_only=True)
    table.loc["组内均值"] = avg.round(4)
    # 不用 Styler.background_gradient —— 它依赖 matplotlib，而本项目只需要
    # plotly 出图。相对位置已由下面的条形图表达，这里不再叠一层颜色依赖。
    st.dataframe(table, use_container_width=True, height=340,
                 column_config={
                     c: st.column_config.NumberColumn(format="%.2f%%")
                     for c in metric_cols if c in PCT_COLS
                 })
    st.caption("组内均值是「你这几只的均值」，不是行业均值，别当外部基准用")

    st.subheader("组内横向比较")
    bar_dim = st.selectbox(
        "比较维度",
        [c for c in ("毛利率", "ROE", "净利率", "周转率", "现金流/利润", "有息负债率", "F-Score")
         if c in metric_cols],
    )
    plot_df = sub[["名称", bar_dim]].dropna().sort_values(bar_dim)
    # px.bar 的横向参数是 orientation，写成 orient 会直接 TypeError；
    # 且传 Series 作位置参数不如显式绑轴清楚
    fig = px.bar(
        plot_df, x=bar_dim, y="名称", orientation="h",
        color=bar_dim, color_continuous_scale="RdYlGn",
        text=[f"{v:.1%}" if bar_dim in PCT_COLS else f"{v:.2f}" for v in plot_df[bar_dim]],
        labels={bar_dim: bar_dim, "名称": ""},
    )
    fig.update_traces(textposition="outside")
    fig.update_layout(height=140 + 42 * len(plot_df), showlegend=False,
                     coloraxis_showscale=False, margin=dict(t=30, b=10))
    st.plotly_chart(fig, use_container_width=True)

    st.subheader("质地散点")
    plot = sub.dropna(subset=["ROE", "毛利率"])
    if len(plot) >= 2:
        fig2 = px.scatter(plot, x="毛利率", y="ROE", size="营收(亿)", color="名称",
                          text="名称", labels={"毛利率": "毛利率", "营收(亿)": "营收(亿)"})
        fig2.update_traces(textposition="top center")
        fig2.add_hline(y=float(avg.get("ROE", 0)), line_dash="dot", annotation_text="组内平均 ROE")
        fig2.update_layout(height=460, margin=dict(t=30, b=10))
        st.plotly_chart(fig2, use_container_width=True)
    else:
        st.info("组内 ROE 与毛利率同时可比的标的不足两只（金融业无营业成本，毛利率为 N/A）")


def main() -> None:
    st.set_page_config(page_title="财报标注工作台", layout="wide")
    st.title("财报标注工作台")

    if not store.config.DB_PATH.exists():
        st.warning(
            "库还没建。这个界面不联网，取数由写进程负责：\n\n"
            "```\npython -m fw.fetch --queue\n```\n\n"
            "一家公司约 25 秒。DuckDB 是单写者，所以取数和看报告分两个进程。"
        )
        st.stop()

    conn = store.connect(read_only=True)
    try:
        n = len(store.list_codes(conn))
    finally:
        conn.close()

    st.sidebar.markdown(f"库内 **{n}** 只标的")
    if st.sidebar.button("重新加载数据"):
        refresh()
        st.rerun()
    st.sidebar.caption(
        "取数后用这个按钮刷新。缓存不自动失效——DuckDB 文件被替换后，"
        "旧句柄仍指向原 inode，不刷新会看到过期数据。"
    )

    PAGES = ["标注卡", "筛选", "同业对比"]
    # 支持 ?page=筛选 直达，既方便日常用链接回到某个视图，也让视图可被独立验证
    qp = st.query_params.get("page", "")
    default = PAGES.index(qp) if qp in PAGES else 0
    page = st.sidebar.radio("视图", PAGES, index=default)
    st.query_params["page"] = page
    st.divider()

    if page == "标注卡":
        conn = store.connect(read_only=True)
        try:
            page_card(conn)
        finally:
            conn.close()
    elif page == "筛选":
        page_screen()
    else:
        page_compare()


if __name__ == "__main__":
    main()
