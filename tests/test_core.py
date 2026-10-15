"""核心回归测试。

覆盖的都是**曾经真实出错**或极易静默出错的地方，不是形式化覆盖率。
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from fw import config, derive, registry, rules_loader, store
from fw.fscore import eval_fscore, summarize
from fw.metric import NA, PASS, Metric
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
