"""Static regression checks for parking the legacy inline What-If route."""
import importlib.util
from pathlib import Path
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('parking_isolation', ROOT / 'scripts/check_ci_isolation.py')
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class LegacyWhatifParkingTests(unittest.TestCase):
    def setUp(self):
        self.path = ROOT / checker.GUARDED_AZURE
        self.raw = self.path.read_text()
        self.doc = checker.strict_load(yaml, self.raw)

    def test_legacy_route_and_unused_dispatch_inputs_are_absent(self):
        self.assertEqual(set(self.doc['jobs']), {'validate', 'preview', 'whatif', 'deploy'})
        inputs = self.doc['on']['workflow_dispatch']['inputs']
        self.assertEqual(inputs['operation']['options'], ['preview', 'deploy'])
        self.assertEqual(set(inputs), {'operation', 'confirmation', 'approved_sha'})
        for token in ('owner_whatif', 'whatif_admission', 'AZURE_WHATIF_CLIENT_ID',
                      'AZURE_DEV_WHATIF_ENABLED', 'ProviderNoRbac', 'PREVIEW-DEV-WORKER'):
            self.assertNotIn(token, self.raw)
        for suffix in ('yml', 'yaml'):
            self.assertFalse((ROOT / ('.github/workflows/dev-worker-whatif-preview.' + suffix)).exists())
        self.assertEqual(checker.scan(ROOT), [])

    def test_deployment_still_requires_complete_validation_chain(self):
        jobs = self.doc['jobs']
        for name, predecessor in (('preview', 'validate'), ('whatif', 'preview'), ('deploy', 'whatif')):
            self.assertEqual(jobs[name]['needs'], predecessor)
            self.assertNotIn('always()', jobs[name]['if'])
            self.assertIn("github.event_name == 'workflow_dispatch'", jobs[name]['if'])
            self.assertIn("github.ref == 'refs/heads/main'", jobs[name]['if'])
            self.assertIn("github.actor == 'ScottMDavistv'", jobs[name]['if'])
        for name in ('whatif', 'deploy'):
            self.assertIn("inputs.operation == 'deploy'", jobs[name]['if'])
            self.assertEqual(jobs[name]['environment'], 'dev-deployment-apply')
        for guard in ("inputs.approved_sha == github.sha", "inputs.confirmation == 'DEPLOY-DEV-WORKER'",
                      "vars.AZURE_DEV_DEPLOY_ENABLED == 'true'", "vars.PROTECTED_MAIN_VERIFIED == 'true'"):
            self.assertIn(guard, jobs['deploy']['if'])
        run = jobs['validate']['steps'][-1]['run']
        self.assertIn('test "$OPERATION" = preview -o "$OPERATION" = deploy', run)
        self.assertNotIn('= whatif', run)
        for check in ('test "$APPROVED_SHA" = "$GITHUB_SHA"', 'test "$CONFIRMATION" = DEPLOY-DEV-WORKER',
                      'test "$DEPLOY_ENABLED" = true', 'test "$MAIN_PROTECTED" = true'):
            self.assertIn(check, run)

    def test_removed_route_cannot_survive_in_another_workflow(self):
        for path in (ROOT / '.github/workflows').iterdir():
            if path.suffix.lower() not in ('.yml', '.yaml'):
                continue
            doc = checker.strict_load(yaml, path.read_text())
            for name, job in doc.get('jobs', {}).items():
                with self.subTest(path=path.name, job=name):
                    self.assertNotIn(name, ('owner_whatif', 'whatif_admission'))
                    self.assertNotIn('dev-worker-whatif-preview', str(job))
        self.assertEqual(self.doc['permissions'], {'contents': 'read'})
        self.assertNotIn('permissions', self.doc['jobs']['validate'])
        self.assertNotIn('environment', self.doc['jobs']['validate'])
