# Ticket #90: supervised local acceptance archive

Reference snapshot only. **Not a PR, not for merging, and not a CI workflow.**
The original run passed with the repository at
`606dd91663b8bc63f87773dd70988898d55572d0`. See the
[acceptance report](https://github.com/ScottMDavistv/azure-text-to-video/issues/90#issuecomment-7177559252).
This sanitized copy replaces personal setup details with local inputs. It is not
the byte-identical launcher used in that run, and has not been rerun against Entra.

## Offline checks

```bash
bash -n launch.sh
python3 local_studio.py --offline-self-test
node --input-type=module --check < panel.js
node --check serve-local.mjs
python3 -m unittest test_run
node --test test-panel.cjs
```

## Setup for a separately approved run

Keep this archive outside the tested checkout. In WSL, create a clean detached
checkout at the tested commit and install the project's existing backend and frontend
dependencies there. Use a Python 3.12+ environment with those backend dependencies,
Node, FFmpeg/ffprobe, and Azurite 3.35.0 already cached by npm. The expected
Windows browser is Edge. Nothing is installed by the launcher. Ports 5173 and
10000–10002 must be free. The app, API proxy, and emulator bind to loopback.

Set `TKT90_CHECKOUT`, optionally `TKT90_PYTHON` and `TKT90_WINDOWS_CURL`, and supply
`TKT90_TENANT`, `TKT90_API` and `TKT90_SPA` as the approved development app identifiers
in your shell. The tenant and API must match the checked-in frontend at that commit.
The API must issue v2 access tokens, and the SPA must already support the local
redirect. Do not grant new permissions during a run. Do not commit configuration,
tokens, accounts, setup codes, or artifacts. Only after approval, use
`bash launch.sh --one-approved-run` in the interactive WSL terminal.

1. Wait for the ready line. Verify the member account in regular Edge and register
   with the new Member code at localhost:5173. Keep the project ID local.
2. In a separate InPrivate session, verify the distinct nonmember account and use
   only the Nonmember code. **Both sessions must be registered before submission.**
3. In regular Edge, submit one synthetic job, retrieve and play the private MP4,
   download it, then use **Check video access** and select the downloaded file.
   The member should get 200 for status, metadata, and video, with matching bytes.
4. In the nonmember window, **Check video access** must return 403 for all three.
   Then close the test InPrivate windows and open a truly signed-out session;
   do not sign in or register there. **Check video access** must return three 401s.
5. Record only the fixed result lines. Leave the launcher running until the final
   report. Enter stops it early; setup codes and registrations are not reusable.
   No production deployment, paid providers, or emitted bearer tokens.
