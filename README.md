# wenshu —— 银行级智能问数引擎

从零设计的 NL2SQL 引擎骨架，目标是**银行口径可控、可解释、可回归**。

初始骨架包含经作者许可从 [askdb] 搬入的周边代码（见 `ATTRIBUTION.md`），
但**引擎核心全部重写**。搬什么、重写什么，见 `MIGRATION.md`。

---

## 一句话说清设计立场

| | askdb（参考项目） | wenshu |
|---|---|---|
| 谁决定用哪些表 | 把数据源全部表塞进 prompt，让模型选 | 检索出本次用到的 3~8 张表，AST 强制"只能用检索到的" |
| 谁写 SQL | 模型直接写，配 5,500 字 prompt 约束 | 模型只产 `Intent`，编译器展开成 SQL（零模型） |
| 业务规则放哪 | 5,500 字 prompt，51 条"必须/禁止"，**零条被验证** | `validation/rules.py`，每条规则都有稳定 code 和单测 |
| 信息够不够 | 散在 5 个节点的硬编码门禁 | 声明式槽位清单 + 追问预算 |
| 怎么证明没变差 | 无 | 双引擎对照门禁：**旧对→新错 = 红线** |

核心信条来自 NVIDIA auto-ontology 的实测结论（`react/prompts.py` 开头）：

> **写在 prompt 里的 SQL 规则块，每次实测都让准确率下降。**

所以本项目的方向是：**能搬进代码的规则，一条都不留在 prompt 里。**

---

## 目录结构

```
app/
├── engine/                 # 新引擎（严格 lint，严格测试）
│   ├── semantic/           # 语义层领域模型：Intent / 指标 / 维度 / 表关系
│   ├── retrieval/          # 检索出本次表范围 + JOIN 路径 + 覆盖率（待实现）
│   ├── compiler/           # Intent -> AST -> 方言 SQL（待实现）
│   ├── validation/         # 只读 / 名字 / 业务规则（已实现，101 个测试）
│   └── clarify/            # 槽位检查 + 追问预算（已实现）
├── llm/                    # 模型访问边界：Protocol + fake/replay/real
├── legacy/                 # 从 askdb 搬入、待逐步替换的代码（宽松 lint）
└── config.py

eval/
├── cases/*.yaml            # 评测集（每条必须有 why_hard + covers）
└── baseline_report.json    # 旧引擎基线（Step 0 产出）

fixtures/sql/bank_fixture.sql   # 故意的"脏"测试库（D-01 ~ D-11）
scripts/                        # seed / 回归门禁 / 用例校验
tests/engine/                   # 纯内存单测，秒级
```

---

## 快速开始

```bash
# 1. 依赖
uv sync --group dev          # 或 pip install -e . && pip install pytest pytest-asyncio ruff PyYAML

# 2. 本地环境（应用库 + 假银行库）
cp .env.example .env
docker compose up -d pgvector bankdb

# 3. 灌 fixture
uv run python -m scripts.seed_fixture

# 4. 跑测试（不需要数据库、不需要模型、不需要网络）
uv run pytest

# 5. lint
uv run ruff check app/engine app/llm scripts tests
uv run ruff format --check app/engine app/llm scripts tests
```

---

## 三条必须在第一天就遵守的纪律

### 1. CI 里绝不调用真实模型

`app/llm/` 的三个实现由 `WENSHU_LLM_MODE` 选择：

| 模式 | 实现 | 用在哪 |
|---|---|---|
| `fake` | `FakeLLM` 按脚本返回 | 单元测试 |
| `replay` | `ReplayLLM` 回放 cassette | **CI / 回归门禁** |
| `real` | `OpenAICompatLLM` | 本地 / 生产 |

回放**找不到记录时抛 `ReplayMiss`，绝不联网**。这一条是硬的：
否则 CI 会在某天悄悄开始调真实模型，慢、花钱、结果不可复现，而流水线还是绿的。

同理，`HashEmbedder` 让检索管道可以在零网络下跑通
（它语义质量为零，只用于证明管道可跑，不用于评测召回率）。

### 2. 规则进代码，不进 prompt

见 `app/engine/validation/rules.py`。每条规则都有：

* 稳定 problem code（`NAME_FIELD_IN_GROUP_BY` 之类）→ 评测按 code 聚合，直接定位退化
* 业务规则编号（`C-05` / `C-11` …）→ 业务方能对照复核
* 单测覆盖 → 规则本身不会写错

已实现：`R-NAME-001` `R-PCT-001` `R-COMP-001` `R-TOPN-001` `R-TBL-001` `R-JOIN-001`

### 3. 重试必须携带原 SQL

`ValidationResult.feedback()` 只生成"问题清单"，**原 SQL 必须由重试构造器一起回灌**。

为什么单独强调：askdb 的重试只回传了错误信息、没回传原 SQL，
而 prompt 里还写着"优先在原 SQL 结构上做局部修复"——
模型看不到原 SQL，所谓"修复"实际退化成重新抽签。这是它重试经常到上限仍失败的原因。

---

## 评测集就是需求清单

`eval/cases/*.yaml` 每条用例有两个关键字段：

```yaml
- id: AQ-006
  why_hard: |
    fixture 里有两张报表都叫 npl_rate：一张按五级分类（标准口径），
    一张按逾期 90 天以上（另一口径）。LP03 前者 0.1407、后者 0.1283。
    命中错的那张，SQL 完全合法、数字看着也合理，但口径是错的。
  covers: [D-04, D-11, C-06]
```

* `why_hard` —— **写不出"为什么容易错"，说明这条用例测不出东西**
* `covers` —— 引用 fixture 缺陷编号（`D-xx`）和业务规则编号（`C-xx`），
  CI 挂了能直接说"是 D-04 混口径那类退化了"

`python -m scripts.validate_cases eval/cases` 会强制检查这两项。

---

## 双引擎回归门禁

| 判定 | 后果 |
|---|---|
| 旧对 → 新错 | **失败（红线）** |
| 缺用例 | **失败** |
| 准确率下降 > 2pp | **失败** |
| LLM 调用数 +10% | 警告 |
| 平均耗时 +50% | 警告 |
| 旧错 → 新对 | 记录为 improvement |

```bash
python -m scripts.check_regression \
    --baseline eval/baseline_report.json \
    --current  eval/current_report.json
```

---

## 当前进度

- [x] 工程骨架 + CI（分圈 lint / pgvector service / skip guard）
- [x] 模型访问边界（Protocol + fake / replay / real）
- [x] 校验层：只读、名字、范围、6 条业务规则（101 个测试）
- [x] 澄清层：槽位清单 + 追问预算
- [x] 假银行 fixture（D-01 ~ D-11）
- [x] 评测集 10 条 + 结构校验脚本
- [x] 双引擎回归门禁
- [ ] Step 0：搬入 askdb 周边代码，跑出 `baseline_report.json`
- [ ] Step 1：替换 SQL 生成（语义层检索 + 编译器）
- [ ] Step 2：替换检索范围（retrieve + `tables_not_in_scope`）
- [ ] Step 3：替换编排
- [ ] Step 4：替换分诊

下一步做 Step 0。见 `MIGRATION.md`。

---

[askdb]: 内部参考项目，非开源
