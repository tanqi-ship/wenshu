"""M3.1 离线抽取契约演示；不读 real 环境、不执行 SQL。

python -m scripts.demo_extract --case EX-001
python -m scripts.demo_extract --case EX-010  # 预期抽取错误，退出码 1
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path

from app.engine.semantic.bank_mini import semantic_catalog
from app.engine.understanding import ExtractionError, extract_intent
from app.llm.protocol import LLMError
from app.llm.replay import ReplayLLM
from scripts.record_cassette import find_case

DEFAULT_CASES_DIR = Path("tests/fixtures/intent_extraction")


async def run_case(case: dict) -> int:
    llm = ReplayLLM(case["cassette"], inner=None, strict=True)
    try:
        result = await extract_intent(case["question"], semantic_catalog(), llm=llm)
    except (ExtractionError, LLMError) as exc:
        print(f"{case['id']}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "id": case["id"],
                "intent": asdict(result.intent),
                "model": result.completion.model,
                "fixture_tokens": result.completion.total_tokens,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="离线单轮意图抽取契约演示，不执行 SQL")
    parser.add_argument("--case", default="EX-001")
    parser.add_argument("--cases-dir", default=str(DEFAULT_CASES_DIR))
    args = parser.parse_args(argv)
    case = find_case(args.case, Path(args.cases_dir))
    print("离线手写假响应契约演示：不是模型准确率/成本/延迟评测，不代表可答。")
    return asyncio.run(run_case(case))


if __name__ == "__main__":
    raise SystemExit(main())
