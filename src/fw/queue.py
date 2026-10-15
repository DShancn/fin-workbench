"""取数范围：自选池 + vest 归因管线的待深挖队列。

两个项目之间的接缝不是共享数据库，而是这条队：
vest 研究卡里的 followup_queue 每一项是 {code, name, question, trigger}，
它本身就是「这只标的值得做财报深挖」的结构化表达，直接作为本工具的输入。
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from . import config


def load_watchlist(path: Path | None = None) -> pd.DataFrame:
    p = path or config.WATCHLIST_FILE
    if not p.exists():
        return pd.DataFrame(columns=["code", "name", "source"])
    df = pd.read_csv(p, dtype=str).fillna("")
    if "code" not in df.columns:
        raise ValueError(f"{p} 缺 code 列。最少格式：code,name")
    df["code"] = df["code"].str.strip().str.zfill(6)
    if "source" not in df.columns:
        df["source"] = "watchlist"
    return df[["code", "name", "source"]]


def load_vest_queue(vest_db: Path | None = None) -> pd.DataFrame:
    """读 vest 的 research_card.followup_queue。

    只读——不写别人的库。文件不存在就返回空表，这不是错误，是两个项目
    各自独立演进的正常状态。
    """
    p = vest_db or config.VEST_DB
    if not p or not Path(str(p)).exists():
        return pd.DataFrame(columns=["code", "name", "source", "question"])

    import duckdb

    conn = duckdb.connect(str(p), read_only=True)
    try:
        rows = conn.execute(
            "SELECT theme, anomaly_date, followup_queue FROM research_card "
            "WHERE followup_queue IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()

    out = []
    for theme, day, raw in rows:
        try:
            items = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            continue
        for it in items or []:
            if isinstance(it, dict) and it.get("code"):
                out.append(
                    {
                        "code": str(it["code"]).zfill(6),
                        "name": it.get("name", ""),
                        "source": f"vest:{theme}@{day}",
                        "question": it.get("question", ""),
                    }
                )
    return pd.DataFrame(out, columns=["code", "name", "source", "question"])


def merged_queue(vest_db: Path | None = None) -> pd.DataFrame:
    wl = load_watchlist()
    wl["question"] = ""
    vq = load_vest_queue(vest_db)
    if vq.empty:
        return wl[["code", "name", "source", "question"]]

    both = pd.concat([wl, vq], ignore_index=True)
    # 同一只股票可能既在自选池又被归因队提出，保留两个来源，问题文本合并展示
    agg = (
        both.groupby("code", as_index=False)
        .agg(
            name=("name", lambda s: next((x for x in s if x), "")),
            source=("source", lambda s: " + ".join(sorted(set(x for x in s if x)))),
            question=("question", lambda s: " | ".join(x for x in s if x)),
        )
    )
    return agg
