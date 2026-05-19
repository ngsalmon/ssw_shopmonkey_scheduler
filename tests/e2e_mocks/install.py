"""Install mock clients onto a running FastAPI app."""

from __future__ import annotations

import sys
from types import ModuleType

import structlog

from .mock_sheets import MockSheetsClient
from .mock_shopmonkey import MockShopmonkeyClient
from .state import STATE

logger = structlog.get_logger(__name__)


def install_mocks(main_module: ModuleType | None = None) -> None:
    """Replace the main module's client globals with mocks.

    Also loads the default fixture into the shared STATE so the app has a
    realistic world to serve from the moment it starts.
    """
    module = main_module or sys.modules.get("main")
    if module is None:  # pragma: no cover - main always loaded first
        raise RuntimeError("main module not found; cannot install mocks")

    STATE.load_default()
    module.shopmonkey_client = MockShopmonkeyClient(STATE)
    module.sheets_client = MockSheetsClient(STATE)
    logger.info(
        "e2e_mocks_installed",
        service_count=len(STATE.services),
        tech_count=len(STATE.techs),
    )
