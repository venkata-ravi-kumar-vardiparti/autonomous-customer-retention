"""Seeds one shared test database for the whole test session.

Moved here (from tests/unit/conftest.py) in Phase 4 so tests/golden (and
any future top-level test package) gets the same autouse fixture without
duplicating it - a root conftest.py applies to every subdirectory.

DB_LATENCY_MS is forced to 0 here so the suite runs fast; the default-value
behaviour (40ms) is verified explicitly in test_db_access.py by monkeypatching
the env var back out.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator

import pytest

from churnguard.data.seed.generate import SEED, seed_database


@pytest.fixture(scope="session", autouse=True)
def seeded_db(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    db_path = str(tmp_path_factory.mktemp("churnguard_data") / "churnguard_test.db")
    os.environ["CHURNGUARD_DB_PATH"] = db_path
    os.environ["DB_LATENCY_MS"] = "0"
    asyncio.run(seed_database(db_path, seed=SEED))
    yield db_path
