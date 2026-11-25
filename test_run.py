import io
import json
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from run import *

class IdentityError(PermissionError):
    pass

class ScopeError(PermissionError):
    pass

class LocalChecks(unittest.TestCase):
    def test_registration_auth_categories_are_fixed_and_still_fail_closed(self):
        secret = "DO-NOT-LOG-FIXTURE"
        def verify(token):
            if token == "scope":
                raise ScopeError(secret)
            if token == "connection":
                try:
                    raise PyJWKClientConnectionError(secret)
                except PyJWKClientConnectionError as error:
                    raise IdentityError(secret) from error
            if token == "rejected":
                raise IdentityError(secret)
            if token == "unexpected":
                raise RuntimeError(secret)
            return token
        api, state = FastAPI(), RunState()
        records = []
        modules = {
            "project_models": SimpleNamespace(Project=lambda *a: a, ProjectRole=SimpleNamespace(MEMBER="member")),
            "project_oidc": SimpleNamespace(verify_entra_access_claims=verify, ProjectIdentityError=IdentityError, ProjectScopeDenied=ScopeError),
            "project_store": SimpleNamespace(create_project=lambda *a: None, add_membership=lambda *a: None),
        }
        with patch.dict(sys.modules, modules):
            add_controls(api, state, SimpleNamespace(mapping_identity=lambda token, verify: verify(token)),
                         SimpleNamespace(create_entity=records.append), "marker")
        client = TestClient(api)
        code = state.codes["member"]
        cases = [("", "bearer-missing"), ("Basic " + secret, "bearer-invalid"),
                 ("Bearer " + "A"*8193, "bearer-invalid"),
                 ("Bearer scope", "api-scope-missing"),
                 ("Bearer connection", "signing-key-service-unavailable"),
                 ("Bearer rejected", "token-rejected"), ("Bearer unexpected", "verification-error")]
        for auth, category in cases:
            with self.subTest(category=category), patch("sys.stdout", new_callable=io.StringIO) as out:
                response = client.post(CONTROL + "/enroll", json={"code":code},
                                       headers={"Origin":ORIGIN,"Authorization":auth})
                self.assertEqual(response.status_code,401)
                self.assertEqual(response.json(),{"detail":"Verified test sign-in required"})
                self.assertEqual(out.getvalue(),f"TKT90 registration-auth={category}\n")
                self.assertEqual(state.codes["member"],code)
                self.assertEqual(records,[])
        with patch("sys.stdout", new_callable=io.StringIO) as out:
            response = client.post(CONTROL + "/enroll", json={"code":code},
                                   headers={"Origin":"https://example.invalid","Authorization":"Bearer scope"})
            self.assertEqual(response.status_code,403)
            self.assertEqual(out.getvalue(),"")

    def test_direct_demands_all_exact_denials(self):
        class Fake:
            def __init__(self, expected, detail):
                self.seen, self.expected, self.detail = [], expected, detail
            def open(self, req, timeout):
                self.seen.append((req.full_url, req.get_header("Authorization")))
                body = io.BytesIO(json.dumps({"detail": self.detail}).encode())
                raise HTTPError(req.full_url, self.expected, "fixture", {}, body)
        f = Fake(403, "Project access denied")
        self.assertTrue(verify_http(f, "/fixture", "Bearer synthetic", 403))
        self.assertEqual(f.seen, [(ORIGIN + "/fixture" + x, "Bearer synthetic") for x in SUFFIXES])
        self.assertFalse(verify_http(Fake(403, "Other 403"), "/fixture", "Bearer synthetic", 403))
        self.assertTrue(verify_http(Fake(401, "Bearer token required"), "/fixture", None, 401))
        self.assertFalse(verify_http(Fake(302, "Redirect"), "/fixture", None, 401))

    def test_enrollment_render_gate_and_exact_negative_statuses(self):
        state = RunState()
        api = FastAPI()
        memberships, records = [], []
        ident = "12345678-1234-1234-1234-123456789012"
        base = f"/projects/{state.project}/jobs"
        @api.post(base, status_code=201)
        def create():
            return {"job_id": ident}
        @api.post(base + "/" + ident + "/queue")
        def queue():
            return {"status": "queued"}
        @api.get(base + "/" + ident + "/video/metadata")
        def metadata():
            return {"ok": True}
        @api.get(base + "/" + ident + "/video")
        def video():
            return PlainTextResponse("fixture", headers={"Cache-Control": CACHE})
        extras = {
            "project_models": SimpleNamespace(Project=lambda *a: a, ProjectRole=SimpleNamespace(MEMBER="member")),
            "project_oidc": SimpleNamespace(verify_entra_access_claims=lambda x: x, ProjectIdentityError=IdentityError, ProjectScopeDenied=ScopeError),
            "project_store": SimpleNamespace(create_project=lambda *a: None, add_membership=lambda *a: memberships.append(a)),
        }
        with patch.dict(sys.modules, extras), patch("run.verify_http", return_value=True) as direct:
            add_controls(api, state, SimpleNamespace(mapping_identity=lambda token, verify: verify(token)),
                         SimpleNamespace(create_entity=records.append), "marker")
            client = TestClient(api)
            a = {"Origin": ORIGIN, "Authorization": "Bearer one"}
            b = {"Origin": ORIGIN, "Authorization": "Bearer two"}
            fake = {"Origin": ORIGIN, "Authorization": "Bearer other"}
            unsigned = {"Origin": ORIGIN}
            m, n = state.codes["member"], state.codes["nonmember"]
            assert client.post(CONTROL + "/enroll", json={"code":m}, headers={**a,"Origin":"https://example.org"}).status_code == 403
            assert client.post(base, headers=a).status_code == 409
            assert client.post(CONTROL + "/enroll", json={"code":m}, headers=a).status_code == 200
            assert client.post(CONTROL + "/enroll", json={"code":n}, headers=a).status_code == 403
            assert client.post(CONTROL + "/enroll", json={"code":n}, headers=b).status_code == 200
            self.assertEqual((len(memberships),len(records)), (1,2))
            assert client.post(base, headers=b).status_code == 403
            assert client.post(base, headers=a).status_code == 201
            assert client.post(base, headers=a).status_code == 409
            assert client.post(base + "/" + ident + "/queue", headers=a).status_code == 200
            assert client.post(base + "/" + ident + "/queue", headers=a).status_code == 409
            state.complete, state.data_hash = True, "expected"
            assert client.post(CONTROL + "/check", headers=fake).status_code == 403
            assert client.post(CONTROL + "/check", headers=a).status_code == 409
            for suffix in SUFFIXES[1:]:
                assert client.get(base + "/" + ident + suffix, headers={"Sec-Fetch-Dest":"empty"}).status_code == 200
            for headers, expected in ((a,200),(b,403),(unsigned,401)):
                response = client.post(CONTROL + "/check", headers=headers)
                assert response.status_code == 200 and response.json()["expected"] == expected, response.text
                assert direct.call_args.args[-1] == expected
                assert client.post(CONTROL + "/report", headers=headers, json={"results":[expected]*3}).status_code == 200
            self.assertFalse(state.accepted)
            assert client.post(CONTROL + "/media-proof", headers=b, json={"sha":"expected","played":True}).status_code == 409
            assert client.post(CONTROL + "/media-proof", headers=a, json={"sha":"wrong","played":True}).status_code == 409
            assert client.post(CONTROL + "/media-proof", headers=a, json={"sha":"expected","played":True}).status_code == 200
            self.assertTrue(state.accepted)

if __name__ == "__main__":
    unittest.main()
