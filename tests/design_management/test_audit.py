"""Offline regression tests for design-management reporting, no network or keys."""
import importlib.util
import pathlib
import unittest
from unittest.mock import patch

PATH = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "design_management_audit.py"
spec = importlib.util.spec_from_file_location("design_management_audit", PATH)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)

class DesignManagementTests(unittest.TestCase):
    def test_markers_replace_without_destroying_owner_notes(self):
        original = "Owner notes\n" + audit.START + "\nold\n" + audit.END + "\nAfterword"
        updated = audit.replace_report(original, "new")
        self.assertIn("Owner notes", updated)
        self.assertIn("Afterword", updated)
        self.assertNotIn("old", updated)
        self.assertEqual(updated.count(audit.START), 1)
        self.assertIn("new", updated)

    def test_append_when_missing(self):
        self.assertIn(audit.START, audit.replace_report("Original", "snapshot"))

    def test_capability_presence_is_not_readiness(self):
        report = audit.render_report({"paths": ["frontend/src/App.tsx"], "sha": "abc",
                                      "runs": [], "prs": []}, "2026-11-16 12:00 UTC")
        self.assertIn("NOT VERIFIED", report)
        self.assertIn("file existence only", report)
        self.assertIn("No run evidence returned", report)

    def test_capabilities_require_all_files(self):
        found = dict(audit.capability_results(["backend/main.py"]))
        self.assertFalse(found["backend job APIs"])
        found = dict(audit.capability_results(["backend/main.py", "backend/jobs.py"]))
        self.assertTrue(found["backend job APIs"])

    def test_ci_does_not_turn_into_product_success(self):
        report = audit.render_report({"paths": [], "sha": "deadbeef",
                                      "runs": [("Backend Tests", "success", "https://github.com/example/run")],
                                      "prs": [(3, "Finance review", "https://github.com/example/pr", True)]},
                                     "2026-11-16 12:00 UTC")
        self.assertIn("NOT VERIFIED", report)
        self.assertIn("success", report)
        self.assertIn("(draft)", report)

    def test_blocker_candidates_are_deterministic(self):
        keys = [key for key, _ in audit.blocker_candidates([])]
        self.assertIn("front-end-creation-journey", keys)
        self.assertIn("300-second-acceptance", keys)

    def test_closed_issue_marker_prevents_duplicate(self):
        existing = [{"body": "<!-- design-management-blocker:300-second-acceptance -->",
                     "number": 21}]
        with patch.object(audit, "github_get", return_value=existing):
            keys = audit.list_existing_blocker_keys("owner/repo", "token")
        self.assertIn("300-second-acceptance", keys)

    def test_report_includes_inventory_without_conflating_health(self):
        sample = {"paths": [], "sha": "abc", "runs": [], "prs": [],
                  "azure": {"status": "inventory only", "resource_group": "rg-dev", "resources": []}}
        report = audit.render_report(sample, "2026-11-16 12:00 UTC")
        self.assertIn("inventory only", report)
        self.assertIn("NOT VERIFIED", report)

if __name__ == "__main__":
    unittest.main()
