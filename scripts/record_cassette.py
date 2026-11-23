"""显式 real 模式下手动录制单轮意图抽取，不编译、不执行 SQL。

python -m scripts.record_cassette --case AQ-001
原始响应（含解析失败）由 finally flush 保存，另存来源/抽取状态。
真实录制放 recorded/intent_extraction，不自动覆盖手写回归样本。
仅用于将来的手动模型验收；实现该脚本不代表已调用真实模型。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

from app.config import Settings
from app.engine.semantic.bank_mini import semantic_catalog
from app.engine.understanding import ExtractionError, ExtractionResult, extract_intent
from app.engine.understanding.context import PROTOCOL_VERSION
from app.llm.factory import build_recorder
from app.llm.protocol import LLMError
from app.llm.replay import ReplayLLM, build_cassette_path

DEFAULT_CASES_DIR = Path("eval/cases")


def find_case(case_id: str, cases_dir: Path) -> dict:
    """在评测集里找一条用例。"""
    try:
        import yaml
    except ModuleNotFoundError as exc:
        raise SystemExit("缺少 PyYAML。请先 `uv sync` 或 `pip install PyYAML`。") from exc

    for path in sorted(cases_dir.glob("*.yaml")) + sorted(cases_dir.glob("*.yml")):
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        cases = payload if isinstance(payload, list) else (payload or {}).get("cases") or []
        for case in cases:
            if isinstance(case, dict) and str(case.get("id")) == case_id:
                return case
    raise SystemExit(f"在 {cases_dir} 里找不到用例 {case_id}")


async def record_question(question: str, recorder: ReplayLLM) -> ExtractionResult:
    try:
        return await extract_intent(question, semantic_catalog(), llm=recorder)
    finally:
        # complete 已留存 raw Completion，即使严格解析拒绝也保存供审查。
        recorder.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="显式 real 模式手动录制单轮意图抽取")
    parser.add_argument("--case", required=True, help="用例 ID，例如 AQ-001")
    parser.add_argument("--cases-dir", default=str(DEFAULT_CASES_DIR))
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", args.case):
        parser.error("用例 ID 只能包含字母、数字、下划线和连字符。")
    settings = Settings.from_env()
    if settings.llm_mode != "real":
        print(
            f"录制需要真实模型：当前 WENSHU_LLM_MODE={settings.llm_mode}。请显式配置 real 及密钥。",
            file=sys.stderr,
        )
        return 2
    settings.validate()
    case = find_case(args.case, Path(args.cases_dir))
    cassette_name = "recorded/intent_extraction/" + args.case
    target = build_cassette_path(settings.llm_cassette_dir, cassette_name)
    recorder = build_recorder(settings, cassette=cassette_name)
    ok = False
    error = ""
    try:
        result = asyncio.run(record_question(case["question"], recorder))
        ok = True
        print(f"抽取成功：{result.intent.metric!r}；原始响应已保存至 {target}，仍需人工审核。")
        return 0
    except (ExtractionError, LLMError) as exc:
        error = f"{type(exc).__name__}: {exc}"
        print(error, file=sys.stderr)
        return 1
    finally:
        target.parent.mkdir(parents=True, exist_ok=True)
        metadata = {
            "provenance": "recorded_real",
            "protocol_version": PROTOCOL_VERSION,
            "case_id": args.case,
            "question": case["question"],
            "extraction_ok": ok,
            "error": error,
        }
        target.with_suffix(".provenance.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    raise SystemExit(main())
