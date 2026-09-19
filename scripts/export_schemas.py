"""Export JSON Schema for every contract model into schemas/.

Run via `make schemas`. Used by tests/contracts/test_schema_drift.py to
detect accidental contract changes — regenerate and review the diff
whenever a contracts/*.py change is intentional.
"""

from __future__ import annotations

import importlib
import inspect
import json
from pathlib import Path

from pydantic import BaseModel

CONTRACT_MODULES = [
    "churnguard.contracts.envelope",
    "churnguard.contracts.conversation",
    "churnguard.contracts.customer",
    "churnguard.contracts.competitor",
    "churnguard.contracts.offers",
    "churnguard.contracts.policy",
    "churnguard.contracts.recommendation",
    "churnguard.contracts.approval",
]

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "schemas"


def iter_contract_models() -> list[tuple[str, type[BaseModel]]]:
    models: list[tuple[str, type[BaseModel]]] = []
    for module_name in CONTRACT_MODULES:
        module = importlib.import_module(module_name)
        for name, obj in inspect.getmembers(module, inspect.isclass):
            if obj.__module__ != module_name:
                continue
            if not issubclass(obj, BaseModel):
                continue
            models.append((f"{module_name}.{name}", obj))
    return models


def export_schemas(output_dir: Path = OUTPUT_DIR) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for qualified_name, model in iter_contract_models():
        schema = model.model_json_schema()
        out_path = output_dir / f"{qualified_name}.json"
        out_path.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
        written.append(out_path)
    return written


if __name__ == "__main__":
    for path in export_schemas():
        print(f"wrote {path}")
