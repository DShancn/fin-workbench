"""取数命令行。**这是唯一的写进程。**

用法：
    python -m fw.fetch 600519 000530          # 指定代码
    python -m fw.fetch --queue                 # 自选池 + vest 待办队
    python -m fw.fetch --queue --force         # 已入库的也重取
    python -m fw.fetch --status                # 看库里有什么

一家公司约 2 分钟、60+ 个子请求。Streamlit 界面只读，不联网，所以这个命令
要么手动跑，要么由 compose 里的 fetcher 服务定时跑。
"""

from __future__ import annotations

import argparse

from . import config, queue as queue_mod, sources, statements, store

config.apply_proxy_bypass()


def _expected_year(now=None) -> int:
    """上一年度年报在本年内迟早会有；当年年报要到次年 4 月底。"""
    from datetime import datetime

    y = (now or datetime.now(config.CN_TZ)).year
    return y - 1


def fetch_one(conn, code: str, force: bool = False) -> str:
    code = str(code).zfill(6)
    if not force and store.has_data(conn, code):
        row = conn.execute("SELECT last_year FROM company WHERE code=?", [code]).fetchone()
        if row and row[0] >= _expected_year():
            return f"跳过 {code}（已有 {row[0]} 年报）"

    try:
        tables = sources.fetch_all(code)
    except Exception as exc:
        return f"失败 {code}: {type(exc).__name__}: {exc}"

    st = statements.build(code, tables)
    n = store.upsert(conn, st)
    return f"入库 {code} {st.name}: {len(st.years())} 个年度 / {n} 个数值"


def _staged(main_db):
    """把库复制到暂存名再改，最后原子替换。

    DuckDB 的只读连接会持共享文件锁，写连接要求独占——若直接在原文件上写，
    界面开着的时候取数会失败。复制-改-替换把冲突窗口压到一次 rename。
    """
    import shutil

    staging = main_db.with_name(f".{main_db.name}.staging")
    if main_db.exists():
        shutil.copy2(main_db, staging)
    elif staging.exists():
        staging.unlink()
    return staging


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="fw.fetch", description="财报取数（唯一写进程）")
    p.add_argument("codes", nargs="*", help="6 位代码，可多个")
    p.add_argument("--queue", action="store_true", help="取自选池 + vest 待办队列")
    p.add_argument("--force", action="store_true", help="已入库的也重取")
    p.add_argument("--status", action="store_true", help="列出库内公司已取年度")
    p.add_argument("--inplace", action="store_true", help="直接改原库（确认没有界面在读时用）")
    args = p.parse_args(argv)

    if args.status:
        # 查询命令不该碰文件。走只读连接，不做复制-替换。
        if not config.DB_PATH.exists():
            print("库还没建，先跑 fw.fetch <代码>")
            return
        conn = store.connect(config.DB_PATH, read_only=True)
        try:
            df = store.list_codes(conn)
        finally:
            conn.close()
        print(df.to_string(index=False) if not df.empty else "库是空的，先跑 fw.fetch <代码>")
        return

    if args.inplace:
        conn = store.connect()
        _run(conn, args, p)
        return

    target = config.DB_PATH
    staging = _staged(target)
    conn = store.connect(staging)
    try:
        _run(conn, args, p)
    finally:
        conn.close()
    if target.exists():
        target.unlink()
    staging.rename(target)
    print(f"已原子替换 {target}")


def _run(conn, args, p) -> None:
    codes = list(args.codes)
    if args.queue:
        q = queue_mod.merged_queue()
        codes.extend(q["code"].tolist())
        print(f"队列共 {len(q)} 只：{', '.join(q['code'].tolist())}")

    if not codes:
        p.error("没给代码。用 `fw.fetch 600519` 或 `fw.fetch --queue`")

    seen = set()
    todo = [c for c in codes if not (c in seen or seen.add(c))]
    for i, code in enumerate(todo, 1):
        print(f"[{i}/{len(todo)}] {fetch_one(conn, code, args.force)}", flush=True)


if __name__ == "__main__":
    main()
