"""Synthetic admission model and real disabled CLI; no network or cloud execution."""
import copy
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'scripts/verify_solo_preview_dispatch.py'
spec = importlib.util.spec_from_file_location('solo', SCRIPT)
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
NOW = dt.datetime(2026,11,27,tzinfo=dt.timezone.utc)
IDENTITY = (900000001,900000002,'offline-fixture[bot]')
SHA = 'a' * 40

class Admission(unittest.TestCase):
    def setUp(self):
        self.env = dict(GITHUB_SHA=SHA,GITHUB_RUN_ID='900000003',GITHUB_REPOSITORY=m.REPO,
            GITHUB_REPOSITORY_ID=str(m.REPO_ID),GITHUB_REPOSITORY_OWNER_ID=str(m.OWNER_ID),
            GITHUB_WORKFLOW_REF=m.REPO+'/'+m.WORKFLOW+'@refs/heads/main',
            GITHUB_EVENT_NAME='workflow_dispatch',GITHUB_REF='refs/heads/main',
            GITHUB_ACTOR=IDENTITY[2],GITHUB_ACTOR_ID=str(IDENTITY[1]),
            GITHUB_TRIGGERING_ACTOR=IDENTITY[2],GITHUB_RUN_ATTEMPT='1',
            APPROVED_SHA=SHA,CONFIRMATION='PREVIEW-DEV-WORKER',APPROVED_TARGET=m.TARGET,
            APPROVED_DIGEST=m.DIGEST,OPERATION='whatif')
        actor=dict(id=IDENTITY[1],login=IDENTITY[2],type='Bot')
        repo=dict(id=m.REPO_ID,full_name=m.REPO,owner=dict(id=m.OWNER_ID,login='ScottMDavistv'))
        self.run=dict(id=900000003,run_attempt=1,event='workflow_dispatch',head_branch='main',
            head_sha=SHA,path=m.WORKFLOW,actor=actor,triggering_actor=copy.deepcopy(actor),
            repository=repo,head_repository=copy.deepcopy(repo),created_at='2026-11-27T00:00:00Z')

    def verify(self, run=None, env=None, now=NOW, identity=IDENTITY):
        return m.deadline(self.run if run is None else run,self.env if env is None else env,now,identity=identity)

    def test_positive_model_is_not_activation(self):
        self.assertEqual(self.verify(), int(NOW.timestamp())+3600)
        with self.assertRaises(ValueError): self.verify(identity=(m.APP_ID,m.BOT_ID,m.BOT_LOGIN))

    def test_every_required_context_mismatch_or_missing(self):
        for key in self.env:
            for value in ('unknown', None):
                with self.subTest(key=key,value=value), self.assertRaises((ValueError,TypeError)):
                    self.verify(env=dict(self.env,**{key:value}))
        for identity in [(0,900000002,IDENTITY[2]),(900000001,0,IDENTITY[2]),(900000001,m.OWNER_ID,IDENTITY[2]),(1,2,'ScottMDavistv')]:
            with self.assertRaises(ValueError): self.verify(identity=identity)
        with self.assertRaises(ValueError):
            self.verify(env=dict(self.env,GITHUB_ACTOR='ScottMDavistv',GITHUB_ACTOR_ID=str(m.OWNER_ID)))

    def test_metadata_identity_and_provenance_drift(self):
        for key in ('id','run_attempt','event','head_branch','head_sha','path','created_at'):
            with self.subTest(key=key),self.assertRaises((ValueError,TypeError)):
                self.verify(run=dict(self.run,**{key:'unknown'}))
        for field in ('actor','triggering_actor','repository','head_repository'):
            for key in self.run[field]:
                run=copy.deepcopy(self.run);run[field][key]='unknown'
                with self.subTest(field=field,key=key),self.assertRaises(ValueError): self.verify(run=run)
        for field in ('actor','triggering_actor'):
            run=copy.deepcopy(self.run);run[field].update(login='ScottMDavistv',id=m.OWNER_ID,type='User')
            with self.assertRaises(ValueError):self.verify(run=run)
        for k,v in [('run_attempt',2),('id',True),('head_sha','0'*40)]:
            with self.assertRaises(ValueError):self.verify(run=dict(self.run,**{k:v}))

    def test_expiry_future_and_boundary(self):
        for delta in (-1,3600,7200):
            with self.assertRaises(ValueError):self.verify(now=NOW+dt.timedelta(seconds=delta))
        self.assertEqual(self.verify(now=NOW+dt.timedelta(seconds=3599)),int(NOW.timestamp())+3600)

    def test_real_cli_unpark_attempts_refuse_normal_and_optimized(self):
        for flags in ([],['-O'],['--check-pins']):
            env=dict(os.environ,**self.env,APP_ID=str(IDENTITY[0]),BOT_ID=str(IDENTITY[1]),BOT_LOGIN=IDENTITY[2],RELEASED='true')
            for payload in (json.dumps(self.run),'private-synthetic-hold', '{"id":1,"id":2}'):
                result=subprocess.run([sys.executable,*(['-O'] if flags == ['-O'] else []),'-I','-B',str(SCRIPT),*(['--check-pins'] if flags == ['--check-pins'] else [])],input=payload,text=True,capture_output=True,env=env)
                self.assertEqual((result.returncode,result.stdout,result.stderr),(78,'','Solo preview admission refused.\n'))

    def test_duplicate_parser_unknown_type_and_no_raw_error(self):
        with self.assertRaises(ValueError):json.loads('{"id":1,"id":2}',object_pairs_hook=m.unique_pairs)
        for run in ([],None,'unknown'):
            with self.assertRaises(ValueError):self.verify(run=run if run is not None else 'unknown')

class RealWorkflowBoundaries(unittest.TestCase):
    def setUp(self):
        try:
            import yaml
        except ModuleNotFoundError as exc:
            if exc.name != 'yaml': raise
            self.skipTest('PyYAML absent; real workflow boundary checks require it')

    def test_production_scanner_mutations_via_cli(self):
        import tempfile
        import importlib.util
        spec=importlib.util.spec_from_file_location('policy',ROOT/'scripts/check_ci_isolation.py')
        checker=importlib.util.module_from_spec(spec);spec.loader.exec_module(checker)
        try: import yaml
        except ModuleNotFoundError: self.skipTest('PyYAML required for real scanner fixtures')
        caller=(ROOT/checker.GUARDED_AZURE).read_bytes()
        callee=(ROOT/checker.PREVIEW_CALLEE).read_bytes()
        cases=[('exact',caller,callee,0),('missing',caller,None,1),
            ('caller drift',caller+b'\n',callee,1),('callee drift',caller,callee+b'\n',1),
            ('wrong pin',caller.replace(checker.SOLO_CALLEE_SHA.encode(),b'1'*40),callee,1),
            ('absent pin',caller.replace(b'@'+checker.SOLO_CALLEE_SHA.encode(),b''),callee,1),
            ('enabled caller',caller.replace(b'${{ false }}',b'${{ true }}'),callee,1),
            ('enabled callee',caller,callee.replace(b'${{ false }}',b'${{ true }}'),1),
            ('caller oidc',caller.replace(b'actions: read',b'actions: read\n      id-token: write'),callee,1),
            ('duplicate permissions',caller+b'permissions: {}\n',callee,1),
            ('unknown trigger',caller.replace(b'workflow_dispatch:',b'unknown_event:'),callee,1)]
        for name,c,a,expected in cases:
            with self.subTest(case=name),tempfile.TemporaryDirectory() as t:
                root=Path(t);(root/'.github/workflows').mkdir(parents=True)
                (root/checker.GUARDED_AZURE).write_bytes(c)
                if a is not None:(root/checker.PREVIEW_CALLEE).write_bytes(a)
                result=subprocess.run([sys.executable,'-B',str(ROOT/'scripts/check_ci_isolation.py'),'--root',str(root)],capture_output=True,text=True)
                self.assertEqual(result.returncode,expected,result.stdout+result.stderr)

    def test_governance_entry_point_is_not_removed(self):
        for flags in ([], ['-O']):
            result=subprocess.run([sys.executable,*flags,'-B',str(ROOT/'scripts/check_azure_deployment_governance.py')],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('Hard-disabled preview governance passed',result.stdout)

    def test_governance_rejects_reactivation_and_broad_apply(self):
        import tempfile
        for mutation in ('callee', 'caller', 'legacy'):
            with tempfile.TemporaryDirectory() as t:
                root=Path(t);(root/'scripts').mkdir();(root/'.github/workflows').mkdir(parents=True)
                for name in ('check_azure_deployment_governance.py','check_ci_isolation.py'):
                    (root/'scripts'/name).write_bytes((ROOT/'scripts'/name).read_bytes())
                for name in ('dev-worker-whatif-preview.yml','guarded-azure-worker-deploy.yml','deploy-infrastructure.yml'):
                    data=(ROOT/'.github/workflows'/name).read_text()
                    if mutation == 'callee' and name == 'dev-worker-whatif-preview.yml': data=data.replace('${{ false }}','${{ true }}')
                    if mutation == 'caller' and name == 'guarded-azure-worker-deploy.yml': data=data.replace('${{ false }}','${{ true }}')
                    if mutation == 'legacy' and name == 'deploy-infrastructure.yml': data+='\n# az deployment group create\n'
                    (root/'.github/workflows'/name).write_text(data)
                for flags in ([],['-O']):
                    result=subprocess.run([sys.executable,*flags,'-B',str(root/'scripts/check_azure_deployment_governance.py')],capture_output=True,text=True)
                    self.assertEqual((result.returncode,result.stdout,result.stderr),(1,'','Azure preview governance refused.\n'))
