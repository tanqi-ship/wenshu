"""把 fixtures/sql/bank_fixture.sql 灌进目标业务库。

用法::

    # 只检查 SQL 文件能不能读、有多少语句（不需要数据库）
    python -m scripts.seed_fixture --print-sql

    # 真正灌库（需要 psycopg）
    python -m scripts.seed_fixture --url postgresql://bankfixture:bankfixture@localhost:5434/bankfixture

CI 里由 ci.yml 的 "Seed the bank fixture database" 步骤调用。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

DEFAULT_FIXTURE_SQL = Path(__file__).resolve().parent.parent / "fixtures" / "sql" / "bank_fixture.sql"


def load_sql(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"fixture SQL 不存在：{path}")
    return path.read_text(encoding="utf-8")


def seed(url: str, sql: str) -> None:
    """执行 fixture SQL。

    刻意不引入 ORM —— 灌 fixture 是纯 DDL/DML，直接用驱动最省事，
    也避免"fixture 灌不进去是因为 ORM 模型和 DDL 不一致"这种无关干扰。
    """
    try:
        import psycopg
    except ModuleNotFoundError as exc:  # pragma: no cover - 取决于本地环境
        raise SystemExit(
            "缺少 psycopg。请先 `uv sync` 或 `pip install 'psycopg[binary]'`。"
        ) from exc

    with psycopg.connect(url, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="灌入假银行 fixture 库")
    parser.add_argument("--url", default=os.environ.get("WENSHU_FIXTURE_URL", ""))
    parser.add_argument("--file", default=str(DEFAULT_FIXTURE_SQL))
    parser.add_argument(
        "--print-sql",
        action="store_true",
        help="只打印统计信息，不连数据库",
    )
    args = parser.parse_args(argv)

    sql = load_sql(Path(args.file))
    if args.print_sql:
        statements = [chunk for chunk in sql.split(";") if chunk.strip()]
        print(f"文件：{args.file}")
        print(f"字符数：{len(sql)}")
        print(f"语句数（按分号粗分）：{len(statements)}")
        tables = [
            line.strip()
            for line in sql.splitlines()
            if line.strip().upper().startswith("CREATE TABLE")
        ]
        print(f"CREATE TABLE：{len(tables)} 条")
        for statement in tables:
            print(f"  - {statement}")
        return 0

    if not args.url:
        print(
            "未提供 --url，且环境变量 WENSHU_FIXTURE_URL 为空。"
            "用 --print-sql 只检查 SQL 文件，或提供连接串。",
            file=sys.stderr,
        )
        return 2

    seed(args.url, sql)
    print(f"已灌入 fixture：{args.file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
