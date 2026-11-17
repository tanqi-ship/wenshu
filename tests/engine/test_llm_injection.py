"""模型访问层的可注入性。

这组测试的存在意义：**证明引擎可以在零网络、零模型的情况下被完整测试。**
如果这组测试有一天开始需要网络，说明架构被破坏了。
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from app.llm import (
    FakeLLM,
    HashEmbedder,
    Message,
    ReplayLLM,
    ReplayMiss,
    ScriptExhausted,
    cosine_similarity,
    message_key,
)


class TestFakeLLM:
    async def test_sequential_script(self) -> None:
        llm = FakeLLM(["第一次", "第二次"])
        first = await llm.complete([Message("user", "问题一")])
        second = await llm.complete([Message("user", "问题二")])
        assert first.text == "第一次"
        assert second.text == "第二次"
        assert llm.call_count == 2
        llm.assert_exhausted()

    async def test_content_matched_script(self) -> None:
        llm = FakeLLM(
            [
                ("指标", '{"metric": "loan_balance"}'),
                ("澄清", '{"clarification_needed": true}'),
            ]
        )
        # 故意颠倒调用顺序：匹配优先于顺序
        reply = await llm.complete([Message("user", "请给出澄清")])
        assert "clarification" in reply.text
        reply = await llm.complete([Message("user", "抽取指标")])
        assert "loan_balance" in reply.text

    async def test_exhausted_script_raises(self) -> None:
        """脚本用尽必须抛错。

        一个悄悄返回空串的 fake 会让测试变成"什么都没验但显示通过"。
        """
        llm = FakeLLM(["只有一条"])
        await llm.complete([Message("user", "一")])
        with pytest.raises(ScriptExhausted):
            await llm.complete([Message("user", "二")])

    async def test_default_suppresses_exhaustion(self) -> None:
        llm = FakeLLM([], default="固定回答")
        assert (await llm.complete([Message("user", "一")])).text == "固定回答"
        assert (await llm.complete([Message("user", "二")])).text == "固定回答"

    async def test_calls_are_recorded(self) -> None:
        llm = FakeLLM(["x"])
        await llm.complete([Message("system", "系统"), Message("user", "用户提问")])
        assert llm.calls[0].last_user_content == "用户提问"
        assert "系统" in llm.calls[0].joined

    async def test_unconsumed_script_is_assertable(self) -> None:
        llm = FakeLLM(["没用到"])
        with pytest.raises(AssertionError, match="未被消费"):
            llm.assert_exhausted()


class TestHashEmbedder:
    def test_deterministic_across_instances(self) -> None:
        a = HashEmbedder(dim=64)
        b = HashEmbedder(dim=64)
        assert a.embed(["贷款余额"]) == b.embed(["贷款余额"])

    def test_same_text_same_vector(self) -> None:
        embedder = HashEmbedder(dim=64)
        vectors = embedder.embed(["不良率", "不良率"])
        assert vectors[0] == vectors[1]

    def test_different_text_different_vector(self) -> None:
        embedder = HashEmbedder(dim=64)
        left, right = embedder.embed(["贷款余额", "不良率"])
        assert left != right

    def test_vectors_are_normalized(self) -> None:
        embedder = HashEmbedder(dim=64)
        vector = embedder.embed(["贷款余额"])[0]
        norm = math.sqrt(sum(value * value for value in vector))
        assert norm == pytest.approx(1.0, abs=1e-9)

    def test_cosine_of_self_is_one(self) -> None:
        embedder = HashEmbedder(dim=32)
        vector = embedder.embed(["核销金额"])[0]
        assert cosine_similarity(vector, vector) == pytest.approx(1.0, abs=1e-9)

    def test_batch_length_matches(self) -> None:
        embedder = HashEmbedder(dim=16)
        assert len(embedder.embed(["a", "b", "c"])) == 3

    def test_dim_is_stable(self) -> None:
        assert HashEmbedder(dim=128).dim == 128


class TestReplayLLM:
    async def test_miss_raises_instead_of_calling_network(self, tmp_path: Path) -> None:
        """这是整个架构最不能妥协的一条：回放未命中绝不联网。"""
        llm = ReplayLLM(tmp_path / "empty.json")
        with pytest.raises(ReplayMiss, match="未命中"):
            await llm.complete([Message("user", "从未录过的问题")])

    async def test_record_then_replay_roundtrip(self, tmp_path: Path) -> None:
        cassette = tmp_path / "case.json"
        messages = [Message("system", "你是SQL专家"), Message("user", "贷款余额")]

        # 录制：inner 是 FakeLLM，代替真实模型
        recorder = ReplayLLM(cassette, inner=FakeLLM(["SELECT 1"]), strict=False)
        recorded = await recorder.complete(messages)
        recorder.flush()
        assert recorded.text == "SELECT 1"
        assert cassette.exists()

        # 回放：没有 inner，纯读文件
        replay = ReplayLLM(cassette)
        replayed = await replay.complete(messages)
        assert replayed.text == "SELECT 1"
        assert replay.hits == 1
        assert replay.misses == 0
        assert replayed.meta.get("replayed") is True

    async def test_strict_recording_refuses_to_write(self, tmp_path: Path) -> None:
        """strict=True 时即使有 inner 也不许自动录制。

        否则 CI 会悄悄掩盖"cassette 过期"这件事 —— 流水线绿的，
        但跑的其实是新录制的内容，回归门禁什么也没证明。
        """
        cassette = tmp_path / "strict.json"
        llm = ReplayLLM(cassette, inner=FakeLLM(["SELECT 1"]), strict=True)
        with pytest.raises(ReplayMiss):
            await llm.complete([Message("user", "x")])
        assert not cassette.exists()

    async def test_repeated_identical_messages_reuse_entry(self, tmp_path: Path) -> None:
        """相同消息命中同一条记录。

        这会把一个引擎 bug 变成可见信号：如果重试带着和上次**完全相同**的消息
        再问一遍，回放会返回同样的回答，于是重试永远不可能修好任何东西。
        引擎必须保证重试携带新信息（上一版 SQL + 错误原因）。
        """
        cassette = tmp_path / "repeat.json"
        messages = [Message("user", "同样的输入")]
        recorder = ReplayLLM(cassette, inner=FakeLLM(["回答A"]), strict=False)
        await recorder.complete(messages)
        recorder.flush()

        replay = ReplayLLM(cassette)
        first = await replay.complete(messages)
        second = await replay.complete(messages)
        assert first.text == second.text == "回答A"

    def test_message_key_is_stable(self) -> None:
        messages = [Message("user", "贷款余额")]
        assert message_key(messages) == message_key(messages)
        assert message_key(messages) != message_key([Message("user", "不良率")])

    def test_cassette_version_mismatch_is_detected(self, tmp_path: Path) -> None:
        path = tmp_path / "old.json"
        path.write_text('{"version": 999, "entries": {}}', encoding="utf-8")
        with pytest.raises(ReplayMiss, match="版本"):
            ReplayLLM(path)
