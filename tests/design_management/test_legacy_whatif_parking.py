"""Parking remains unconditional; no deployment route in the new caller."""
import importlib.util
from pathlib import Path
import unittest
ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('parking_isolation', ROOT / 'scripts/check_ci_isolation.py')
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)

class LegacyWhatifParkingTests(unittest.TestCase):
    def load(self, path):
        try:
            import yaml
        except ModuleNotFoundError as exc:
            if exc.name != 'yaml': raise
            self.skipTest('PyYAML absent; parser-dependent parking checks require it')
        return checker.strict_load(yaml, (ROOT / path).read_text())

    def test_text_parking_and_no_apply(self):
        for path in (checker.GUARDED_AZURE, checker.PREVIEW_CALLEE):
            raw = (ROOT / path).read_text()
            self.assertIn('if: ${{ false }}', raw)
            for forbidden in ('az deployment group create', 'DEPLOY-DEV-WORKER', 'id-token: write'):
                self.assertNotIn(forbidden, raw)
        broad = (ROOT / '.github/workflows/deploy-infrastructure.yml').read_text()
        self.assertIn('exit 1', broad)
        self.assertNotIn('az deployment', broad)

    def test_all_preview_jobs_unconditionally_disabled(self):
        for path in (checker.GUARDED_AZURE, checker.PREVIEW_CALLEE):
            doc = self.load(path)
            self.assertEqual(doc['permissions'], {'contents': 'read'})
            for job in doc['jobs'].values():
                self.assertEqual(job['if'], '${{ false }}')
                self.assertNotIn('id-token', job.get('permissions', {}))
        self.assertEqual(checker.scan(ROOT), [])

    def test_preview_only_inputs_and_same_run_environment(self):
        caller = self.load(checker.GUARDED_AZURE)
        self.assertEqual(set(caller['jobs']), {'owner_whatif'})
        self.assertEqual(set(caller['on']), {'workflow_dispatch'})
        inputs = caller['on']['workflow_dispatch']['inputs']
        self.assertEqual(inputs['operation']['options'], ['whatif'])
        self.assertEqual(set(inputs), {'operation','confirmation','approved_sha','approved_preview_target','approved_preview_digest'})
        job = caller['jobs']['owner_whatif']
        self.assertNotIn('with', job)
        self.assertNotIn('secrets', job)
        callee = self.load(checker.PREVIEW_CALLEE)
        self.assertEqual(set(callee['on']), {'workflow_call'})
        self.assertEqual(callee['jobs']['owner_whatif']['environment'], 'dev-worker-whatif-preview')
        self.assertEqual(callee['jobs']['owner_whatif']['needs'], 'whatif_admission')

    def test_broad_apply_stays_denied(self):
        broad = self.load('.github/workflows/deploy-infrastructure.yml')
        self.assertEqual(set(broad['jobs']), {'disabled'})
        self.assertNotIn('environment', broad['jobs']['disabled'])
        self.assertTrue(any('exit 1' in step.get('run','') for step in broad['jobs']['disabled']['steps']))
