# M2.6 机构值精确解析实施计划

日期：2026-11-23
依据：用户已批准 `../specs/2026-11-23-org-exact-match-design.md`。
基线：`545b402`（设计），实现基线 `41f1dda`，752 项测试、22 个黄金用例。

本机未安装 writing-plans 技能，按已批准规格手工编写等价实施计划；不联网安装技能。
本轮不委派、不调用模型或外部数据库，不自动提交实现代码。

## 1. 语义层严格值解析

- 新建 `app/engine/semantic/exact_values.py`：独立异常、严格映射验证及 token 候选索引。
- 修改 `semantic/catalog.py`：build 验证 value_kind/已提供映射；code 对 high_cardinality 分流，不改 coded。
- 补 `tests/engine/test_exact_values.py`：全称/简称/编码、trim、同码别名、键撞编码、歧义、非法/空/缺映射、非字符串及绕过 build。
- 先运行新测试确认缺失，再实现并运行该测试。

## 2. 编译器与演示目录

- 在 `compiler/conditions.py` 统一接入中立异常到稳定 CompileError 的转换，严格维度仅 eq/in，空 IN/None 和旧分支错误不变。
- 在 `compiler/__init__.py` 拒绝普通/报表非空 Intent.org；share 保持既有错误。
- 在 `compiler/errors.py` 增加稳定码；更新 `semantic/bank_mini.py` 的严格标记与显式登记。
- 补 `test_org_compile.py` 覆盖所有既有形状、权限不重译、IN 整体失败、独立 SQLite 100/300/600 结果反例。
- 回归业务过滤、JOIN、报表、占比以及旧黄金用例，旧 SQL 不改。

## 3. 独立策略与来源解析

- `validation/policy.py` 末尾追加 org_columns；冻结副本、校验 schema.table/列标识符。
- 新建 `validation/org.py`；如作用域解析较长，拆为独立内部模块 `validation/org_sources.py`，不依赖编译器/语义目录。
- 物理身份以独立 Catalog 为准，按作用域处理别名/CTE/派生列/UNION 输出位置和单源星号。
- 区分普通、精确保护、已变换及无法确认来源；排名控制不传播，数值统计不当机构标识符。
- 未能确认的机构匹配保守拒绝，但不误伤无关表达式或展示。

## 4. R-ORG-001 与独立攻击测试

- 允许直接/反向 EQ 字符串、直接 IN 字符串集合、直接列 JOIN、正常显示聚合。
- 拒绝 LIKE/ILIKE/正则/任意函数变形、否定、范围、NULL、子查询/计算 IN、数值隐式匹配；检查所有条件位置。
- 补 `tests/engine/test_validation_org.py`，直接手写 SQL，不由编译器生成攻击 SQL。
- 覆盖嵌套同名别名、CTE/UNION、变形改名、未知来源、自定义 schema/字段、空策略及不受保护同名字段。
- 将规则接入 `validation/pipeline.py`，无 Intent 时也生效。

## 5. 黄金集与完整回归

- 从 G-023 起新增手写 SQL、意图、covers/why_hard；验证简称、混合 IN、JOIN 上级与 Scope/share。
- `python -m pytest`、`python -m scripts.validate_cases`、`python -m scripts.check_golden --cases eval/cases`。
- `python -m ruff check .`、变更 Python 文件 `ruff format --check`、`git diff --check`。
- 运行 demo_ask/trace_compile 的本地示例；若渲染其他方言只声称 AST 渲染/回解析。

## 6. 文档与交付

- 更新 README、CONTEXT、ARCHITECTURE、ROADMAP 和 DECISIONS 的 ADR-0012。
- 仅全部验收后将 M2 标为 6/6、独立业务规则标为 8 条，并记真实测试/黄金集数量。
- 下一阶段 M3，明确 L1/L5 仍未完成；汇报改动和验证结果，保留未提交实现供用户决定。

## 进度

- [x] 用户批准书面设计，完成实施计划。
- [x] 严格解析与编译接入。
- [x] 独立策略、来源追踪与 R-ORG-001。
- [x] 黄金集、全量回归及文档同步。

## 实际验收（2026-11-23）

- 991 项 pytest 全过（较基线新增 239：227 项专项 + 12 项黄金门禁参数化）。
- 26/26 黄金用例，25 种形状标签；10 条评测纪律通过，旧黄金 SQL 和 AQ-002 未改。
- Ruff 全库检查、变更 Python 格式、git diff --check 通过；demo_ask 与 trace_compile 可运行。
- 新黄金四例均在独立 SQLite 样本上验结果；PostgreSQL/Oracle 仅 AST 渲染、回解析及校验。
- 复核时补齐 LATERAL/表值函数保护、HAVING 别名不跨作用域、FILTER 控制与数值统计分离、
  递归锚点列别名恢复和根码值映射格式检查，均有回归测试。
- M2 已 6/6，规则 8 条；下一步 M3.1，L1/L5 未实现；未自动提交本次实现。
