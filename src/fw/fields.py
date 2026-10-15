"""akshare 东财三表 -> canonical 字段名映射。

规则引擎只认 canonical 名，不认数据源列名。这是刻意与国内1号的做法反着来：
它的排雷规则直接把 Tushare 列名写进了规则文本（checklist-rules.md 每条的
`数据源:` 段），结果规则文件与数据源死死绑住，换 akshare 就得逐条改规则。
"""

from __future__ import annotations

# 东财按报告期接口返回的是长表：一行 = 一个报告期，列 = 科目代码。
# 下列映射经 2026-10-15 对 sh600519 实测确认，未命中的键会取首个包含匹配。

PROFIT_MAP = {
    "revenue": "TOTAL_OPERATE_INCOME",      # 营业总收入
    "net_cogs": "OPERATE_COST",              # 营业成本
    "net_profit": "NETPROFIT",               # 净利润（含少数股东）
    "parent_profit": "PARENT_NETPROFIT",     # 归母净利润
    "deduct_profit": "DEDUCT_PARENT_NETPROFIT",  # 扣非归母
    "operate_profit": "OPERATE_PROFIT",      # 营业利润
    "total_profit": "TOTAL_PROFIT",          # 利润总额（税前）
    "sell_expense": "SALE_EXPENSE",          # 东财拼写是 SALE 不是 SELL
    "manage_expense": "MANAGE_EXPENSE",
    "rd_expense": "RESEARCH_EXPENSE",
    "finance_expense": "FINANCE_EXPENSE",
    "basic_eps": "BASIC_EPS",
    "diluted_eps": "DILUTED_EPS",
    "notice_date": "NOTICE_DATE",            # 披露日期，溯源用
}

BALANCE_MAP = {
    "total_assets": "TOTAL_ASSETS",
    "total_liab": "TOTAL_LIABILITIES",
    "total_equity": "TOTAL_EQUITY",
    "parent_equity": "TOTAL_PARENT_EQUITY",
    "current_assets": "TOTAL_CURRENT_ASSETS",
    "current_liab": "TOTAL_CURRENT_LIAB",
    "inventory": "INVENTORY",
    "accounts_rece": "ACCOUNTS_RECE",
    "goodwill": "GOODWILL",
    "fixed_asset": "FIXED_ASSET",
    "short_loan": "SHORT_LOAN",
    "long_loan": "LONG_LOAN",
    "bond_payable": "BOND_PAYABLE",
    "liab_1year": "NONCURRENT_LIAB_1YEAR",  # 一年内到期的非流动负债
    "undistributed_profit": "UNASSIGN_RPOFIT",  # 东财列名本身拼写有误（RPOFIT）
    # F-Score 第7条「未增发」。1元面值下 SHARE_CAPITAL 数值即股本数，
    # 实测 600519 为 1,256,197,800 与真实总股本一致。
    # 国际3号用的是 FMP 的 Weighted Average Shares Out，akshare 三表无加权口径，
    # 这里退化为期末注册股本——对「是否增发」这个二值判定足够，
    # 但不能再拿它去反推每股指标。
    "share_capital": "SHARE_CAPITAL",
    "notice_date": "NOTICE_DATE",
}

CASH_MAP = {
    "ocf": "NETCASH_OPERATE",               # 经营活动现金流净额
    "icf": "NETCASH_INVEST",
    "fcf_fin": "NETCASH_FINANCE",
    "capex": "CONSTRUCT_LONG_ASSET",        # 购建长期资产支付的现金
    "end_cash": "END_CASH",
    "notice_date": "NOTICE_DATE",
}

STATEMENT_MAPS = {"profit": PROFIT_MAP, "balance": BALANCE_MAP, "cash": CASH_MAP}

# 有息负债 = 短期借款 + 一年内到期 + 长期借款 + 应付债券。
# 国内1号/国际3号的杠杆判定只用了长期借款一项（intl3 dLeverage 只看 Long Term Debt），
# 对A股会系统性低估去杠杆幅度，这里合并成单一 canonical 口径。
INTEREST_BEARING_DEBT = ("short_loan", "liab_1year", "long_loan", "bond_payable")

AUDIT_OPINION_COLUMN = "OPINION_TYPE"  # 东财三表自带，实测值如「标准无保留意见」「未经审计」
