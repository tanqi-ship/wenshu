"""槽位澄清：确定性判断"信息是否足够"。"""

from __future__ import annotations

from app.engine.clarify import ClarifyState, build_answer_note, check_slots


class TestSlotCompleteness:
    def test_empty_intent_asks_metric_first(self) -> None:
        check = check_slots({})
        assert not check.complete
        # 一次只问一个，且先问指标
        assert check.next_question is not None
        assert "指标" in check.next_question

    def test_metric_present_but_time_missing_asks_time(self) -> None:
        check = check_slots({"metric": "贷款余额"})
        assert not check.complete
        assert [spec.name for spec in check.missing] == ["time_range"]
        assert check.next_question is not None
        assert "时间" in check.next_question

    def test_both_required_present_is_complete(self) -> None:
        check = check_slots({"metric": "贷款余额", "time_range": "2026年4月末"})
        assert check.complete
        assert check.next_question is None

    def test_time_present_but_metric_missing(self) -> None:
        check = check_slots({"time_range": "2026年4月末"})
        assert not check.complete
        assert [spec.name for spec in check.missing] == ["metric"]


class TestDefaults:
    def test_org_defaults_to_whole_bank_without_asking(self) -> None:
        """没提机构绝大多数情况就是"全行"。追问它会让追问率翻倍。"""
        check = check_slots({"metric": "贷款余额", "time_range": "2026年4月末"})
        assert check.complete
        assert check.defaulted["org"] == "全行"
        assert "按全行口径计算" in check.disclosures

    def test_dimensions_default_to_empty(self) -> None:
        check = check_slots({"metric": "贷款余额", "time_range": "2026年4月末"})
        assert check.defaulted["dimensions"] == ()
        assert "未做分组" in " ".join(check.disclosures)

    def test_explicit_org_is_not_defaulted(self) -> None:
        check = check_slots(
            {"metric": "贷款余额", "time_range": "2026年4月末", "org": "渝北支行"}
        )
        assert "org" not in check.defaulted
        assert check.filled["org"] == "渝北支行"


class TestValuePresence:
    def test_zero_is_a_present_value(self) -> None:
        """不良率 0% 是合法结果，不能被当成"缺失"。"""
        check = check_slots({"metric": "不良率", "time_range": "2026年4月末", "org": 0})
        assert check.complete
        assert check.filled["org"] == 0

    def test_whitespace_string_is_missing(self) -> None:
        check = check_slots({"metric": "   ", "time_range": "2026年4月末"})
        assert not check.complete

    def test_empty_list_is_missing(self) -> None:
        check = check_slots({"metric": "贷款余额", "time_range": []})
        assert not check.complete


class TestQuestionQuality:
    def test_question_includes_examples(self) -> None:
        check = check_slots({})
        assert check.next_question is not None
        assert "例如" in check.next_question

    def test_only_one_question_at_a_time(self) -> None:
        check = check_slots({})
        assert len(check.missing) == 2  # 指标 + 时间都缺
        # 但只问第一个
        question = check.next_question or ""
        assert "指标" in question
        assert "时间" not in question


class TestClarifyBudget:
    def test_budget_blocks_further_asking(self) -> None:
        """预算用尽后不再追问 —— 这是防追问泛滥的唯一手段。"""
        state = ClarifyState(rounds_used=0, max_rounds=2)
        check = check_slots({})
        assert state.should_ask(check) is True

        state.record_ask("metric")
        state.record_ask("time_range")
        assert state.exhausted is True
        assert state.should_ask(check) is False

    def test_zero_budget_never_asks(self) -> None:
        state = ClarifyState(rounds_used=0, max_rounds=0)
        assert state.should_ask(check_slots({})) is False

    def test_complete_check_never_asks(self) -> None:
        state = ClarifyState(rounds_used=0, max_rounds=2)
        check = check_slots({"metric": "贷款余额", "time_range": "2026年4月末"})
        assert state.should_ask(check) is False

    def test_retried_slot_stops_being_asked(self) -> None:
        """同一个槽位问了两遍用户还没答清楚，就别再问了。"""
        state = ClarifyState(rounds_used=0, max_rounds=10)
        state.record_ask("metric")
        state.record_ask("metric")
        check = check_slots({})
        assert "metric" in state.retried
        # 只剩 time_range 还没问过，所以还会问（问的是时间）
        assert state.should_ask(check) is True


class TestAnswerNote:
    def test_note_lists_defaults(self) -> None:
        check = check_slots({"metric": "贷款余额", "time_range": "2026年4月末"})
        note = build_answer_note(check)
        assert "全行" in note

    def test_note_discloses_unconfirmed_when_budget_exhausted(self) -> None:
        state = ClarifyState(rounds_used=2, max_rounds=2)
        check = check_slots({"metric": "贷款余额"})
        note = build_answer_note(check, state)
        assert "未确认" in note

    def test_no_note_when_everything_filled_explicitly(self) -> None:
        check = check_slots(
            {
                "metric": "贷款余额",
                "time_range": "2026年4月末",
                "org": "全行",
                "dimensions": ["产品"],
                "comparison": "yoy",
            }
        )
        assert build_answer_note(check) == ""
