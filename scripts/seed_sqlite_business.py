"""创建本地业务测试库，不覆盖现有文件。"""

import argparse
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

DEFAULT_DB = Path("build/business_fixture.db")
FIXTURE_SQL = Path(__file__).resolve().parents[1] / "fixtures/sql/bank_sqlite_minimal.sql"


def seed_business(path: Path) -> None:
    sql = FIXTURE_SQL.read_text(encoding="utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb"):
        pass
    try:
        with closing(sqlite3.connect(path)) as db:
            db.executescript(sql)
    except (sqlite3.Error, OSError):
        path.unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args(argv)
    try:
        seed_business(args.db)
    except FileExistsError:
        print("文件已存在，未覆盖；请指定新的 --db。", file=sys.stderr)
        return 1
    except (OSError, sqlite3.Error):
        print("无法创建本地业务测试库。", file=sys.stderr)
        return 1
    print(f"已创建本地业务测试库：{args.db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
