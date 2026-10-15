"""DuckDB 存储。

DuckDB 是单写者模型：一个文件同一时刻只能有一个写连接，写连接存在期间其他进程
连不上。而本项目的取数（一家公司约 2 分钟、60+ 个子请求）和看报告是两种完全不同的
访问节奏。所以刻意分成两条路径：

    fetch 进程  -> 写模式，独占
    Streamlit   -> read_only=True，永不写

这也是 Docker 拆两个服务的原因，不是装饰。
"""

from __future__ import annotations

import duckdb
import pandas as pd

from . import config
from .statements import Statements

_SCHEMA = """
CREATE TABLE IF NOT EXISTS company (
    code VARCHAR PRIMARY KEY,
    name VARCHAR,
    fetched_at TIMESTAMP,
    first_year INTEGER,
    last_year  INTEGER
);

CREATE TABLE IF NOT EXISTS annual (
    code  VARCHAR NOT NULL,
    year  INTEGER NOT NULL,
    field VARCHAR NOT NULL,
    value DOUBLE,
    PRIMARY KEY (code, year, field)
);

CREATE TABLE IF NOT EXISTS audit_opinion (
    code    VARCHAR NOT NULL,
    year    INTEGER NOT NULL,
    opinion VARCHAR,
    PRIMARY KEY (code, year)
);

-- 巨潮公告索引。存元数据与 PDF 直链，不存正文：
-- 目的是让每个判定旁边都能翻到公司自己的原始披露，而不是让工具变成文档库。
CREATE TABLE IF NOT EXISTS disclosure (
    code         VARCHAR NOT NULL,
    anno_id      VARCHAR NOT NULL,
    title        VARCHAR,
    kind         VARCHAR,
    report_year  INTEGER,
    publish_ts   TIMESTAMP,
    ts_precision VARCHAR,
    pdf_url      VARCHAR,
    fetched_at   TIMESTAMP,
    PRIMARY KEY (code, anno_id)
);
"""


def connect(path=None, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    p = path or config.DB_PATH
    if read_only:
        if not p.exists():
            raise FileNotFoundError(
                f"数据库 {p} 不存在。先执行 `python -m fw.fetch <代码>` 或 "
                "`fw-fetch --queue` 取数——界面与命令行工具都只读，不联网。"
            )
        return duckdb.connect(str(p), read_only=True)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(p))
    conn.execute(_SCHEMA)
    return conn


def upsert(conn: duckdb.DuckDBPyConnection, st: Statements) -> int:
    """把一年年报拆成 (code, year, field, value) 长表写入。"""
    ann = st.annual
    # 按 dtype 筛，不靠列名黑名单：审计意见是字符串，日期是 datetime，
    # 都会混进 annual 帧，写进 DOUBLE 列会直接报错。
    numeric_cols = [c for c in ann.columns if pd.api.types.is_numeric_dtype(ann[c])]
    rows = []
    for year, series in ann.iterrows():
        for field in numeric_cols:
            v = series.get(field)
            if pd.isna(v):
                continue
            rows.append((st.code, int(year), field, float(v)))
    if rows:
        conn.executemany("INSERT OR REPLACE INTO annual VALUES (?, ?, ?, ?)", rows)

    if st.raw_opinion is not None:
        opinions = [
            (st.code, int(y), None if pd.isna(o) else str(o))
            for y, o in st.raw_opinion.items()
        ]
        if opinions:
            conn.executemany("INSERT OR REPLACE INTO audit_opinion VALUES (?, ?, ?)", opinions)

    conn.execute(
        "INSERT OR REPLACE INTO company VALUES (?, ?, now(), ?, ?)",
        [st.code, st.name, min(st.years()), max(st.years())],
    )
    return len(rows)


def load(conn: duckdb.DuckDBPyConnection, code: str) -> Statements:
    code = str(code).zfill(6)
    meta = conn.execute("SELECT name FROM company WHERE code = ?", [code]).fetchone()
    if meta is None:
        raise KeyError(f"{code} 未入库，先跑 `python -m fw.fetch {code}`")

    wide = conn.execute(
        "SELECT * FROM annual WHERE code = ? ORDER BY year", [code]
    ).fetch_df()
    ann = wide.pivot(index="year", columns="field", values="value") if not wide.empty else pd.DataFrame()

    ops = conn.execute(
        "SELECT year, opinion FROM audit_opinion WHERE code = ? ORDER BY year", [code]
    ).fetch_df()
    opinion = None
    if not ops.empty:
        opinion = pd.Series(ops["opinion"].values, index=ops["year"].astype(int).values, name="opinion")

    return Statements(code=code, annual=ann, name=meta[0] or code, raw_opinion=opinion)


def list_codes(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    return conn.execute(
        "SELECT code, name, first_year, last_year, fetched_at FROM company ORDER BY code"
    ).fetch_df()


def upsert_disclosures(conn: duckdb.DuckDBPyConnection, code: str,
                       result: dict[str, list[dict]]) -> int:
    rows = []
    for group in ("periodic", "risk"):
        for r in result.get(group, []):
            rows.append(
                (
                    code,
                    r["anno_id"],
                    r["title"],
                    r.get("kind", ""),
                    r.get("report_year"),
                    r["publish_ts"] or None,
                    r["ts_precision"],
                    r["pdf_url"],
                )
            )
    if rows:
        conn.executemany(
            "INSERT OR REPLACE INTO disclosure VALUES (?, ?, ?, ?, ?, ?, ?, ?, now())", rows
        )
    return len(rows)


def load_disclosures(conn: duckdb.DuckDBPyConnection, code: str) -> pd.DataFrame:
    return conn.execute(
        """SELECT title, kind, report_year, publish_ts, ts_precision, pdf_url
           FROM disclosure WHERE code = ? ORDER BY publish_ts DESC""",
        [str(code).zfill(6)],
    ).fetch_df()


def has_data(conn: duckdb.DuckDBPyConnection, code: str) -> bool:
    return conn.execute("SELECT 1 FROM company WHERE code = ?", [str(code).zfill(6)]).fetchone() is not None
