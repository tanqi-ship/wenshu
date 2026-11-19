"""配置。

刻意用普通 dataclass + ``os.environ``，而不是全局单例 ——
测试时可以直接 ``Settings(llm_mode="fake")`` 构造，不需要摆弄环境变量。

用法::

    settings = Settings.from_env()

    # 测试里显式构造，覆盖任何环境
    settings = Settings(llm_mode="fake", embed_mode="fake")
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

LLMMode = Literal["fake", "replay", "real"]
EmbedMode = Literal["fake", "real"]


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"环境变量 {name} 必须是整数，当前为 {raw!r}") from exc


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"环境变量 {name} 必须是数字，当前为 {raw!r}") from exc


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass(slots=True)
class Settings:
    env: str = "local"

    # -- 数据库 ------------------------------------------------------------
    database_url: str = ""
    fixture_url: str = ""

    # -- 模型 --------------------------------------------------------------
    llm_mode: LLMMode = "fake"
    llm_cassette_dir: Path = Path("tests/cassettes")
    llm_strict_replay: bool = True

    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout_s: float = 120.0

    embed_mode: EmbedMode = "fake"
    embed_dim: int = 256
    embed_base_url: str = ""
    embed_api_key: str = ""
    embed_model: str = ""

    # -- 查询执行 ----------------------------------------------------------
    sql_timeout_s: int = 30
    row_limit: int = 1000
    explain_enabled: bool = True

    # -- 澄清预算 ----------------------------------------------------------
    #: 一次提问最多追问几轮。超过就按默认值作答并在答案里标注口径。
    max_clarify_rounds: int = 2

    extra: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            env=_env("WENSHU_ENV", "local"),
            database_url=_env("WENSHU_DATABASE_URL"),
            fixture_url=_env("WENSHU_FIXTURE_URL"),
            llm_mode=_llm_mode(_env("WENSHU_LLM_MODE", "fake")),
            llm_cassette_dir=Path(_env("WENSHU_LLM_CASSETTE_DIR", "tests/cassettes")),
            llm_strict_replay=_env_bool("WENSHU_LLM_STRICT_REPLAY", True),
            llm_base_url=_env("WENSHU_LLM_BASE_URL"),
            llm_api_key=_env("WENSHU_LLM_API_KEY"),
            llm_model=_env("WENSHU_LLM_MODEL"),
            llm_timeout_s=_env_float("WENSHU_LLM_TIMEOUT_S", 120.0),
            embed_mode=_embed_mode(_env("WENSHU_EMBED_MODE", "fake")),
            embed_dim=_env_int("WENSHU_EMBED_DIM", 256),
            embed_base_url=_env("WENSHU_EMBED_BASE_URL"),
            embed_api_key=_env("WENSHU_EMBED_API_KEY"),
            embed_model=_env("WENSHU_EMBED_MODEL"),
            sql_timeout_s=_env_int("WENSHU_SQL_TIMEOUT_S", 30),
            row_limit=_env_int("WENSHU_ROW_LIMIT", 1000),
            explain_enabled=_env_bool("WENSHU_EXPLAIN_ENABLED", True),
            max_clarify_rounds=_env_int("WENSHU_MAX_CLARIFY_ROUNDS", 2),
        )

    def validate(self) -> None:
        """启动前自检。宁可启动失败，也不要运行时才发现配错。"""
        if self.llm_mode == "real":
            missing = [
                name
                for name, value in (
                    ("WENSHU_LLM_BASE_URL", self.llm_base_url),
                    ("WENSHU_LLM_MODEL", self.llm_model),
                )
                if not value
            ]
            if missing:
                raise ValueError(f"llm_mode=real 需要配置：{', '.join(missing)}")

        if self.llm_mode == "replay" and not self.llm_cassette_dir.exists():
            raise ValueError(f"llm_mode=replay 但 cassette 目录不存在：{self.llm_cassette_dir}")

        if self.embed_mode == "real":
            missing = [
                name
                for name, value in (
                    ("WENSHU_EMBED_BASE_URL", self.embed_base_url),
                    ("WENSHU_EMBED_MODEL", self.embed_model),
                )
                if not value
            ]
            if missing:
                raise ValueError(f"embed_mode=real 需要配置：{', '.join(missing)}")

        if self.max_clarify_rounds < 0:
            raise ValueError("max_clarify_rounds 不能为负")


def _llm_mode(raw: str) -> LLMMode:
    value = (raw or "fake").lower()
    if value not in {"fake", "replay", "real"}:
        raise ValueError(f"WENSHU_LLM_MODE 必须是 fake / replay / real，当前为 {raw!r}")
    return value  # type: ignore[return-value]


def _embed_mode(raw: str) -> EmbedMode:
    value = (raw or "fake").lower()
    if value not in {"fake", "real"}:
        raise ValueError(f"WENSHU_EMBED_MODE 必须是 fake / real，当前为 {raw!r}")
    return value  # type: ignore[return-value]
