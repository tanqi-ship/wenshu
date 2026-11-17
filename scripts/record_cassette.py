"""录制模型响应到 cassette。

⚠️ **当前是骨架，Step 1 完成后才真正可用。**

原因：录制需要"跑一遍完整提问"，而完整提问流程（分诊 → 理解 → 检索 → 编译）
还没实现。本脚本先把接口和落盘逻辑定下来，避免到 Step 1 时才发现
cassette 的结构不好用。

用法（Step 1 之后）::

    WENSHU_LLM_MODE=real WENSHU_LLM_API_KEY=sk-... \\
      python -m scripts.record_cassette --case AQ-001
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.config import Settings
from app.llm.factory import build_recorder

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="录制模型响应")
    parser.add_argument("--case", required=True, help="用例 ID，例如 AQ-001")
    parser.add_argument("--cases-dir", default=str(DEFAULT_CASES_DIR))
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    if settings.llm_mode != "real":
        print(
            f"录制需要真实模型：当前 WENSHU_LLM_MODE={settings.llm_mode}。\n"
            "请设置 WENSHU_LLM_MODE=real 并配置 WENSHU_LLM_API_KEY。",
            file=sys.stderr,
        )
        return 2

    settings.validate()
    case = find_case(args.case, Path(args.cases_dir))
    print(f"用例：{case.get('id')} —— {case.get('question')}")

    recorder = build_recorder(settings, cassette=args.case)

    # ---- Step 1 之后在这里接上引擎入口 -------------------------------------
    # 期望形状：
    #   from app.engine.pipeline import answer_question
    #   await answer_question(case["question"], llm=recorder, settings=settings)
    #
    # 关键是必须把 recorder 一路传下去 —— 引擎不能自己去构造 LLM，
    # 否则录制会绕过 cassette，CI 里也就照样联网了。
    print(
        "\n引擎入口尚未实现（Step 1）。cassette 结构已就绪，"
        "落盘路径：\n  " + str(recorder._cassette.path) + "\n"
        "接上 app.engine 的入口函数后，本脚本即可工作。",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
