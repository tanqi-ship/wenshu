# wenshu —— 银行级智能问数引擎

从零设计的 NL2SQL 引擎骨架，目标是**银行口径可控、可解释、可回归**。

> **当前状态（第一步）**：后端骨架 + 校验层 + 澄清层 + CI + fixture + 评测集，101 个测试通过。
> **没有前端**，也**还没有搬入任何 askdb 代码**。详见 `MIGRATION.md`。

设计立场（对照参考项目 askdb）：

| | askdb | wenshu |
|---|---|---|
| 谁决定用哪些表 | 数据源全部表塞进 prompt，让模型选 | 检索出本次用到的 3~8 张表，AST 强制"只能用检索到的" |
| 谁写 SQL | 模型直接写，配 5,500 字 prompt 约束 | 模型只产 `Intent`，编译器展开成 SQL（零模型） |
| 业务规则放哪 | 5,500 字 prompt、51 条"必须禁止"、**零条被验证** | `validation/rules.py`，每条有稳定 code 和单测 |
| 信息够不够 | 散在 5 个节点的硬编码门禁 | 声明式槽位清单 + 追问预算 |
| 怎么证明没变差 | 无 | 双引擎对照门禁：**旧对→新错 = 红线** |

核心信条来自 NVIDIA auto-ontology 的实测结论：

> **写在 prompt 里的 SQL 规则块，每次实测都让准确率下降。**

所以方向是：**能搬进代码的规则，一条都不留在 prompt 里。**

---

## 目录结构

```
wenshu/
├── app/                          # 后端（当前唯一存在的代码）
│   ├── engine/                   # 新引擎（严格 lint，严格测试）
│   │   ├── semantic/             # 语义层领域模型：Intent / 指标 / 维度 / 表关系
│   │   ├── retrieval/            # 检索出表范围 + JOIN 路径（待实现，见 Step 2）
│   │   ├── compiler/             # Intent -> AST -> 方言 SQL（待实现，见 Step 1）
│   │   ├── validation/           # 只读 / 名字 / 业务规则（已实现）
│   │   └── clarify/              # 槽位检查 + 追问预算（已实现）
│   ├── llm/                      # 模型访问边界：Protocol + fake/replay/real
│   ├── legacy/                   # 待搬入的 askdb 周边代码（Step 0，当前空）
│   └── config.py
│
├── web/                          # ⬜ 前端 —— 尚未创建，Step 0 搬入或重写
│
├── eval/
│   ├── cases/*.yaml              # 评测集（每条必须有 why_hard + covers）
│   └── baseline_report.json      # 旧引擎基线（Step 0 产出）
├── fixtures/sql/bank_fixture.sql # 故意的"脏"测试库（D-01 ~ D-11）
├── scripts/                      # seed / 回归门禁 / 用例校验 / 录制
├── tests/                        # engine 单测（纯内存）+ cassettes + legacy
└── docs/DECISIONS.md             # 架构决策记录（LangGraph、embedding 等）
```

### 前端在哪？

**现在没有。** 这是有意的，不是遗漏：

1. 本次交付是**垂直切片**：先把"一个指标从提问到出数"这条最短路径跑通，
   前端在这一步不产生信息。
2. 前端要覆盖的是会话列表、结果表、分页、导出、图表、指标卡、SQL 预览、
   追问交互、场景下拉——这些**依赖引擎的输出形态定型**。引擎还在变的时候写前端，
   改起来比写起来贵。
3. 计划放在同仓库的 `web/` 下（monorepo，两个可部署单元）。
   理由：接口和界面一起改的 PR 能原子提交；CI 一次跑两边的 lint；
   新同事一次 clone 就能跑全栈。部署仍然是分开的（后端镜像 + 前端静态资源）。

技术栈待定，见 `docs/DECISIONS.md` 末尾的"待决事项"。

---

## 快速开始

```bash
uv sync --group dev          # 或 pip install -e . && pip install pytest pytest-asyncio ruff PyYAML

cp .env.example .env
docker compose up -d pgvector bankdb

uv run python -m scripts.seed_fixture     # 灌假银行库
uv run pytest                             # 101 passed，不需要数据库/模型/网络
uv run ruff check app/engine app/llm scripts tests
```

---

## 三条必须从第一天遵守的纪律

### 1. CI 里绝不调用真实模型

`app/llm/` 的三个实现由 `WENSHU_LLM_MODE` 选择：

| 模式 | 实现 | 用在哪 |
|---|---|---|
| `fake` | `FakeLLM` 按脚本返回 | 单元测试 |
| `replay` | `ReplayLLM` 回放 cassette | **CI / 回归门禁** |
| `real` | `OpenAICompatLLM` | 本地 / 生产 |

回放**找不到记录时抛 `ReplayMiss`，绝不联网**。这条是硬的：
否则 CI 会在某天悄悄开始调真实模型，慢、花钱、结果不可复现，而流水线还是绿的。

`HashEmbedder` 让检索管道能在零网络下跑通——它语义质量为零，
只用于证明管道可跑，**不用于评测召回率**。

### 2. 规则进代码，不进 prompt

见 `app/engine/validation/rules.py`：

```
R-NAME-001  名称字段禁止出现在 GROUP BY        (C-11)
R-PCT-001   百分比指标禁止 * 100               (C-07)
R-COMP-001  多时点对比禁止用子查询查另一时点    (C-05)
R-TOPN-001  每月 Top N 必须用窗口函数          (C-12)
R-TBL-001   ETL 分支最多 2 张表                (C-04)
R-JOIN-001  报表/UNION 分支禁止 JOIN           (C-06)
```

每条都有：稳定 problem code（评测按 code 聚合定位退化）、业务规则编号
（业务方复核）、单测覆盖（规则本身不会写错）。

### 3. 重试必须携带原 SQL

`ValidationResult.feedback()` 只生成问题清单，**原 SQL 必须由重试构造器一起回灌**。

askdb 的重试只回传错误信息、没回传原 SQL，而 prompt 里还写着
"优先在原 SQL 结构上做局部修复"——模型看不到原 SQL，
所谓"修复"实际退化成重新抽签。这是它重试经常到上限仍失败的原因。

---

## 评测集就是需求清单

```yaml
- id: AQ-006
  why_hard: |
    fixture 里有两张报表都叫 npl_rate：一张按五级分类（标准口径），
    一张按逾期 90 天以上（另一口径）。LP03 前者 0.1407、后者 0.1283。
    命中错的那张，SQL 完全合法、数字看着也合理，但口径是错的。
  covers: [D-04, D-11, C-06]
```

* `why_hard` —— **写不出"为什么容易错"，说明这条用例测不出东西**
* `covers` —— 引用 fixture 缺陷编号（`D-xx`）和规则编号（`C-xx`）

`python -m scripts.validate_cases eval/cases` 强制检查这两项。

## 双引擎回归门禁

| 判定 | 后果 |
|---|---|
| 旧对 → 新错 | **失败（红线）** |
| 缺用例 | **失败** |
| 准确率下降 > 2pp | **失败** |
| LLM 调用数 +10% / 耗时 +50% | 警告 |
| 旧错 → 新对 | 记录为 improvement |

---

## 路线图

- [x] 工程骨架 + CI（分圈 lint / pgvector / skip guard）
- [x] 模型访问边界（Protocol + fake / replay / real）
- [x] 校验层：只读、名字、范围、6 条业务规则
- [x] 澄清层：槽位清单 + 追问预算
- [x] 假银行 fixture（D-01 ~ D-11）+ 评测集 10 条
- [x] 双引擎回归门禁
- [ ] **Step 0**：搬入 askdb 周边代码（含 `web/`），跑出 `baseline_report.json`
- [ ] **Step 1**：替换 SQL 生成（语义层检索 + 编译器）
- [ ] **Step 2**：替换检索范围（retrieve + `TABLE_NOT_IN_SCOPE`）+ 混合召回/重排
- [ ] **Step 3**：替换编排（24 节点 DAG → 显式状态机）
- [ ] **Step 4**：替换分诊 + 统一澄清

细节见 `MIGRATION.md`；已定的架构决策见 `docs/DECISIONS.md`。
