"""Process-wide configuration, read from environment variables.

Out of scope for Phase 0 beyond the settings later phases will need
immediately (DB path, default policy pack, default deadline). Extend this
as agents/tools/data layers land in later phases rather than reaching for
os.environ directly elsewhere in the codebase.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    openai_api_key: str | None
    database_path: str
    default_policy_pack_version: str
    default_deadline_ms: int


def load_settings() -> Settings:
    return Settings(
        openai_api_key=os.environ.get("OPENAI_API_KEY"),
        database_path=os.environ.get("CHURNGUARD_DB_PATH", "churnguard.db"),
        default_policy_pack_version=os.environ.get("CHURNGUARD_POLICY_PACK_VERSION", "0.0.0"),
        default_deadline_ms=int(os.environ.get("CHURNGUARD_DEADLINE_MS", "2000")),
    )
