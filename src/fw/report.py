"""导出独立 HTML 报告。

Streamlit 界面需要一个在跑的服务；这里产出的 HTML 是**自包含单文件**：
无 CDN、无 JS 依赖、断网可开、能直接发给别人或存进浏览器书签。

交互靠原生 <details> 实现点击展开注释——这正是标注卡的核心交互，
不需要任何脚本，任何浏览器（包括 Qoder 内置浏览器）都支持。

用法：
    fw-report 600519                 # 单只标的
    fw-report --all                  # 全库 + 一个可点击进入的索引页
"""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path

from . import brief, config, registry, rules_loader, screen, scoring, store

CSS = """
:root{--bg:#fbfbfd;--card:#fff;--ink:#16191d;--muted:#6b7280;--line:#e5e7eb;
--pass:#0a7d3c;--warn:#a15c00;--fail:#b3261e;--na:#6b7280;--rule:#2f5fd0}
*{box-sizing:border-box}
body{margin:0;padding:32px 20px 64px;background:var(--bg);color:var(--ink);
font:15px/1.65 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:960px;margin:0 auto}
h1{font-size:24px;margin:0 0 4px}h2{font-size:17px;margin:34px 0 10px}
.sub{color:var(--muted);font-size:13px;margin-bottom:18px}
.head{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:18px 20px}
.kpis{display:flex;gap:26px;flex-wrap:wrap;margin-top:12px}
.kpi{min-width:84px}.kpi b{display:block;font-size:22px;font-weight:650}
.kpi span{font-size:11.5px;color:var(--muted)}
.veto{background:#fdecea;border:1px solid #f5c2bd;border-radius:8px;padding:12px 16px;margin:16px 0}
.veto b{color:var(--fail)}
.grp{margin:26px 0 8px;font-size:13px;color:var(--muted);
border-bottom:1px solid var(--line);padding-bottom:6px}
details{background:var(--card);border:1px solid var(--line);border-radius:8px;
margin:8px 0;padding:0 16px}
details[open]{padding-bottom:12px}
summary{cursor:pointer;padding:11px 0;font-weight:570;list-style:none;
display:flex;justify-content:space-between;gap:12px;align-items:baseline}
summary::-webkit-details-marker{display:none}
summary:before{content:"▸";color:var(--muted);margin-right:8px;font-size:12px}
details[open] summary:before{content:"▾"}
.v{font-variant-numeric:tabular-nums;color:var(--muted);font-weight:400;font-size:14px}
.p{font-weight:700;font-size:12px;padding:1px 7px;border-radius:20px;color:#fff}
.PASS{background:var(--pass)}.WARN{background:var(--warn)}
.FAIL{background:var(--fail)}.NA{background:var(--na)}
.row{padding:6px 0;border-top:1px solid #f1f2f4;font-size:14px}
.row b{font-weight:560;color:#374151;font-size:12.5px;display:block;margin-bottom:2px}
.formula{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px;color:#374151}
.th{background:#eef3fd;border-left:3px solid var(--rule);padding:9px 12px;
border-radius:0 6px 6px 0;margin:6px 0;font-size:13.5px}
.th .tag{font-size:10.5px;padding:1px 6px;border-radius:4px;background:var(--rule);color:#fff}
.th.tb .tag{background:#7b8794}.th.tb{background:#f4f5f7;border-left-color:#9ca3af}
.learn{background:#fffdf5;border-left:3px solid #e0c060;padding:9px 12px;
border-radius:0 6px 6px 0;margin:6px 0;font-size:13.5px;color:#3f3a2c}
.why{color:#4b5563}
.drill{font-size:12.5px;color:var(--muted)}
table{border-collapse:collapse;width:100%;font-size:13px;background:var(--card)}
th,td{border:1px solid var(--line);padding:7px 9px;text-align:right;font-variant-numeric:tabular-nums}
th{background:#f4f5f7;font-weight:570}td:first-child,th:first-child{text-align:left}
a{color:var(--rule);text-decoration:none}a:hover{text-decoration:underline}
.ann{font-size:13.5px;padding:5px 0;border-bottom:1px solid #f1f2f4}
.foot{margin-top:36px;padding-top:14px;border-top:1px solid var(--line);
font-size:12px;color:var(--muted)}
.warnbox{background:#fffbeb;border:1px solid #fde68a;border-radius:8px;
padding:10px 14px;margin:14px 0;font-size:13.5px}
.idx td{text-align:left}.idx .s{font-weight:650}
"""

VERDICT_CN = {"PASS": "通过", "WARN": "关注", "FAIL": "警示", "N/A": "无法判定", None: "未判定"}


def e(x) -> str:
    return html.escape("" if x is None else str(x))


def _kpi(m: dict) -> str:
    cells = []
    for label, key, fmt in (
        ("风险分", "score", "{:.1f}"),
        ("评级", "band", "{}"),
        ("F-Score", "fscore", "{}/9"),
        ("判定覆盖", "cov", "{:.0%}"),
    ):
        v = m.get(key)
        if v is None:
            continue
        cells.append(f'<div class="kpi"><b>{e(fmt.format(v))}</b><span>{label}</span></div>')
    return '<div class="kpis">' + "".join(cells) + "</div>"


def _metric_html(m: dict) -> str:
    badge = f'<span class="p {e(m["verdict"] or "NA")}">{e(VERDICT_CN.get(m["verdict"], ""))}</span>'
    head = (f'<summary><span>{e(m["label"])} '
            f'<span class="v">= {e(m["display"])}</span></span>'
            f'<span>{badge} <span class="v">{e(m["rule"])}</span></span></summary>')

    rows = []
    if m.get("formula"):
        rows.append(f'<div class="row"><b>怎么算的</b><span class="formula">{e(m["formula"])}</span></div>')
    if m.get("inputs"):
        items = "；".join(
            f"{e(k)} = {v/1e8:,.2f} 亿" if isinstance(v, (int, float)) and abs(v) > 1e6
            else f"{e(k)} = {v:,.4f}" if isinstance(v, (int, float)) else f"{e(k)} = {e(v)}"
            for k, v in m["inputs"].items()
        )
        rows.append(f'<div class="row"><b>参与计算的原值</b>{items}</div>')
    for t in m.get("thresholds", []):
        cls = "" if t["kind"] == "user_rule" else " tb"
        tag = "你的规则" if t["kind"] == "user_rule" else "教材参考值"
        rows.append(
            f'<div class="th{cls}"><span class="tag">{tag}</span> '
            f'{e(t["label"] or "阈值")} = <b>{e(t["value"])}</b>'
            f'<span class="drill">　出处 {e(t["origin"])}</span></div>'
        )
    if m.get("reason"):
        rows.append(f'<div class="row"><b>判定理由</b>{e(m["reason"])}</div>')
    for note in m.get("learn", []):
        rows.append(f'<div class="learn"><b class="why">为什么这条存在</b><br>{e(note)}</div>')
    if m.get("drill"):
        rows.append(f'<div class="row drill">🔍 去年报核对：{e(m["drill"])}</div>')
    return f"<details>{head}{''.join(rows)}</details>"


def render(b: dict) -> str:
    r = b["risk"]
    vetoed = bool(r.get("veto"))
    parts = [f"<!DOCTYPE html><html lang=zh-CN><meta charset=utf-8>"
             f"<meta name=viewport content='width=device-width,initial-scale=1'>"
             f"<title>{e(b['name'])} {b['year']} 财报标注</title><style>{CSS}</style><div class=wrap>"]

    parts.append(
        f"<div class=head><h1>{e(b['name'])}　<span class=v>{e(b['code'])}</span></h1>"
        f"<div class=sub>{b['year']} 年年报 · 库内 {b['years_available'][0]}–{b['years_available'][1]}"
        f" 共 {b['years_available'][1] - b['years_available'][0] + 1} 年 · 生成于 {e(b['generated_at'])}>"
        + _kpi({
            "score": None if vetoed else r.get("score"),
            "band": r.get("band"), "fscore": b["fscore"]["summary"]["score"],
            "cov": r.get("coverage"),
        }) + "</div>"
    )

    if vetoed:
        items = "；".join(f"{e(v['label'])}（{e(v['rule'])}）：{e(v['reason'])}" for v in r["veto"])
        parts.append(f'<div class=veto><b>⛔ 一票否决</b>　{items}<br>'
                     f'<span class=drill>被排除的标的不进入 0-100 的可比评分，'
                     f'下方各维度的通过情况仅供参考，不构成「整体健康」。</span></div>')

    if b.get("coverage_warnings"):
        parts.append('<div class=warnbox><b>使用限制</b><br>'
                     + "<br>".join(e(w) for w in b["coverage_warnings"]) + "</div>")

    att = (b["dupont"] or {}).get("attribution")
    if att:
        contrib = "；".join(
            f"{e({'net_profit_margin': '销售净利率', 'asset_turnover': '总资产周转率', 'equity_multiplier': '权益乘数'}.get(k, k))} "
            f"{v * 100:+.2f}pp"
            for k, v in (att.get("contributions") or {}).items()
        )
        parts.append(f"<h2>ROE 变动归因</h2><div class=head><div>总变动 "
                     f"<b>{att.get('total_change', 0) * 100:+.2f}pp</b>　主导因子 "
                     f"<b>{e(att.get('dominant'))}</b><br><span class=drill>{contrib}</span></div></div>")

    parts.append("<h2>指标标注</h2><div class=sub>点任意一行展开：公式、原值、阈值出处、判定理由，"
                 "以及这个指标该怎么读</div>")
    bygroup: dict[str, list] = {}
    for m in b["metrics"]:
        bygroup.setdefault(m.get("group") or "其他", []).append(m)
    for g, ms in bygroup.items():
        parts.append(f'<div class=grp>{e(g)}</div>')
        parts.extend(_metric_html(m) for m in ms)

    if not b["trend"].get("rows"):
        pass
    else:
        tr = b["trend"]
        parts.append("<h2>逐年趋势</h2><table><tr>" + "".join(f"<th>{e(c)}</th>" for c in tr["columns"]) + "</tr>")
        for row in tr["rows"]:
            parts.append("<tr>" + "".join(
                f"<td>{e(v) if not isinstance(v, float) else f'{v:,.3f}'}</td>" for v in row) + "</tr>")
        parts.append("</table>")

    parts.append("<h2>原始披露核对</h2>")
    if b.get("source_pdf"):
        sp = b["source_pdf"]
        extra = "　<span class=drill>⚠️ 该公告仅日级时间精度</span>" if sp["ts_precision"] == "day" else ""
        parts.append(f'<div class=ann>📄 <a href="{e(sp["url"])}" target=_blank rel=noopener>'
                     f'{e(sp["title"])}</a>　披露于 {e(sp["published"])}{extra}</div>')
    else:
        parts.append('<div class=ann>未索引到本期年报正文，跑 <code>fw-fetch &lt;代码&gt;</code> 可补齐</div>')
    if b.get("risk_announcements"):
        parts.append(f'<div class=ann style=margin-top:12px><b>风险与异动类公告 {len(b["risk_announcements"])} 条</b>'
                     f'<br><span class=drill>产业资本与监管方的表态，与二级市场情绪经常相反。</span></div>')
        for a in b["risk_announcements"]:
            parts.append(f'<div class=ann>{e(a["date"])} <span class=v>[{e(a["kind"])}]</span> '
                         f'<a href="{e(a["url"])}" target=_blank rel=noopener>{e(a["title"])}</a></div>')

    parts.append(f'<div class=foot>判定全部由代码给出，规则定义见 <code>{e(b["authority"]["rules_file"])}</code>；'
                 f'知识注解见 <code>{e(b["authority"]["knowledge_file"])}</code>。<br>'
                 f'本页自包含、无外部依赖、可离线打开。分数仅表示所列规则未报警，'
                 f'不涉及成长性、护城河或估值，不构成投资建议。</div></div>')
    return "".join(parts)


def report_one(code: str, outdir: Path, rules) -> Path:
    b = brief.build(code, rules)
    outdir.mkdir(parents=True, exist_ok=True)
    path = outdir / f"{b['code']}.html"
    path.write_text(render(b), encoding="utf-8")
    return path


def render_index(rows: list[dict], generated: str) -> str:
    head = ("<tr><th>代码</th><th>名称</th><th>分组</th><th>风险分</th><th>评级</th>"
            "<th>覆盖</th><th>F-Score</th><th>警示</th><th>短板</th></tr>")
    body = []
    for r in rows:
        cls = ' class="s"' if r["band"] == "排除" else ""
        score = "—" if r["score"] is None else f"{r['score']:.1f}"
        body.append(
            f"<tr><td><a href='{e(r['file'])}'>{e(r['code'])}</a></td><td>{e(r['name'])}</td>"
            f"<td>{e(r['group'])}</td><td{cls}>{e(score)}</td><td>{e(r['band'])}</td>"
            f"<td>{(r['coverage'] or 0):.0%}</td><td>{r['fscore']}/9</td>"
            f"<td>{r['fail']}</td><td>{e(r['weak'])}</td></tr>"
        )
    return (f"<!DOCTYPE html><html lang=zh-CN><meta charset=utf-8>"
            f"<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>自选池财报体检索引</title><style>{CSS}</style><div class=wrap>"
            f"<div class=head><h1>自选池财报体检</h1>"
            f"<div class=sub>共 {len(rows)} 只 · 按风险分降序 · 生成于 {e(generated)}<br>"
            f"点代码进入该标的的完整标注页。风险分为空的表示命中一票否决，该分数不再可比。</div></div>"
            f"<table class=idx>{head}{''.join(body)}</table>"
            f"<div class=foot>判定由代码给出，规则见 rules/pailei.md。不构成投资建议。</div></div>")


def report_all(outdir: Path, rules) -> Path:
    conn = store.connect(read_only=True)
    try:
        codes = store.list_codes(conn)["code"].tolist()
        groups = {c: g for g, lst in registry_groups().items() for c in lst}
    finally:
        conn.close()

    outdir.mkdir(parents=True, exist_ok=True)
    rows = []
    for code in codes:
        path = report_one(code, outdir, rules)
        b = brief.build(code, rules)
        r = b["risk"]
        rows.append({
            "code": b["code"], "name": b["name"], "group": groups.get(b["code"], ""),
            "score": None if r.get("veto") else r.get("score"), "band": r.get("band"),
            "coverage": r.get("coverage"), "fscore": b["fscore"]["summary"]["score"],
            "fail": sum(1 for m in b["metrics"] if m["verdict"] == "FAIL"),
            "weak": scoring.worst_group(r) or "", "file": path.name,
        })
    rows.sort(key=lambda x: -1 if x["score"] is None else x["score"])
    idx = outdir / "index.html"
    idx.write_text(render_index(rows, datetime.now(config.CN_TZ).strftime("%Y-%m-%d %H:%M")),
                   encoding="utf-8")
    return idx


def registry_groups() -> dict[str, list[str]]:
    from . import queue as queue_mod
    return queue_mod.groups()


def main(argv=None) -> None:
    import argparse

    p = argparse.ArgumentParser(prog="fw-report", description="导出自包含 HTML 标注报告")
    p.add_argument("code", nargs="?", help="6 位代码；用 --all 时省略")
    p.add_argument("--all", action="store_true", help="全库导出并生成索引页")
    p.add_argument("--out", default=str(config.data_dir() / "reports"), help="输出目录")
    args = p.parse_args(argv)

    rules = rules_loader.load_rules(config.RULES_PATH)
    outdir = Path(args.out).expanduser()
    try:
        if args.all:
            idx = report_all(outdir, rules)
            print(f"已导出全库报告，索引页：{idx}")
        elif args.code:
            print(f"已导出：{report_one(args.code, outdir, rules)}")
        else:
            p.error("给一个代码，或用 --all 导出全库")
    except (FileNotFoundError, KeyError) as exc:
        p.error(str(exc))


if __name__ == "__main__":
    main()
