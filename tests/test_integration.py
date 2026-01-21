"""Integration tests for Shopmonkey scheduler (requires live API access).

These tests hit real APIs and require valid credentials in .env file.
Run with: pytest tests/test_integration.py -v -s
"""

import pytest
import asyncio
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()


# Skip all tests if credentials aren't available
pytestmark = pytest.mark.integration


@pytest.fixture
def sheets_client():
    """Create a SheetsClient instance."""
    from sheets_client import SheetsClient
    return SheetsClient()


@pytest.fixture
async def shopmonkey_client():
    """Create a ShopmonkeyClient instance."""
    from shopmonkey_client import ShopmonkeyClient
    client = ShopmonkeyClient()
    yield client
    await client.close()


class TestGoogleSheetsIntegration:
    """Integration tests for Google Sheets API."""

    def test_can_connect_to_sheets(self, sheets_client):
        """Should be able to connect to Google Sheets."""
        departments = sheets_client._sync_get_all_departments()
        assert isinstance(departments, list)
        assert len(departments) > 0

    def test_can_get_tech_departments(self, sheets_client):
        """Should be able to get technician department mappings."""
        tech_data = sheets_client._sync_get_tech_departments()
        assert isinstance(tech_data, dict)
        assert len(tech_data) > 0

        # Each tech should have required fields
        for tech_id, info in tech_data.items():
            assert "tech_name" in info
            assert "departments" in info
            assert isinstance(info["departments"], dict)

    def test_can_get_techs_for_department(self, sheets_client):
        """Should be able to get techs for a specific department."""
        departments = sheets_client._sync_get_all_departments()
        if departments:
            # Test with first available department
            techs = sheets_client._sync_get_techs_for_department(departments[0])
            assert isinstance(techs, list)
            for tech in techs:
                assert "tech_id" in tech
                assert "tech_name" in tech
                assert "priority" in tech


class TestShopmonkeyIntegration:
    """Integration tests for Shopmonkey API."""

    @pytest.mark.asyncio
    async def test_can_get_bookable_services(self, shopmonkey_client):
        """Should be able to get bookable canned services."""
        services = await shopmonkey_client.get_bookable_canned_services()
        assert isinstance(services, list)
        assert len(services) > 0

        # Each service should have required fields
        for svc in services:
            assert "id" in svc
            assert "name" in svc

    @pytest.mark.asyncio
    async def test_services_have_labels(self, shopmonkey_client):
        """Most services should have labels for department mapping."""
        services = await shopmonkey_client.get_bookable_canned_services()

        labeled = [s for s in services if s.get("labels")]
        unlabeled = [s for s in services if not s.get("labels")]

        # At least 90% should have labels
        label_rate = len(labeled) / len(services) if services else 0
        assert label_rate >= 0.9, f"Only {label_rate:.0%} of services have labels"

        if unlabeled:
            print(f"\nWarning: {len(unlabeled)} services without labels:")
            for s in unlabeled:
                print(f"  - {s.get('name')}")

    @pytest.mark.asyncio
    async def test_can_get_users(self, shopmonkey_client):
        """Should be able to get users/technicians."""
        users = await shopmonkey_client.get_users()
        assert isinstance(users, list)
        assert len(users) > 0

    @pytest.mark.asyncio
    async def test_can_get_appointments(self, shopmonkey_client):
        """Should be able to get appointments for a date."""
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        appointments = await shopmonkey_client.get_appointments_for_date(today)
        assert isinstance(appointments, list)


class TestEndToEndFlow:
    """End-to-end tests for the availability check flow."""

    @pytest.mark.asyncio
    async def test_service_to_tech_mapping_flow(self, sheets_client, shopmonkey_client):
        """Test the complete service -> department -> technician flow."""
        from main import get_department_from_service

        # Get a service from Shopmonkey
        services = await shopmonkey_client.get_bookable_canned_services()
        assert services, "No bookable services found"

        # Find a service with a label
        labeled_services = [s for s in services if s.get("labels")]
        assert labeled_services, "No services with labels found"

        test_service = labeled_services[0]
        service_name = test_service.get("name")
        print(f"\nTesting with service: {service_name}")

        # Get department from label
        department = get_department_from_service(test_service)
        assert department, f"No department found for {service_name}"
        print(f"  Department: {department}")

        # Get qualified technicians
        techs = sheets_client._sync_get_techs_for_department(department)
        print(f"  Qualified techs: {[t['tech_name'] for t in techs]}")

        # We should have at least one tech (or this department needs setup)
        if not techs:
            pytest.skip(f"No techs assigned to {department} department")

    @pytest.mark.asyncio
    async def test_all_labeled_services_have_techs(self, sheets_client, shopmonkey_client):
        """Verify all labeled services can find qualified technicians."""
        from main import get_department_from_service

        services = await shopmonkey_client.get_bookable_canned_services()
        available_depts = set(sheets_client._sync_get_all_departments())

        issues = []
        for svc in services:
            dept = get_department_from_service(svc)
            if dept and dept not in available_depts:
                issues.append(f"{svc.get('name')}: department '{dept}' not in sheet")
            elif dept:
                techs = sheets_client._sync_get_techs_for_department(dept)
                if not techs:
                    issues.append(f"{svc.get('name')}: no techs for '{dept}'")

        if issues:
            print("\nMapping issues found:")
            for issue in issues:
                print(f"  - {issue}")

        # Allow some issues but flag if too many
        issue_rate = len(issues) / len(services) if services else 0
        assert issue_rate < 0.2, f"{len(issues)} services have mapping issues"
