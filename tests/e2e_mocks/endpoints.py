"""Test-only FastAPI router for manipulating mock state.

Mounted on the app only when E2E_MODE=1 is set at process start. Any request
to /test/state returns 404 in production.
"""

from __future__ import annotations

import copy
import sys
from typing import Any

from fastapi import APIRouter, Body

from .state import STATE

router = APIRouter(prefix="/test", tags=["e2e-only"])

# Snapshot of main.config taken on first call; lets /state/reset restore it.
_original_config_snapshot: dict[str, Any] | None = None


def _snapshot_original_config() -> None:
    global _original_config_snapshot
    if _original_config_snapshot is not None:
        return
    main_module = sys.modules.get("main")
    if main_module is None:
        return
    _original_config_snapshot = copy.deepcopy(getattr(main_module, "config", {}))


def _apply_config_override(overrides: dict[str, Any]) -> None:
    """Shallow-merge overrides into main.config so business behavior shifts."""
    _snapshot_original_config()
    main_module = sys.modules.get("main")
    if main_module is None:
        return
    config = getattr(main_module, "config", None)
    if config is None:
        return
    for key, value in overrides.items():
        config[key] = copy.deepcopy(value)


def _restore_original_config() -> None:
    if _original_config_snapshot is None:
        return
    main_module = sys.modules.get("main")
    if main_module is None:
        return
    main_module.config = copy.deepcopy(_original_config_snapshot)


@router.post("/state")
async def set_state(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    """Apply a partial scenario to the in-memory mock state.

    Recognized payload keys (all optional):
      reset, load_default, services, techs, appointments, errors (see state.py)
      config: dict of overrides for main.config (e.g. disabled_departments)
    """
    if "config" in payload:
        _apply_config_override(payload["config"])
    STATE.apply_scenario(payload)
    return {"ok": True, "state": STATE.snapshot()}


@router.get("/state")
async def get_state() -> dict[str, Any]:
    """Return a snapshot of the current mock state for assertions."""
    main_module = sys.modules.get("main")
    snap = STATE.snapshot()
    snap["config"] = copy.deepcopy(getattr(main_module, "config", {})) if main_module else {}
    return snap


@router.post("/state/reset")
async def reset_state() -> dict[str, Any]:
    """Reset state and config to defaults."""
    STATE.load_default()
    _restore_original_config()
    return {"ok": True}
