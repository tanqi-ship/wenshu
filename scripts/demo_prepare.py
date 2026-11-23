"""M3.2 严格离线单轮准备演示；澄清正常返回0，技术错误返回1。

python -m scripts.demo_prepare --case EX-008  # 缺时间
python -m scripts.demo_prepare --case EX-010  # 请求指定一个指标
python -m scripts.demo_prepare --case EX-001  # 槽位齐全，不代表可答
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path

from app.engine.clarify import prepare_question
from app.engine.semantic.bank_mini import semantic_catalog
from app.engine.understanding import ExtractionError
from app.llm.protocol import LLMError
from app.llm.replay import ReplayLLM
from scripts.record_cassette import find_case

DEFAULT_CASES_DIR = Path("tests/fixtures/clarification")
DEFAULT_EXTRACTION_CASES_DIR = Path("tests/fixtures/intent_extraction")


async def run_case(case: dict, *, case_id: str) -> int:
    try:
        llm = ReplayLLM(case["cassette"], inner=None, strict=True)
        result = await prepare_question(case["question"], semantic_catalog(), llm=llm)
    except (ExtractionError, LLMError) as exc:
        print(f"{case_id}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    # 仅用于展示，asdict 不参与槽位判断；原文/片段是JSON数据，不执行。
    print(json.dumps({"id": case_id, **asdict(result)}, ensure_ascii=False, indent=2, default=str))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="离线单轮澄清接线演示，不编译、不执行SQL")
    parser.add_argument("--case", default="EX-001")
    parser.add_argument("--cases-dir", default=str(DEFAULT_CASES_DIR))
    parser.add_argument("--extraction-cases-dir", default=str(DEFAULT_EXTRACTION_CASES_DIR))
    args = parser.parse_args(argv)
    reference = find_case(args.case, Path(args.cases_dir))
    # 只使用样本引用读取真实问题/响应；不读取expected字段决定结果。
    case = find_case(reference["extraction_case"], Path(args.extraction_cases_dir))
    print("离线手写假响应准备层演示：slots_complete仅表示必需槽位齐全，不代表可答或授权；不编译、不执行SQL。")
    return asyncio.run(run_case(case, case_id=args.case))


if __name__ == "__main__":
    raise SystemExit(main())
