"""Schema-drift test: exported JSON Schema must match the committed snapshots in schemas/.

Regenerate via `make schemas` and review the diff whenever a contracts/*.py
change is intentional; a failure here with no contracts/*.py change is a
sign the committed snapshots are stale, not that the code is wrong.
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts.export_schemas import export_schemas

REPO_ROOT = Path(__file__).resolve().parents[2]
COMMITTED_SCHEMAS_DIR = REPO_ROOT / "schemas"


def test_committed_schemas_exist() -> None:
    committed = sorted(p.name for p in COMMITTED_SCHEMAS_DIR.glob("*.json"))
    assert committed, (
        f"no committed schemas found in {COMMITTED_SCHEMAS_DIR}; run `make schemas` and commit "
        "the output"
    )


def test_no_schema_drift(tmp_path: Path) -> None:
    export_schemas(output_dir=tmp_path)

    fresh = {p.name: json.loads(p.read_text()) for p in tmp_path.glob("*.json")}
    committed = {
        p.name: json.loads(p.read_text()) for p in COMMITTED_SCHEMAS_DIR.glob("*.json")
    }

    missing_in_committed = sorted(fresh.keys() - committed.keys())
    stale_in_committed = sorted(committed.keys() - fresh.keys())
    assert not missing_in_committed, (
        f"new contract schemas not committed, run `make schemas`: {missing_in_committed}"
    )
    assert not stale_in_committed, (
        f"committed schemas for removed contracts, run `make schemas`: {stale_in_committed}"
    )

    drifted = [name for name in fresh if fresh[name] != committed[name]]
    assert not drifted, f"schema drift detected, run `make schemas` and review: {drifted}"
