# M2.4 显式报表与 UNION ALL 实施计划

依据：用户已确认 `docs/superpowers/specs/2026-11-23-report-union-design.md`。
本机缺少 writing-plans 技能，直接记录可执行文件级计划，不下载额外依赖。

1. [x] **报表元数据**：新增 semantic/report.py；Catalog 增加可选 reports 注册表，校验指标/维度/月份/列与输出布局，保留既有明细配置。
2. [x] **月份与编译**：新增独立报告月份解析和编译模块，单月 SELECT、多月显式 UNION ALL，固定输出与外层排序；返回类型拓宽为 Query，不影响 plain 路径。
3. [x] **条件与权限复用**：小范围提取按物理列构造过滤的共用函数；每分支复用行策略和列白名单，任意分表表列权限错误均使整体失败。
4. [x] **测试与黄金集**：先运行定向测试，再补 SQLite 口径/结果/边界反例；手写 G-015 起单月、多月、不同权限和空行策略 SQL，G-001~G-014 不改。
5. [x] **最终回归**：全量 pytest、黄金集、用例纪律、Ruff、变更 Python 格式及 diff 检查；运行演示脚本，更新 README/CONTEXT/ARCHITECTURE/ROADMAP 和 ADR-0010。

不自动提交实现代码；不引入模型调用、外部数据库、任意表发现或报表重新聚合。


## 实施验收记录（2026-11-23）

- 全量 pytest：562 项通过；新增元数据/时间/编译用例 152 项，新增黄金接线覆盖；原负例按真实路由更新错误码。
- scripts.check_golden --cases eval/cases：18/18，17 类形状，评测覆盖 7/10；G-001~G-014 SQL 保持不变。
- scripts.validate_cases：1 个文件、10 条用例全部通过。
- Ruff check、所有变更 Python 格式检查、git diff --check 通过；demo_ask / trace_compile 正常。
- SQLite 独立验证标准口径、整月分区、三表 ALL、NULL/零值、表列行权限、缺配置/不可见整条拒绝。
- 更新 README/CONTEXT/ARCHITECTURE/ROADMAP 和 ADR-0010；M2 为 4/6，下一步 M2.5 整体分母占比。
- 代码尚未自动提交，没有外部模型或数据库调用；Oracle/PostgreSQL 仅 AST 渲染与回解析，不代表真实数据库验收。
