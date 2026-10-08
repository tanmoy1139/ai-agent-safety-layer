"""
Pytest fixtures for agent safety layer test suite.

All fixtures are session-scoped where safe to avoid repeated construction overhead.
"""

from __future__ import annotations

import pytest

from dharmaos.ethics_engine import Action, EthicsEngine


@pytest.fixture(autouse=True)
def reset_engine_singleton() -> None:
  """Reset singleton state before each test to avoid cross-test pollution."""
  EthicsEngine._deprecated_singleton = None  # P0-1: renamed from _instance
  EthicsEngine._instances = {}  # P0-1: per-tenant instances
  # Also reset CoreSelf singleton + module-level reference
  from dharmaos.consciousness import CoreSelf

  CoreSelf.reset()
  yield  # type: ignore[misc]
  EthicsEngine._deprecated_singleton = None
  EthicsEngine._instances = {}
  CoreSelf.reset()


@pytest.fixture()
def engine() -> EthicsEngine:
  """Return a fresh EthicsEngine instance."""
  return EthicsEngine()


@pytest.fixture()
def clean_action() -> Action:
  """A fully clean, low-risk browse action that passes all checks."""
  return Action(
  description="Browse the public Wikipedia page for Python programming",
  intent="browse",
  target="https://en.wikipedia.org/wiki/Python_(programming_language)",
  estimated_cost=0.0,
  reversible=True,
  affects_others=False,
  data_fields=["title", "summary"],
  budget_limit=10.0,
  agent_id="test-agent-001",
  estimated_compute_cost_usd=0.001,
  estimated_token_budget=500,
  task_worth_score=5.0,
  )
