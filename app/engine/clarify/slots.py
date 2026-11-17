"""槽位澄清：确定性判断"信息是否足够"。

这一层存在的理由是：**模型不知道自己不知道。**

LLM 在信息不全时的默认行为是"补一个合理值然后继续"，而不是说"我不确定"。
在银行场景里，一个看起来对的错数字比一句"请补充时间范围"危害大得多 ——
因为它会被拿去开会、报监管。

所以判据不能是"模型觉得够了"，必须是**槽位清单 + 确定性的齐全性检查**。
模型只负责抽取槽位；"够不够"由代码判定。

三个设计决定：

1. **默认值优先，追问兜底。** 有合理默认的槽位（机构=全行）不追问，
   而是带默认作答并在答案里标注口径。
2. **一次只问一个问题。** 同时问三个问题的产品没人用。
3. **有预算上限。** ``settings.max_clarify_rounds``（默认 2）。超过预算就
   按默认作答 + 标注不确定项，不再追问。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

#: 槽位缺失时的处理方式。
OnMissing = Literal["ask", "use_default", "ignore"]


@dataclass(frozen=True, slots=True)
class SlotSpec:
    name: str
    #: 缺失时的行为。``required=True`` 的槽位通常用 ``ask``。
    on_missing: OnMissing = "use_default"
    default: Any = None
    #: 追问文本。一次只说一件事，所以文本要能独立成立。
    question: str = ""
    #: 追问时给的取值范围示例，帮助用户一次答对。
    examples: tuple[str, ...] = ()
    #: 用了默认值时，答案里要附带的说明。
    disclosure: str = ""
    #: 该槽位是否必需。仅用于生成可读的说明，行为由 ``on_missing`` 决定。
    required: bool = False


#: 意图槽位清单。**这是"够不够"的唯一判据。**
#:
#: 注意 ``org`` 和 ``dimensions`` 用的是 ``use_default``：银行问数里
#: "没提机构"绝大多数情况就是"全行"，追问一次会让追问率翻倍。
INTENT_SLOTS: tuple[SlotSpec, ...] = (
    SlotSpec(
        name="metric",
        required=True,
        on_missing="ask",
        question="您想查询哪个指标？",
        examples=("贷款余额", "不良率", "核销金额"),
    ),
    SlotSpec(
        name="time_range",
        required=True,
        on_missing="ask",
        question="请补充时间范围。",
        examples=("2026年4月末", "过去12个月", "截至2026年3月底"),
    ),
    SlotSpec(
        name="org",
        on_missing="use_default",
        default="全行",
        disclosure="按全行口径计算",
    ),
    SlotSpec(
        name="dimensions",
        on_missing="use_default",
        default=(),
        disclosure="未做分组，返回汇总值",
    ),
    SlotSpec(
        name="comparison",
        on_missing="use_default",
        default=None,
        disclosure="未做同比/环比",
    ),
)

DEFAULT_SLOTS: dict[str, SlotSpec] = {spec.name: spec for spec in INTENT_SLOTS}


def _is_present(value: Any) -> bool:
    """槽位是否"有值"。

    刻意把 ``0`` 和 ``False`` 当作有值 —— 否则"不良率 0%"这种合法结果会被
    误判成缺失。
    """
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) > 0
    return True


@dataclass(slots=True)
class SlotCheck:
    """槽位检查结果。"""

    #: 缺失且需要追问的槽位（按声明顺序）。
    missing: list[SlotSpec] = field(default_factory=list)
    #: 用了默认值的槽位 -> 默认值。
    defaulted: dict[str, Any] = field(default_factory=dict)
    #: 填上的槽位 -> 值。
    filled: dict[str, Any] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        """所有必需信息是否齐全 —— 这就是"能不能生成 SQL"的判据。"""
        return not self.missing

    @property
    def next_question(self) -> str | None:
        """下一次要问的问题。一次只问一个。"""
        if not self.missing:
            return None
        spec = self.missing[0]
        text = spec.question
        if spec.examples:
            text = f"{text}例如：{'、'.join(spec.examples)}。"
        return text

    @property
    def disclosures(self) -> list[str]:
        """答案里要标注的口径说明。"""
        notes: list[str] = []
        for name in self.defaulted:
            spec = DEFAULT_SLOTS.get(name)
            if spec is not None and spec.disclosure:
                notes.append(spec.disclosure)
        return notes

    def summary(self) -> str:
        parts = []
        if self.filled:
            parts.append(f"已明确：{', '.join(sorted(self.filled))}")
        if self.defaulted:
            parts.append(f"用默认值：{', '.join(sorted(self.defaulted))}")
        if self.missing:
            parts.append(f"待确认：{', '.join(spec.name for spec in self.missing)}")
        return "；".join(parts) if parts else "无槽位信息"


def check_slots(
    intent: Mapping[str, Any],
    *,
    slots: tuple[SlotSpec, ...] = INTENT_SLOTS,
) -> SlotCheck:
    """检查意图槽位是否齐全。

    *intent* 是模型输出的扁平映射（``metric`` / ``time_range`` / ``org`` / ...）。

    这个函数是**纯确定性**的：不看模型置信度、不做语义推断。
    正因为如此，它可以被单测覆盖，也可以直接进 CI。
    """
    check = SlotCheck()

    for spec in slots:
        value = intent.get(spec.name)

        if _is_present(value):
            check.filled[spec.name] = value
            continue

        if spec.on_missing == "ignore":
            continue

        if spec.on_missing == "ask":
            check.missing.append(spec)
            continue

        # use_default
        if spec.default is not None:
            check.defaulted[spec.name] = spec.default
        else:
            check.filled[spec.name] = spec.default

    return check


@dataclass(slots=True)
class ClarifyState:
    """跨轮次的澄清状态。

    追踪"已经问过几轮"是防止追问泛滥的唯一手段。没有它，"一直问到够了为止"
    会变成用户体感上"这个系统老在问我问题"。
    """

    rounds_used: int = 0
    max_rounds: int = 2
    #: 已经问过并已解决的槽位，避免重复问同一个问题。
    resolved: set[str] = field(default_factory=set)
    #: 已经问过但用户没答清楚的槽位。
    retried: set[str] = field(default_factory=set)

    @property
    def exhausted(self) -> bool:
        return self.rounds_used >= self.max_rounds

    def record_ask(self, slot_name: str) -> None:
        self.rounds_used += 1
        if slot_name in self.resolved or slot_name in self.retried:
            self.retried.add(slot_name)
        self.resolved.add(slot_name)

    def should_ask(self, check: SlotCheck) -> bool:
        """是否还应该追问。

        预算用尽后不再问 —— 此时应该按默认值作答并标注不确定项，
        或者直接拒答。**绝不能无限追问。**
        """
        if not check.missing:
            return False
        if self.exhausted:
            return False
        # 所有缺失槽位都已问过两遍以上，再问也没意义
        unnamed = [spec for spec in check.missing if spec.name not in self.retried]
        return bool(unnamed)


def build_answer_note(check: SlotCheck, state: ClarifyState | None = None) -> str:
    """生成答案里的口径说明。

    追问预算耗尽时，要额外说明"哪些信息是按默认值处理的" ——
    这比悄悄用一个默认值好得多：用户能看见假设，才能判断数字能不能用。
    """
    notes = check.disclosures
    if state is not None and state.exhausted and check.missing:
        pending = "、".join(spec.name for spec in check.missing)
        notes.append(f"以下信息未确认，按最可能口径处理：{pending}")
    return "；".join(notes)
