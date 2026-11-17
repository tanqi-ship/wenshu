"""测试共用常量。

单独成文件而不是放 conftest：测试文件直接 import conftest 会让它被加载两次
（一次作为 pytest 的 ``conftest``，一次作为 ``tests.conftest``），
虽然能跑，但容易在 fixture 解析上踩到奇怪的坑。
"""

from __future__ import annotations

#: 校验器默认按哪个方言解析。生产是 Oracle，本地 fixture 用 Postgres。
DIALECT = "postgres"

#: Oracle fixture 用的方言（profile: oracle 时跑）。
ORACLE_DIALECT = "oracle"
