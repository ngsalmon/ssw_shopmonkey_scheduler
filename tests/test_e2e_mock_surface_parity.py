"""Surface-parity test: the mock clients must implement every method that
main.py reaches on the real clients.

If main.py grows a new client call, this test fails until the corresponding
mock method is added. That keeps the e2e suite honest as the app evolves.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from sheets_client import SheetsClient
from shopmonkey_client import ShopmonkeyClient
from tests.e2e_mocks.mock_sheets import MockSheetsClient
from tests.e2e_mocks.mock_shopmonkey import MockShopmonkeyClient

REPO_ROOT = Path(__file__).resolve().parent.parent
MAIN_PY = REPO_ROOT / "main.py"


def _collect_attribute_accesses(source_path: Path, target_names: set[str]) -> set[str]:
    """Find every `name.attr` access where name is in target_names."""
    tree = ast.parse(source_path.read_text())
    found: set[str] = set()

    class Visitor(ast.NodeVisitor):
        def visit_Attribute(self, node: ast.Attribute) -> None:
            if (
                isinstance(node.value, ast.Name)
                and node.value.id in target_names
            ):
                found.add(node.attr)
            self.generic_visit(node)

    Visitor().visit(tree)
    return found


@pytest.fixture(scope="module")
def shopmonkey_calls_in_main() -> set[str]:
    return _collect_attribute_accesses(MAIN_PY, {"shopmonkey_client"})


@pytest.fixture(scope="module")
def sheets_calls_in_main() -> set[str]:
    return _collect_attribute_accesses(MAIN_PY, {"sheets_client"})


def test_mock_shopmonkey_implements_all_used_methods(shopmonkey_calls_in_main):
    """Every attr main.py reads on shopmonkey_client must exist on the mock."""
    missing = set()
    for attr in shopmonkey_calls_in_main:
        # Skip private and the lifecycle hooks already covered by mock.close()
        if attr.startswith("_"):
            continue
        if not hasattr(MockShopmonkeyClient, attr):
            missing.add(attr)
    assert not missing, (
        f"MockShopmonkeyClient missing methods used by main.py: {sorted(missing)}"
    )


def test_mock_sheets_implements_all_used_methods(sheets_calls_in_main):
    """Every attr main.py reads on sheets_client must exist on the mock."""
    missing = set()
    for attr in sheets_calls_in_main:
        if attr.startswith("_"):
            continue
        if not hasattr(MockSheetsClient, attr):
            missing.add(attr)
    assert not missing, (
        f"MockSheetsClient missing methods used by main.py: {sorted(missing)}"
    )


@pytest.mark.parametrize(
    "real_cls,mock_cls,method_name",
    [
        (ShopmonkeyClient, MockShopmonkeyClient, "get_bookable_canned_services"),
        (ShopmonkeyClient, MockShopmonkeyClient, "get_canned_service"),
        (ShopmonkeyClient, MockShopmonkeyClient, "get_appointments_for_date"),
        (ShopmonkeyClient, MockShopmonkeyClient, "find_or_create_customer"),
        (ShopmonkeyClient, MockShopmonkeyClient, "find_or_create_vehicle"),
        (ShopmonkeyClient, MockShopmonkeyClient, "create_appointment"),
        (ShopmonkeyClient, MockShopmonkeyClient, "get_active_user_ids"),
        (ShopmonkeyClient, MockShopmonkeyClient, "health_check"),
        (ShopmonkeyClient, MockShopmonkeyClient, "close"),
        (SheetsClient, MockSheetsClient, "get_techs_for_department"),
        (SheetsClient, MockSheetsClient, "health_check"),
        (SheetsClient, MockSheetsClient, "get_cache_status"),
    ],
)
def test_mock_method_signature_matches_real(real_cls, mock_cls, method_name):
    """Mock methods must accept the same positional/keyword args as the real ones.

    We check that every parameter the real method has (other than self) also
    exists on the mock method. This catches accidental drift where the real
    client adds a kwarg the mocks don't handle.
    """
    real_sig = inspect.signature(getattr(real_cls, method_name))
    mock_sig = inspect.signature(getattr(mock_cls, method_name))

    real_params = {n for n in real_sig.parameters if n != "self"}
    mock_params = {n for n in mock_sig.parameters if n != "self"}

    missing_on_mock = real_params - mock_params
    assert not missing_on_mock, (
        f"{mock_cls.__name__}.{method_name} is missing params present on "
        f"{real_cls.__name__}.{method_name}: {sorted(missing_on_mock)}"
    )
