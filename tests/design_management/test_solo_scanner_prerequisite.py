"""Real production CLI and unmodified pins; workflow bodies never execute."""
import copy
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('policy',ROOT/'scripts/check_ci_isolation.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class SoloPrerequisite(unittest.TestCase):
    def setUp(self):
        try: import yaml
        except ModuleNotFoundError as exc:
            if exc.name!='yaml':raise
            self.skipTest('PyYAML required for real production scanner')
        self.yaml=yaml
        fixtures=ROOT/'tests/design_management/fixtures'
        self.new_a=(fixtures/'solo-disabled-preview/dev-worker-whatif-preview.yml.txt').read_bytes()
        self.new_b=(fixtures/'solo-disabled-preview/guarded-azure-worker-deploy.yml.txt').read_bytes()
        self.old_a=(fixtures/'c1-byte-pinned-preview/dev-worker-whatif-preview.yml.txt').read_bytes()
        self.old_b=(fixtures/'c1-byte-pinned-preview/guarded-azure-worker-deploy.yml.txt').read_bytes()

    def cli(self,a,b,expected,callee_path=None,caller_path=None):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);(root/'.github/workflows').mkdir(parents=True)
            if a is not None:(root/(callee_path or m.PREVIEW_CALLEE)).write_bytes(a)
            if b is not None:(root/(caller_path or m.GUARDED_AZURE)).write_bytes(b)
            result=subprocess.run([sys.executable,'-B',str(ROOT/'scripts/check_ci_isolation.py'),'--root',str(root)],capture_output=True,text=True)
            self.assertEqual(result.returncode,expected,result.stdout+result.stderr)

    def test_both_reviewed_pairs_and_callee_parking(self):
        for a,b in [(self.old_a,self.old_b),(self.new_a,self.new_b),(self.old_a,None),(self.new_a,None)]:
            with self.subTest(caller=b is not None,legacy=a==self.old_a):self.cli(a,b,0)

    def test_cross_pair_and_missing_callee_refused(self):
        for a,b in [(self.old_a,self.new_b),(self.new_a,self.old_b),(None,self.old_b),(None,self.new_b)]:
            with self.subTest(callee=a is not None):self.cli(a,b,1)

    def test_drift_pin_enable_privilege_and_trigger_refused(self):
        cases=[(self.new_a+b'\n',self.new_b),(self.new_a,self.new_b+b'\n'),
            (self.new_a,self.new_b.replace(m.SOLO_CALLEE_SHA.encode(),b'1'*40)),
            (self.new_a,self.new_b.replace(b'@'+m.SOLO_CALLEE_SHA.encode(),b'')),
            (self.new_a,self.new_b.replace(m.SOLO_CALLEE_SHA.encode(),m.PREVIEW_CALLEE_SHA.encode())),
            (self.new_a.replace(b'${{ false }}',b'${{ true }}'),self.new_b),
            (self.new_a,self.new_b.replace(b'${{ false }}',b'${{ true }}')),
            (self.new_a.replace(b'contents: read',b'contents: write'),self.new_b),
            (self.new_a,self.new_b.replace(b'actions: read',b'id-token: write')),
            (self.new_a,self.new_b.replace(b'workflow_dispatch:',b'pull_request_target:')),
            (self.new_a.replace(b'workflow_call:',b'push:'),self.new_b),
            (self.new_a,self.new_b.replace(b'workflow_dispatch:',b'unknown_event:')),
            (self.new_a,self.new_b.replace(b'    uses:',b'    secrets: inherit\n    uses:'))]
        for i,(a,b) in enumerate(cases):
            with self.subTest(case=i):self.cli(a,b,1)

    def test_hostile_yaml_and_wrong_paths_refused(self):
        for a,b in [(self.new_a+b'permissions: {}\n',self.new_b),
                    (self.new_a,self.new_b+b'permissions: {}\n'),
                    (self.new_a,self.new_b.replace(b'permissions:\n  contents: read',b'permissions: &p\n  contents: read')+b'extra: *p\n')]:
            self.cli(a,b,1)
        self.cli(self.new_a,self.new_b,1,callee_path='.github/workflows/dev-worker-whatif-preview.yaml')
        self.cli(self.new_a,self.new_b,1,caller_path='.github/workflows/other.yml')
        self.cli(self.new_a,self.new_b.replace(m.SOLO_REF.encode(),b'./.github/workflows/dev-worker-whatif-preview.yml'),1)

    def test_structural_hard_stop_defense_without_changing_hash_pins(self):
        for caller,raw in [(True,self.new_b),(False,self.new_a)]:
            doc=m.strict_load(self.yaml,raw.decode())
            self.assertTrue(m.solo_disabled_structure(doc,caller=caller))
            for name in doc['jobs']:
                for key,value in [('if','${{ true }}'),('permissions',{'id-token':'write'}),('secrets','inherit')]:
                    bad=copy.deepcopy(doc);bad['jobs'][name][key]=value
                    with self.subTest(caller=caller,job=name,key=key):self.assertFalse(m.solo_disabled_structure(bad,caller=caller))
            bad=copy.deepcopy(doc);bad['on']={'pull_request':{}}
            self.assertFalse(m.solo_disabled_structure(bad,caller=caller))
        doc=m.strict_load(self.yaml,self.new_b.decode())
        doc['jobs']['owner_whatif']['uses']=m.PREVIEW_REF
        self.assertFalse(m.solo_disabled_structure(doc,caller=True))

    def test_no_document_only_authority(self):
        for path,raw in [(m.PREVIEW_CALLEE,self.new_a),(m.GUARDED_AZURE,self.new_b)]:
            self.assertTrue(m.examine(path,m.strict_load(self.yaml,raw.decode())))
