# M5.1 真实模型基线评测实施计划

日期：2026-11-26
依据：已确认的 `../specs/2026-11-26-real-model-baseline-design.md`。
基线：`5e7790e`，上一轮全量 1528 项测试、黄金 30/30。

分支：`codex/feat-real-model-baseline`。第 1 到第 5 节不调用真实模型，不进 CI 的只有第 6 节的人工步骤。
每节独立提交、独立验证；一节的测试不过不进下一节。

## 1. 客户端：关闭思考模式（T0）

- [x] 修改 `app/config.py`：`Settings` 增加 `llm_thinking`，默认空串；`from_env` 读 `WENSHU_LLM_THINKING`；
  新增解析函数，只接受空、`enabled`、`disabled`，其他值抛 `ValueError`。
- [x] 修改 `app/llm/real.py`：`OpenAICompatLLM` 增加 `thinking` 和 `transport` 两个构造参数。
  `thinking` 非空时请求体加 `thinking` 对象，为空时请求体不变。`transport` 只给测试注入，默认为空。
- [x] 修改 `app/llm/factory.py`：`build_llm` 和 `build_recorder` 把 `settings.llm_thinking` 传给客户端。
- [x] 修改 `.env.example`：模型名改为 `deepseek-flash`，增加 `WENSHU_LLM_THINKING` 并写明三个取值。
- [x] 新建 `tests/engine/test_llm_real.py`，用 `httpx.MockTransport` 截获请求体：
  不设时请求体的键与改动前完全一致；设 `disabled` 时带上该字段；`enabled` 同理；
  `Settings.from_env` 对空、合法、非法三种输入的行为；工厂在 real 模式下把设置传到客户端。

验证：`uv run pytest tests/engine/test_llm_real.py`、`uv run ruff check .`、`uv run ruff format --check .`。
提交：`feat(llm): 支持显式关闭思考模式`。

## 2. 抽出共享判定（T1）

- [x] 新建 `scripts/judging.py`：
  `Verdict`（`ok`、`stage`、`code`、`message`、附加信息）；
  `judge_extraction`，规则照搬 `scripts/evaluate.py` 现有逻辑，不改判法；
  `judge_execution`，按 Spec 的三类结果形状、两条行为题判定；
  四个结果匹配函数，数值容差 1e-6。
- [x] 修改 `scripts/evaluate.py`：抽取和执行两处改为调用共享判定，删掉原来的内联判断。
- [x] 新建 `tests/engine/test_judging.py`，全部用手工构造的 Intent 和返回对象，不过模型：
  抽取的成功、预期错误、错误码不符、`compile_error`、预期失败却成功；
  执行的标量、按行键、按列名、多行、AQ-009、AQ-010，以及数值差一个容差外的反例。
- [x] 判定器自检：对 AQ-001～005、008（可编译且 SQLite fixture 有对应表），用正确的 Intent 走 `compile_sql`
  和临时 SQLite，把结果交给判定器，必须全部判对。这一步同时验证容差。**任何一条判错就停下来说明情况，不修改预期数字。**
  AQ-006 的报表表未灌入 SQLite fixture、AQ-007 在语义层没有核销指标，二者改为用手工 QueryRows 覆盖
  按行键/按列名匹配函数；此范围收窄已于 2026-11-27 经人工确认，不扩语义层或 fixture。

验证：`uv run pytest tests/engine/test_judging.py tests/engine/test_evaluation_report.py`、
`uv run python -m scripts.evaluate` 仍为 71/71、`uv run pytest` 全量。
提交：`refactor(评测): 抽出共享判定逻辑`。

## 3. 记录型客户端与预算（T2）

- [ ] 新建 `scripts/call_recorder.py`：
  `CallRecord`；`RecordingLLM` 包住任意客户端，不重试、不改请求，记录消息摘要、原文、返回的模型版本、
  `finish_reason`、token 数、耗时和异常；`BudgetExhausted` 和 `RunAborted` 两个异常；
  调用数触顶、连续 5 次 `transport` 失败时抛出，成功一次后连续计数清零。
- [ ] 新建 `tests/engine/test_call_recorder.py`，包 `FakeLLM`：调用次数和记录内容；
  请求消息与不包时一致；`transport` 异常原样抛出且被记录；触顶后不再调用；连续计数清零。

验证：`uv run pytest tests/engine/test_call_recorder.py`、Ruff。
提交：`feat(评测): 记录型客户端与调用预算`。

## 4. 运行器与报告（T3）

- [ ] 新建 `scripts/evaluate_real.py`：
  前置检查；`run` 接受可注入的客户端工厂，供测试替换；逐题逐次执行；汇总；落盘。
  参数：`--repeats`（默认 3）、`--case`（可重复，冒烟用）、`--max-calls`（默认 200）。
- [ ] 执行套件用 `QueryService.start`，Scope 显式列出 fixture 的全部表，临时 SQLite 用 `seed_business` 灌数。
  有黄金对照的题另记 `golden_matches`，复用 `scripts/golden.py` 的 `compare`。
- [ ] 输出到 `eval/real/<run_id>/`：`report.json`、`report.md`、`calls.jsonl`、`review.md`。
  中止或出错时在 `finally` 里把已有结果写完。元数据按 Spec 所列，git commit 用 `git rev-parse HEAD` 取。
- [ ] 修改 `.gitignore`：加 `eval/real/`。
- [ ] 新建 `tests/engine/test_evaluate_real.py`，用一个按问题返回手写 cassette 文本的假客户端：
  模式为 `replay`、密钥为空、思考模式未关闭、目录已存在时返回 2，客户端工厂一次都没被调用；
  预算触顶返回 3，已有结果落盘；全对率、多数通过率、不稳定题数的计算；
  `transport` 失败不进分母；Markdown 含固定的结论边界声明；密钥不出现在任何输出文件；
  运行前后 `tests/cassettes` 目录内容不变；运行目录不出现在 `git status`。

验证：`uv run pytest tests/engine/test_evaluate_real.py`、`uv run pytest` 全量、
`uv run ruff check .`、`uv run ruff format --check .`。
提交：`feat(评测): 真实模型基线运行器`。

## 5. 文档与合并（T4）

- [ ] `docs/EVALUATION.md`：第 6 节写真实评测的运行方式；第 7 节归因阶段加 `transport`；
  写明运行目录不提交、`CONTEXT.md` 要记 run_id 和所在电脑。
- [ ] `docs/GLOSSARY.md`：基线、全对率、多数通过率、不稳定题、记录型客户端、思考模式。
- [ ] `docs/ROADMAP.md`：新增 M5 和 M5.1。
- [ ] `README.md`：快速开始加命令，注明需要密钥、不在 CI 里跑。
- [ ] `AGENTS.md`：常用命令加 `evaluate_real`，注明是人工步骤。
- [ ] `docs/CONTEXT.md`：写成「实现完成，尚未运行」。
- [ ] 推送分支，开 PR，等 Ruff、Pytest、Golden-set gate 三个 job 通过，自审后合并，拉回 `main`。

## 6. 人工步骤（需要密钥，不进 CI）

由你在自己的终端里运行，密钥不经过 AI。三行环境变量的写法见 Spec 的「预算与安全」。

- [ ] T5 冒烟：`uv run python -m scripts.evaluate_real --case EX-001 --repeats 1`。
  看四样：退出码是 0 或 1 而不是 2 或 3；`calls.jsonl` 里 `finish_reason` 是 `stop`；单次耗时；token 数。
  输出被截断、耗时不可接受或连续失败，就停下来查原因，不进 T6。
- [ ] T6 全量：`uv run python -m scripts.evaluate_real`。运行期间不改代码、不改题、不改预期。记下 run_id。
- [ ] T7 归因：逐条填 `review.md`，每条失败写阶段、原因和下一步动作。
  把 run_id、所在电脑、两套指标、不稳定题和归因结论写进 `docs/CONTEXT.md`。
  措辞遵守 `EVALUATION.md` 第 9 节：只说规范问法下、单模型单版本的结果。

AI 在 T6 之后读取 `report.md` 和 `calls.jsonl` 协助归因，这两个文件不含密钥。

## 验证命令汇总

```powershell
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run python -m scripts.check_golden
uv run python -m scripts.evaluate
```

## 本计划不做

不调 prompt，不改语义层、编译器、校验器；不新增或修改题目；不做多轮澄清评测；
不实现 `.env` 自动加载；不并发、不重试、不续跑；不把真实响应写进 `tests/cassettes`；不提交运行输出。

## 停下来的条件

- 第 2 节的判定器自检有用正确 Intent 判错的题。
- `scripts.evaluate` 的结果不再是 71/71。
- 发现需要改 prompt、语义层或题目才能继续。
- 冒烟显示思考模式参数没有生效，例如 `finish_reason` 是 `length` 或输出里混入思考内容。
