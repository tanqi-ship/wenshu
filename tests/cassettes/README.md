# tests/cassettes —— 录制的模型响应

## 格式

一个 case 一个文件：`<case_id>.json`

```json
{
  "version": 1,
  "model": "deepseek-chat",
  "entries": {
    "<16位hex>": {
      "text": "模型原始输出",
      "model": "deepseek-chat",
      "prompt_tokens": 412,
      "completion_tokens": 96
    }
  }
}
```

键是消息序列的 sha256 前 16 位，**不含 temperature / max_tokens**。

## 录制

> ⚠️ 录制脚本是骨架，**Step 1（编译器接入）完成后才真正可用**。
> 现在跑它会明确告知这一点并返回非零退出码，不会假装成功。

```bash
WENSHU_LLM_MODE=real \
WENSHU_LLM_API_KEY=sk-... \
python -m scripts.record_cassette --case AQ-001
```

## 回放（CI 默认）

```bash
WENSHU_LLM_MODE=replay WENSHU_LLM_STRICT_REPLAY=1 pytest
```

**未命中一律抛 `ReplayMiss`，绝不联网。**

## 什么时候要重新录制

prompt 改了、模型换了、或者引擎改了导致消息内容变化 —— 都会让键失配。

出现 `ReplayMiss` 时不要"改成 strict=0 让它自己录"，
那样子 CI 会悄悄掩盖 cassette 过期，回归门禁什么也没证明。
正确做法：**确认 prompt 变更是有意的**，然后重新录制并 review cassette 的 diff。

review cassette 时重点看：模型这次说的话，是不是你希望它说的话。
这是 prompt 变更最直接的验收材料。
