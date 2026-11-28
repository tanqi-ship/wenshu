# tests/cassettes —— 固定模型响应契约

## 目录与真实性

- `intent_extraction/EX-001.json`～`EX-020.json`：手写假响应，**不是真实模型录制**。
- 独立手写问题/预期/难点/来源：`tests/fixtures/intent_extraction/cases.yaml`。
- `clarification_session/`：14份新手写响应（首轮/单槽补答/完整重述），独立会话预期见
  `tests/fixtures/clarification_session/cases.yaml`，覆盖10组会话；其中部分首轮直接引用原EX文件，不复制或刷新。
- slot-reply-v1与intent-extraction-v7消息协议分开；固定键不含owner/UUID/数据库路径，不依赖随机ID。
- `query_execution/AQ-001.json`：M3.4 手写假响应，固定键对应 `eval/cases/asset_quality.yaml` 中的原题；与本地业务 SQLite 一起验收结果，不替换 EX-001。
- provenance=handwritten_fixture，model=synthetic/handwritten-fixture，token 数为 0。
- 真实手动录制写入 `<WENSHU_LLM_CASSETTE_DIR>/recorded/intent_extraction/<case>.json`，
  同目录 `.provenance.json` 标记 recorded_real、协议版本与抽取成功/失败状态；不自动替换手写样本。

这些样本只验证抽取契约，不证明真实模型准确率、成本、延迟或业务可答性。
响应和 expected 分别手写，不能从被测代码生成/刷新标准答案。

## 格式

```json
{
  "version": 1,
  "model": "synthetic/handwritten-fixture",
  "entries": {
    "<16位hex>": {
      "text": "模型原始输出",
      "model": "synthetic/handwritten-fixture",
      "prompt_tokens": 0,
      "completion_tokens": 0
    }
  }
}
```

键是消息序列的 SHA256 前 16 位，不含 temperature/max_tokens/timeout，固定调用参数另有单测。
键只在建立样本时机械计算，测试运行只读，不能重造键掩盖 prompt 漂移。

## 离线回放

```bash
python -m scripts.demo_extract --case EX-001
python -m scripts.demo_extract --case EX-010  # 明确多指标错误，退出码 1
pytest tests/engine/test_extraction_replay.py
```

demo_extract 只构造 `ReplayLLM(inner=None, strict=True)`，即使环境设为 real 也不会联网。
未命中/解析失败为非零；不执行 SQL。旧 demo_ask 保持固定问题桩。

## 将来手动录制（本轮未执行）

```bash
WENSHU_LLM_MODE=real \
WENSHU_LLM_BASE_URL=<服务地址> \
WENSHU_LLM_MODEL=<模型名> \
WENSHU_LLM_API_KEY=<密钥> \
python -m scripts.record_cassette --case AQ-001
```

脚本已经接到同一 `extract_intent` 入口，不是完整 answer_question 流水线。
必须显式 real 并通过配置校验；原始坏响应也在 finally flush 保存供审核，但抽取失败返回非零，
不能把文件存在当成抽取/模型验收成功。不要将密钥放进样本或提交记录。

## 消息漂移

prompt/协议/业务目录改变可能导致键失配；未命中抛 ReplayMiss，绝不回落网络。
确认改变有意、审阅实际消息与独立预期后，再显式更新对应固定样本；
不能在测试时刷新键、把 strict 改为 false 或以自动录制掩盖漂移。

当前静态 system 1093 字符，业务目录 JSON 373 字符，20 条完整消息内容总长度范围
1541～1558 字符（EX-001：1550）；未达 ≤300 字精简目标，非 token 测量。

## M3.3跨请求演示

```bash
python -m scripts.demo_session start --case SS-001
python -m scripts.demo_session reply --case SS-001 --session-id <上一步ID> --expected-version 1
python -m scripts.demo_session show --session-id <上一步ID>
```

默认会话文件为build/clarification_sessions.db（忽略产物）。SS-001补答为固定“2026年4月末”，
应在新进程中恢复并保留渝北过滤；--db可显式指定其他本地路径。owner固定local-demo仅为演示，非认证方案。
--case选择问题/响应，不读取expected字段决定状态；start/reply只strict replay，show不调用模型。
澄清、预算停止、要求重启都是正常状态，退出0；版本冲突、回放未命中和技术错误非零。
这些文件不是生产样本、准确率评测或L5业务查询能力证明。
补答静态system717字符，9次固定单槽补答完整消息1398～1490字符，不是tokens。
