"""Unit tests for department lookup from Shopmonkey labels."""

import pytest
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from main import get_department_from_service, LABEL_TO_DEPARTMENT


class TestGetDepartmentFromService:
    """Tests for get_department_from_service function."""

    def test_returns_label_name_when_present(self):
        """Should return the label name when service has a label."""
        service = {
            "name": "Detail - Engine Bay Cleaning",
            "labels": [{"name": "Detail", "color": "blue"}],
        }
        assert get_department_from_service(service) == "Detail"

    def test_returns_none_when_no_labels(self):
        """Should return None when service has no labels."""
        service = {
            "name": "Customer Consultation",
            "labels": [],
        }
        assert get_department_from_service(service) is None

    def test_returns_none_when_labels_key_missing(self):
        """Should return None when labels key is missing."""
        service = {"name": "Some Service"}
        assert get_department_from_service(service) is None

    def test_returns_none_when_label_name_empty(self):
        """Should return None when label name is empty string."""
        service = {
            "name": "Some Service",
            "labels": [{"name": "", "color": "blue"}],
        }
        assert get_department_from_service(service) is None

    def test_uses_first_label_when_multiple(self):
        """Should use first label when service has multiple labels."""
        service = {
            "name": "Multi-Label Service",
            "labels": [
                {"name": "Primary", "color": "blue"},
                {"name": "Secondary", "color": "red"},
            ],
        }
        assert get_department_from_service(service) == "Primary"

    def test_applies_label_mapping_when_exists(self):
        """Should apply LABEL_TO_DEPARTMENT mapping if configured."""
        # This test documents the mapping behavior
        # Currently LABEL_TO_DEPARTMENT is empty, so no mapping occurs
        service = {
            "name": "Window Tint - Full Sedan",
            "labels": [{"name": "Window Tint", "color": "blue"}],
        }
        result = get_department_from_service(service)
        # With empty mapping, returns label as-is
        expected = LABEL_TO_DEPARTMENT.get("Window Tint", "Window Tint")
        assert result == expected

    def test_handles_none_label_name(self):
        """Should handle None as label name."""
        service = {
            "name": "Some Service",
            "labels": [{"name": None, "color": "blue"}],
        }
        assert get_department_from_service(service) is None

    def test_alignment_label(self):
        """Should correctly identify Alignment department."""
        service = {
            "name": "Alignment - Four Wheel",
            "labels": [{"name": "Alignment", "color": "blue"}],
        }
        assert get_department_from_service(service) == "Alignment"

    def test_bedliner_label(self):
        """Should correctly identify Bedliner department."""
        service = {
            "name": "Bedliner - Short Bed",
            "labels": [{"name": "Bedliner", "color": "blue"}],
        }
        assert get_department_from_service(service) == "Bedliner"

    def test_window_tint_label(self):
        """Should correctly identify Window Tint department."""
        service = {
            "name": "Window Tint - Ceramic",
            "labels": [{"name": "Window Tint", "color": "blue"}],
        }
        assert get_department_from_service(service) == "Window Tint"
