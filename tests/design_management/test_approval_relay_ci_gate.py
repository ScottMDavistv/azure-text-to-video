"""Direct unit and CLI coverage for the inactive ancestry gate."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.approval_relay_ci_gate import verify

ROOT = Path(__file__).resolve().parents[2]
SHA = "a" * 40


def snapshot():
    return {
        "repository": "ScottMDavistv/azure-text-to-video",
        "coding_pr": 63, "target_pr": 46,
        "head_branch": "copilot/task", "base_branch": "security/feature",
        "target_branch": "security/feature", "target_head_sha": SHA,
        "origin_sha": SHA, "origin_verified": True,
        "merge_base_sha": SHA, "is_target_ancestor": True,
        "coding_head_sha": "b" * 40, "draft": True,
        "source": "trusted-protected-task-ledger", "task_approved": True,
    }


class DirectGateTests(unittest.TestCase):
    def test_valid(self):
        self.assertTrue(verify(snapshot()))

    def test_schema_and_negative_paths(self):
        mutations = {
            "repository": "attacker/repo",
            "coding_pr": 46, "target_pr": 63,
            "base_branch": "main", "origin_sha": "b" * 40,
            "origin_verified": False, "merge_base_sha": "b" * 40,
            "is_target_ancestor": False, "coding_head_sha": "bad",
            "draft": False, "source": "PR-description", "task_approved": False,
            "target_head_sha": "bad", "target_branch": "main",
        }
        for field, value in mutations.items():
            with self.subTest(field=field):
                case = snapshot()
                case[field] = value
                with self.assertRaises(ValueError):
                    verify(case)
        for invalid in (None, [], {}, {"unexpected": True}):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    verify(invalid)

    def run_cli(self, value):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "snapshot.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(ROOT / "scripts/approval_relay_ci_gate.py"), str(path)],
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10,
                cwd=str(ROOT), check=False,
            )

    def test_cli_success_and_fail_closed(self):
        self.assertEqual(self.run_cli(snapshot()).returncode, 0)
        bad = copy.deepcopy(snapshot())
        bad["origin_verified"] = False
        result = self.run_cli(bad)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FAIL CLOSED", result.stderr)

    def test_cli_malformed_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "invalid.json"
            path.write_text("{", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts/approval_relay_ci_gate.py"), str(path)],
                text=True, capture_output=True, timeout=10, cwd=str(ROOT), check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("FAIL CLOSED", result.stderr)


if __name__ == "__main__":
    unittest.main()
