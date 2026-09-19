"""Reflective test: every BaseModel defined under contracts/ must forbid extra fields.

This is the enforcement mechanism for the non-negotiable rule in CLAUDE.md.
A model missing extra="forbid" would let the Agents SDK's strict JSON schema
silently swallow hallucinated fields.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil

import pytest
from pydantic import BaseModel, ValidationError

import churnguard.contracts as contracts_pkg
from churnguard.contracts.envelope import Telemetry


def _iter_contract_models() -> list[tuple[str, type[BaseModel]]]:
    models: list[tuple[str, type[BaseModel]]] = []
    for module_info in pkgutil.iter_modules(contracts_pkg.__path__, prefix="churnguard.contracts."):
        module = importlib.import_module(module_info.name)
        for name, obj in inspect.getmembers(module, inspect.isclass):
            if obj.__module__ != module_info.name:
                continue
            if not issubclass(obj, BaseModel):
                continue
            models.append((f"{module_info.name}.{name}", obj))
    return models


def test_at_least_one_model_found() -> None:
    assert len(_iter_contract_models()) >= 20


def test_every_contract_model_forbids_extra() -> None:
    offenders = [
        qualified_name
        for qualified_name, model in _iter_contract_models()
        if model.model_config.get("extra") != "forbid"
    ]
    assert offenders == [], f"models missing extra='forbid': {offenders}"


def test_unknown_field_is_actually_rejected_at_runtime() -> None:
    with pytest.raises(ValidationError):
        Telemetry(
            model="gpt-4.1-mini",
            latency_ms=1,
            tokens_in=1,
            tokens_out=1,
            cost_usd=0.0,
            cache_hit=False,
            hallucinated_field="should not be accepted",  # type: ignore[call-arg]
        )
