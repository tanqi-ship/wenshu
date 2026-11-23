# M2.3 受限双表 JOIN 实施计划

依据：用户已确认 `docs/superpowers/specs/2026-11-23-bounded-two-table-join-design.md`。
本机未安装 writing-plans 技能，直接记录可执行的文件级计划，不安装额外依赖。

- [x] **语义与来源解析**：LevelDef 增加 table，RelationDef 增加 cardinality；新增 sources 模块，收集有效依赖、拒绝三表、解析唯一关系和安全方向、生成确定性别名。
- [x] **构造与权限接线**：使用解析出的表别名构造维度/过滤/JOIN；绑定主表公式，拒绝不支持的双表形状；行级策略按表注入，新增空 Scope 和列白名单检查。
- [x] **定向测试**：独立编译单测覆盖 AST、缺失/歧义关系、不安全基数、公式与别名、权限；SQLite 内存验证 INNER/LEFT、金额、缺失维度和权限下的 NULL 分组。
- [x] **黄金集**：演示目录添加 dim_org 与上级机构维度；手写 G-011 起的 INNER、LEFT、过滤与权限 SQL，登记 covers/why_hard；G-001~G-010 不变。
- [x] **文档与完整验收**：修正 AQ-007 错误关联，新增 ADR 与架构说明；运行全量 pytest、黄金集、Ruff、格式和 diff 检查，按实际结果同步进度。

每一步先运行定向测试，再扩展；不自动提交实现代码，不改变 AQ-007 单表查询语义。

## 验收记录（2026-11-23）

- pytest：398 项通过（含 JOIN 定向测试 92 项），无外部数据库/模型/网络。
- 黄金集：14/14（13 类形状）；旧 G-001~G-010 无需修改。
- 用例纪律：scripts.validate_cases 通过，AQ-007 保持原样。
- Ruff 检查与全部变更 Python 文件格式检查通过；git diff --check 通过。
- scripts.demo_ask、scripts.trace_compile 冒烟通过。
- 具体实现增加 sources/permissions；黄金集可选 relations 对照 L0 关系，不让 Intent 写 SQL。
- 数据唯一性仍由约束或独立数据检查保证，不冒称编译期已检查真实数据。
