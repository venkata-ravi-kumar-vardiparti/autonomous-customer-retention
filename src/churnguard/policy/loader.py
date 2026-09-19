"""Load and SHA-256-hash a policy pack.

An unknown rule ID — a rule the pack declares that no rule module in
policy/rules/ knows how to evaluate, or vice versa — raises here, at load
time. It never surfaces as a silent no-op during evaluation.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

import yaml

PACKS_DIR = Path(__file__).resolve().parent / "packs"

# The registry of rule IDs the engine's code actually implements. Kept here
# (not imported from policy/rules/*) so loading a pack never has to import
# rule modules just to validate it.
KNOWN_RULE_IDS = frozenset({"RET-014", "RET-002", "BIL-003", "PLN-021", "FIN-009"})
KNOWN_PROHIBITED_IDS = frozenset({"PRO-007"})


class PolicyPackError(ValueError):
    """Raised for any structural or rule-registry problem in a policy pack."""


@dataclass(frozen=True)
class PolicyLimits:
    max_monthly_discount_tier1: float
    max_monthly_discount_tier2: float
    max_discount_pct: float
    max_bundle_duration_months: int


@dataclass(frozen=True)
class PolicyPack:
    version: str
    pack_hash: str
    limits: PolicyLimits
    raw: dict[str, Any]


def compute_pack_hash(data: dict[str, Any]) -> str:
    canonical = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validate_rule_registry(data: dict[str, Any]) -> None:
    declared_rules = set(data.get("rules", {}).keys())
    declared_prohibited = set(data.get("prohibited_actions", {}).keys())

    unknown_declared = (declared_rules - KNOWN_RULE_IDS) | (
        declared_prohibited - KNOWN_PROHIBITED_IDS
    )
    if unknown_declared:
        raise PolicyPackError(
            f"policy pack declares rule ID(s) with no implementation: "
            f"{sorted(unknown_declared)}"
        )

    unimplemented = (KNOWN_RULE_IDS - declared_rules) | (
        KNOWN_PROHIBITED_IDS - declared_prohibited
    )
    if unimplemented:
        raise PolicyPackError(
            f"policy pack is missing implemented rule ID(s): {sorted(unimplemented)}"
        )


def load_pack_from_dict(data: dict[str, Any]) -> PolicyPack:
    if "policy_pack_version" not in data:
        raise PolicyPackError("policy pack missing policy_pack_version")
    if "limits" not in data:
        raise PolicyPackError("policy pack missing limits")

    _validate_rule_registry(data)

    limits_raw = data["limits"]
    try:
        limits = PolicyLimits(
            max_monthly_discount_tier1=float(limits_raw["max_monthly_discount_tier1"]),
            max_monthly_discount_tier2=float(limits_raw["max_monthly_discount_tier2"]),
            max_discount_pct=float(limits_raw["max_discount_pct"]),
            max_bundle_duration_months=int(limits_raw["max_bundle_duration_months"]),
        )
    except KeyError as exc:
        raise PolicyPackError(f"policy pack limits missing key: {exc}") from exc

    return PolicyPack(
        version=str(data["policy_pack_version"]),
        pack_hash=compute_pack_hash(data),
        limits=limits,
        raw=data,
    )


def load_pack_from_yaml_text(text: str) -> PolicyPack:
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise PolicyPackError("policy pack YAML must parse to a mapping")
    return load_pack_from_dict(data)


def load_pack_from_path(path: Path) -> PolicyPack:
    return load_pack_from_yaml_text(path.read_text(encoding="utf-8"))


@cache
def get_pack(version: str) -> PolicyPack:
    """The one I/O boundary: reads packs/v<version>.yaml once per version, then caches.

    engine.evaluate() calls this so its own body stays I/O-free after the
    pack for a given version has been loaded once.
    """
    path = PACKS_DIR / f"v{version}.yaml"
    if not path.exists():
        raise PolicyPackError(f"no policy pack found for version {version!r} at {path}")
    return load_pack_from_path(path)


__all__ = [
    "KNOWN_PROHIBITED_IDS",
    "KNOWN_RULE_IDS",
    "PolicyLimits",
    "PolicyPack",
    "PolicyPackError",
    "compute_pack_hash",
    "get_pack",
    "load_pack_from_dict",
    "load_pack_from_path",
    "load_pack_from_yaml_text",
]
