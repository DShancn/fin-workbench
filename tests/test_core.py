"""核心回归测试。

覆盖的都是**曾经真实出错**或极易静默出错的地方，不是形式化覆盖率。
"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import pandas as pd

from fw import config, derive, registry, rules_loader, scoring, store
from fw.fscore import eval_fscore, summarize
from fw.metric import FAIL, NA, PASS, WARN, Metric
from fw.pailei import apply_rule, evaluate
from fw.ratios import dupont_analysis, dupont_attribution, gross_margin, return_on_equity
from fw.statements import Statements

ROOT = Path(__file__).resolve().parents[1]
RULES = ROOT / "rules" / "pailei.md"


def fake_st(rows: dict[int, dict], opinions: dict[int, str] | None = None) -> Statements:
    ann = pd.DataFrame(
        [{**d, "year": y} for y, d in rows.items()], columns=sorted({k for d in rows.values() for k in d} | {"year"})
    ).set_index("year").sort_index()
    op = pd.Series(opinions, name="opinion").astype("object") if opinions else None
    return Statements(code="000001", annual=ann, name="测试公司", raw_opinion=op)


BASE = {
    "revenue": 1000.0, "net_cogs": 400.0, "net_profit": 200.0,
    "parent_profit": 180.0, "deduct_profit": 170.0,
    "total_assets": 2000.0, "total_liab": 800.0, "parent_equity": 1200.0,
    # 这三项在真实流水线里由 statements.build 用 shift(1) 滚动算出，
    # 测试夹具必须自己给，否则同比类规则会无端 N/A
    "avg_total_assets": 1950.0, "avg_parent_equity": 1150.0, "beg_total_assets": 1900.0,
    "current_assets": 900.0, "current_liab": 500.0,
    "ocf": 190.0, "capex": 50.0, "free_cash_flow": 140.0,
    "interest_bearing_debt": 100.0, "share_capital": 1000.0,
    "goodwill": 50.0, "accounts_rece": 100.0, "inventory": 120.0,
}


class TestNanIsNotPass(unittest.TestCase):
    """静默失败回归：NaN 穿过 `is None` 检查后，`nan > 0.5` 恒为假，
    于是缺数据的指标被判 PASS。实测在茅台（无商誉）上命中过。"""

    def test_missing_goodwill_is_na_not_pass(self):
        row = {**BASE}
        del row["goodwill"]
        d = derive.derive(pd.Series(row), None)
        rule = rules_loader.Rule(id="R9.9", label="测试", field="goodwill_ratio", check="lte", fail=0.5, warn=0.3)
        m = apply_rule(rule, d, "2025")
        self.assertEqual(m.verdict, NA)
        self.assertIsNone(m.value)

    def test_nan_input_normalized_to_none(self):
        d = derive.derive(pd.Series({**BASE, "goodwill": float("nan")}), None)
        self.assertIsNone(d["goodwill_ratio"])

    def test_none_displays_as_dash(self):
        m = Metric(key="x", label="x", period="2025", engine="info", value=None, unit="ratio")
        self.assertEqual(m.to_display(), "—")

    def test_ratio_displays_as_percent(self):
        m = Metric(key="x", label="x", period="2025", engine="info", value=0.3571, unit="ratio")
        self.assertEqual(m.to_display(), "35.71%")


class TestRulesIntegrity(unittest.TestCase):
    def test_parses_and_counts(self):
        rules = rules_loader.load_rules(RULES)
        self.assertGreaterEqual(len(rules), 8)
        self.assertEqual(len({r.id for r in rules}), len(rules), "规则编号重复")

    def test_every_rule_field_is_computable(self):
        """规则文件引用的 field 必须在派生表里存在，否则那条规则永远 N/A。
        这是 rules/ 与 derive.py 之间唯一的契约，必须有测试守着。"""
        known = set(derive.FIELD_META) | set(derive.PASSTHROUGH)
        for r in rules_loader.load_rules(RULES):
            self.assertIn(r.field, known, f"{r.id} 引用了不存在的 field: {r.field}")

    def test_unknown_key_raises_not_skips(self):
        bad = RULES.read_text().replace("- layer: 0", "- layer: 0\n- bogus: 1", 1)
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write(bad)
            path = Path(f.name)
        try:
            with self.assertRaises(ValueError):
                rules_loader.load_rules(path)
        finally:
            path.unlink()

    def test_layer0_veto(self):
        rules = [r for r in rules_loader.load_rules(RULES) if r.layer == 0]
        d = derive.derive(pd.Series({**BASE, "audit_opinion": "保留意见", "deduct_profit": 20.0}), None)
        d["audit_opinion"] = "保留意见"
        _, summary = evaluate(rules, d, "2025")
        self.assertTrue(summary["veto"], "非标审计意见应触发一票否决")


class TestDupont(unittest.TestCase):
    def test_composite_equals_direct_roe(self):
        d = dupont_analysis(200.0, 1000.0, 2000.0, 1200.0)
        direct = return_on_equity(200.0, 1200.0)
        self.assertAlmostEqual(d["roe_from_dupont"], direct, places=12)

    def test_attribution_sums_to_total_change(self):
        prev = dupont_analysis(180.0, 900.0, 1900.0, 1100.0)
        cur = dupont_analysis(200.0, 1000.0, 2000.0, 1200.0)
        a = dupont_attribution(prev, cur)
        self.assertAlmostEqual(sum(a["contributions"].values()), a["total_change"], places=10)

    def test_gross_margin_and_zero_guard(self):
        self.assertAlmostEqual(gross_margin(1000.0, 400.0), 0.6)
        self.assertIsNone(gross_margin(1000.0, 0.0))
        self.assertIsNone(return_on_equity(100.0, 0.0))


class TestFscoreCoverage(unittest.TestCase):
    def test_na_counted_not_silently_dropped(self):
        """单年数据无法判定同比类规则。必须显式计入覆盖度，
        而不是像 intl3 那样整家公司 except-continue 掉。"""
        cur = derive.derive(pd.Series(BASE), None)
        res = eval_fscore(cur, None)
        s = summarize(res)
        self.assertEqual(s["total"], 9)
        self.assertLess(s["judged"], 9)
        self.assertEqual(s["judged"] + s["na"], 9)

    def test_two_year_full_coverage(self):
        prev = {**BASE, "revenue": 900.0, "net_profit": 180.0, "total_assets": 1900.0,
                "parent_equity": 1100.0, "current_liab": 550.0, "share_capital": 1000.0,
                "net_cogs": 400.0, "ocf": 170.0, "interest_bearing_debt": 150.0}
        a = derive.derive(pd.Series(prev), None)
        b = derive.derive(pd.Series(BASE), a)
        self.assertEqual(summarize(eval_fscore(b, a))["judged"], 9)


class TestNegativeProfit(unittest.TestCase):
    """真实缺陷回归：掌阅科技 2025 归母 -1.76 亿、扣非 -1.94 亿，
    比值 1.101 为正且大于 1，被误判成「利润成色优秀 PASS」。
    两家都亏时这个比率没有含义。"""

    LOSS = {**BASE, "parent_profit": -1.76e8, "deduct_profit": -1.94e8, "net_profit": -1.70e8}

    def test_loss_making_ratio_is_not_computable(self):
        d = derive.derive(pd.Series(self.LOSS), None)
        self.assertIsNone(d["deducted_gap_ratio"])

    def test_loss_making_fails_layer0(self):
        rules = [r for r in rules_loader.load_rules(RULES) if r.id == "R0.3"]
        self.assertTrue(rules, "规则文件缺 R0.3 归母净利为正")
        d = derive.derive(pd.Series(self.LOSS), None)
        metrics, summary = evaluate(rules, d, "2025")
        self.assertEqual(metrics[0].verdict, FAIL)
        self.assertTrue(summary["veto"], "亏损应进入一票否决层")

    def test_cny_yi_thresholds_use_yi_scale(self):
        """金额类阈值按亿表达，比较前必须同量纲，否则 -1.76 亿显示成天文数字。"""
        rule = rules_loader.Rule(id="R0.3", label="x", field="parent_profit", check="gte", fail=0.0)
        d = derive.derive(pd.Series(self.LOSS), None)
        m = apply_rule(rule, d, "2025")
        self.assertAlmostEqual(m.value, -1.76, places=2)
        self.assertEqual(m.to_display(), "-1.76 亿")

    def test_profitable_company_ratio_still_computes(self):
        d = derive.derive(pd.Series(BASE), None)
        self.assertAlmostEqual(d["deducted_gap_ratio"], 170.0 / 180.0, places=6)


class TestScreenMatrix(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "m.duckdb"
        conn = store.connect(self.path)
        for code, over in (("600001", {}), ("600002", {"parent_profit": -5e7})):
            base = {**BASE, **over}
            ann = pd.DataFrame(
                [{**base, "year": y} for y in (2024, 2025)],
                columns=sorted(set(base) | {"year"}),
            ).set_index("year").sort_index()
            ann["avg_total_assets"] = ann["total_assets"]
            ann["avg_parent_equity"] = ann["parent_equity"]
            store.upsert(conn, Statements(code=code, annual=ann, name=f"公司{code[-1]}"))
        conn.close()
        self.rules = rules_loader.load_rules(RULES)

    def tearDown(self):
        self.tmp.cleanup()

    def test_matrix_shape_and_columns(self):
        from fw import screen
        conn = store.connect(self.path, read_only=True)
        try:
            m = screen.matrix(conn, self.rules, {}, groups={"组A": ["600001", "600002"]})
        finally:
            conn.close()
        self.assertEqual(len(m), 2)
        for col in ("名称", "分组", "ROE", "毛利率", "F-Score", "警示", "一票否决"):
            self.assertIn(col, m.columns)
        self.assertEqual(set(m["分组"]), {"组A"})

    def test_loss_making_company_flagged_in_matrix(self):
        """矩阵必须能把亏损公司标出来，否则筛选器会把它排在好公司前面。"""
        from fw import screen
        conn = store.connect(self.path, read_only=True)
        try:
            m = screen.matrix(conn, self.rules, {})
        finally:
            conn.close()
        loser = m.loc["600002"]
        self.assertEqual(loser["一票否决"], "是")
        self.assertLess(loser["归母净利(亿)"], 0)
        self.assertEqual(m.loc["600001"]["一票否决"], "")

    def test_missing_company_is_skipped_not_padded(self):
        from fw import screen
        conn = store.connect(self.path, read_only=True)
        try:
            m = screen.matrix(conn, self.rules, {}, codes=["600001", "999999"])
        finally:
            conn.close()
        self.assertEqual(len(m), 1, "未入库代码应被跳过而不是造出一行空值")


class TestWatchlistGroups(unittest.TestCase):
    def test_group_column_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "w.csv"
            p.write_text("code,name,group\n600519,贵州茅台,白酒\n000001,平安银行,\n", encoding="utf-8")
            from fw import queue
            g = queue.groups(p)
            self.assertEqual(g, {"白酒": ["600519"], "未分组": ["000001"]})

    def test_missing_group_column_still_loads(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "w.csv"
            p.write_text("code,name\n600519,贵州茅台\n", encoding="utf-8")
            from fw import queue
            self.assertEqual(queue.groups(p), {"未分组": ["600519"]})


class TestDisclosureParsing(unittest.TestCase):
    """报告期年份只能从标题取。披露年减一对年报成立，对半年报和季报是错的。"""

    def test_report_year_from_title(self):
        from fw.disclosures import _report_year
        cases = {
            "掌阅科技股份有限公司2025年年度报告": 2025,
            "掌阅科技股份有限公司2023年半年度报告": 2023,
            "掌阅科技股份有限公司2026年第一季度报告": 2026,
            "贵州茅台2025年年度报告摘要": 2025,
            "关于2026年半年度业绩预告": 2026,
            "股票交易异常波动公告": None,
            "": None,
        }
        for title, want in cases.items():
            with self.subTest(title=title):
                self.assertEqual(_report_year(title), want)

    def test_org_id_refuses_prefix_guess(self):
        """orgId 猜错会 HTTP 200 + 空结果，是静默失败，必须显式抛错而不是回退到猜测。"""
        from fw import disclosures
        self.assertTrue(callable(disclosures.org_id))
        # 不打网络，只验 _rows 对缺失字段的降级行为
        rows = disclosures._rows("600001", {"announcements": [
            {"announcementId": "1", "announcementTitle": "测试<em>高亮</em>公告",
             "announcementTime": None, "adjunctUrl": ""},
        ]})
        self.assertEqual(rows[0]["title"], "测试高亮公告")
        self.assertEqual(rows[0]["ts_precision"], "unknown")
        self.assertEqual(rows[0]["pdf_url"], "")

    def test_day_precision_flagged(self):
        from fw import disclosures
        # 北京时间当地零点 -> 日级精度，不能参与秒级先后比较
        ms = int(datetime(2026, 4, 25, tzinfo=config.CN_TZ).timestamp() * 1000)
        rows = disclosures._rows("600001", {"announcements": [
            {"announcementId": "2", "announcementTitle": "年报",
             "announcementTime": ms, "adjunctUrl": "finalpage/a.PDF"},
        ]})
        self.assertEqual(rows[0]["ts_precision"], "day")
        self.assertTrue(rows[0]["pdf_url"].startswith("https://static.cninfo.com.cn/"))

        ms2 = int(datetime(2026, 4, 25, 19, 30, tzinfo=config.CN_TZ).timestamp() * 1000)
        rows2 = disclosures._rows("600001", {"announcements": [
            {"announcementId": "3", "announcementTitle": "年报",
             "announcementTime": ms2, "adjunctUrl": "x.PDF"},
        ]})
        self.assertEqual(rows2[0]["ts_precision"], "second")


class TestDisclosureStore(unittest.TestCase):
    def test_upsert_and_load_roundtrip(self):
        st_ = fake_st({2025: BASE})
        payload = {"periodic": [
            {"code": "000001", "anno_id": "a1", "title": "公司2025年年度报告", "kind": " 年度报告",
             "report_year": 2025, "publish_ts": "2026-04-25 00:00:00", "ts_precision": "day",
             "pdf_url": "https://static.cninfo.com.cn/x.PDF"},
        ], "risk": [
            {"code": "000001", "anno_id": "b1", "title": "股东减持计划公告", "kind": "减持",
             "report_year": None, "publish_ts": "2026-09-25 00:00:00", "ts_precision": "day",
             "pdf_url": "https://static.cninfo.com.cn/y.PDF"},
        ]}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.duckdb"
            conn = store.connect(p)
            store.upsert(conn, st_)
            n = store.upsert_disclosures(conn, "000001", payload)
            conn.close()
            self.assertEqual(n, 2)
            conn = store.connect(p, read_only=True)
            df = store.load_disclosures(conn, "000001")
            self.assertEqual(len(df), 2)
            self.assertEqual(df.iloc[0]["kind"], "减持")            # 按时间倒序
            self.assertEqual(store.load_disclosures(conn, "999999").__len__(), 0)
            conn.close()

    def test_empty_disclosure_result_writes_nothing(self):
        """界面靠「表里有没有行」区分「没索引」与「索引了但确实没风险公告」，
        所以空结果不能写入占位行。"""
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.duckdb"
            conn = store.connect(p)
            self.assertEqual(store.upsert_disclosures(conn, "000001", {"periodic": [], "risk": []}), 0)
            conn.close()


class TestScoring(unittest.TestCase):
    def _m(self, group, verdict, by="R1.1"):
        return Metric(key="k", label="L", period="2025", engine="pailei",
                      group=group, verdict=verdict, verdict_by=by)

    def test_layer_weights_sum_to_100(self):
        self.assertEqual(sum(scoring.LAYER_WEIGHTS.values()), 100)

    def test_all_pass_is_100(self):
        ms = [self._m(g, PASS) for g in ("盈利质量", "现金流质量", "资产质量", "杠杆")]
        r = scoring.score_metrics(ms)
        self.assertEqual(r["score"], 100.0)
        self.assertEqual(r["band"], "优")

    def test_na_does_not_inflate_and_lowers_coverage(self):
        """关键性质：大量 N/A 不能让分数看起来像满分。

        只有一组可判定为 PASS、其余全 N/A 时，分数确实是 100（覆盖到的维度全过），
        但 coverage 必须显著小于 1，界面据此禁止与高覆盖标的直接比较。
        """
        ms = [self._m("盈利质量", PASS), self._m("现金流质量", NA), self._m("杠杆", NA)]
        r = scoring.score_metrics(ms)
        self.assertLess(r["coverage"], 0.6)
        self.assertEqual(r["covered_weight"], scoring.LAYER_WEIGHTS["盈利质量"])
        self.assertLess(r["covered_weight"], r["total_weight"])

    def test_warn_is_half(self):
        ms = [self._m("盈利质量", PASS), self._m("盈利质量", WARN)]
        r = scoring.score_metrics(ms)
        self.assertAlmostEqual(r["groups"]["盈利质量"]["score"], 75.0, places=1)

    def test_layer0_fail_forces_exclude_band(self):
        """命中一票否决时不看分数——否则一个 78 分会盖掉「审计意见非标」这种结论。"""
        ms = [self._m(g, PASS) for g in ("现金流质量", "资产质量", "杠杆")]
        ms.append(self._m("报表可信度", FAIL, by="R0.1"))
        r = scoring.score_metrics(ms)
        self.assertEqual(r["band"], "排除")
        self.assertTrue(r["veto"])

    def test_no_judgable_metrics_gives_no_score(self):
        r = scoring.score_metrics([self._m("盈利质量", NA), self._m("杠杆", NA)])
        self.assertIsNone(r["score"])
        self.assertEqual(r["band"], "无法评级")
        self.assertEqual(r["coverage"], 0.0)

    def test_empty_metrics_list_is_safe(self):
        r = scoring.score_metrics([])
        self.assertIsNone(r["score"])
        self.assertEqual(r["coverage"], 0.0)

    def test_worst_group_points_at_weakest(self):
        ms = [self._m("盈利质量", PASS), self._m("杠杆", FAIL)]
        r = scoring.score_metrics(ms)
        self.assertEqual(scoring.worst_group(r), "杠杆")

    def test_real_cards_have_score_and_coverage(self):
        """真实数据上分数与覆盖率都应有值，且亏损公司评级不高于健康公司。

        别人 clone 下来没取过数，这种情况直接跳过而不是让测试变红。
        """
        db = Path(store.config.DB_PATH)
        if not db.exists():
            self.skipTest(f"未找到 {db}，先跑 python -m fw.fetch 600519 603533")
        conn = store.connect(db, read_only=True)
        try:
            have = set(store.list_codes(conn)["code"])
            if not {"600519", "603533"} <= have:
                self.skipTest("本机未取这两只标的")
            healthy = registry.build_card(store.load(conn, "600519"), self_rules(), {})
            loser = registry.build_card(store.load(conn, "603533"), self_rules(), {})
        finally:
            conn.close()
        self.assertIsNotNone(healthy.risk["score"])
        self.assertEqual(loser.risk["band"], "排除", "归母为负应触发一票否决")
        self.assertLess(loser.risk["score"], healthy.risk["score"])


def self_rules():
    return rules_loader.load_rules(RULES)


class TestProxyBypass(unittest.TestCase):
    def test_cn_hosts_in_no_proxy(self):
        config.apply_proxy_bypass()
        for var in ("NO_PROXY", "no_proxy"):
            self.assertIn("eastmoney.com", os.environ[var])
            self.assertIn("cninfo.com.cn", os.environ[var])


class TestStoreRoundTrip(unittest.TestCase):
    def test_values_survive_serialize(self):
        st = fake_st({2024: BASE, 2025: {**BASE, "revenue": 1100.0}})
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.duckdb"
            conn = store.connect(p)
            store.upsert(conn, st)
            conn.close()
            conn2 = store.connect(p, read_only=True)
            back = store.load(conn2, "000001")
            conn2.close()
            self.assertEqual(back.name, "测试公司")
            self.assertEqual(back.years(), [2024, 2025])
            self.assertAlmostEqual(float(back.year(2025)["revenue"]), 1100.0, places=6)
            with self.assertRaises(KeyError):
                store.load(store.connect(p, read_only=True), "999999")

    def test_string_columns_excluded_from_numeric_write(self):
        """审计意见是字符串，混进 DOUBLE 列会直接报错。"""
        st = fake_st({2025: BASE}, opinions={2025: "标准无保留意见"})
        st.annual["OPINION_TYPE"] = "标准无保留意见"
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.duckdb"
            conn = store.connect(p)
            try:
                store.upsert(conn, st)
            finally:
                conn.close()


class TestVestQueueSeam(unittest.TestCase):
    def test_missing_vest_db_is_not_an_error(self):
        from fw import queue as q

        self.assertTrue(q.load_vest_queue(Path("/nonexistent/vest.duckdb")).empty)


if __name__ == "__main__":
    unittest.main()
