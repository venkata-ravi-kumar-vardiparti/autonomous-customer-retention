"""Prompt-injection and safety guardrails.

Module map:
    injection.py    deterministic transcript classifier (allow /
                     allow_with_quarantine / block) + the matching
                     @input_guardrail for the Conversation agent.
    corpus/          adversarial test fixtures for injection.py.
"""
