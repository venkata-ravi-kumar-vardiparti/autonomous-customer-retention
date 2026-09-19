"""Round-trip test: model -> json -> model must be byte-identical.

Covers every contract in tests/contracts/factories.py, including the two
generic wrappers (AgentEnvelope[ConversationInput], AgentResult[ConversationSignals]).
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from tests.contracts.factories import ALL_EXAMPLES


@pytest.mark.parametrize("name", list(ALL_EXAMPLES.keys()))
def test_roundtrip_is_byte_identical(name: str) -> None:
    instance = ALL_EXAMPLES[name]
    assert isinstance(instance, BaseModel)

    first_json = instance.model_dump_json()
    rehydrated = type(instance).model_validate_json(first_json)
    second_json = rehydrated.model_dump_json()

    assert first_json == second_json
    assert rehydrated == instance
