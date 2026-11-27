#!/usr/bin/env python3
"""Offline solo-operator admission candidate; unresolved identity fails closed.

GitHub Environment must independently require Scott, prevent self-review and
admin bypass, and restrict main. This program cannot establish those settings.
"""
import datetime as dt
import json
import os
import re
import sys

APP_ID = 0
BOT_ID = 0
BOT_LOGIN = '__UNRESOLVED_APPLICATION_PREVIEW_BOT__'
OWNER_ID = 212811615
REPO_ID = 1457187422
REPO = 'ScottMDavistv/azure-text-to-video'
WORKFLOW = '.github/workflows/guarded-azure-worker-deploy.yml'
TARGET = 'rg-textvideo-dev/job-textvideo-worker-dev'
DIGEST = 'sha256:219ebbe03d9b1356a4091912b4955c1987ba8a0131a4e084953ab3796b5f0646'
TTL_SECONDS = 3600
MAX_BYTES = 65536


def require(ok):
    if not ok:
        raise ValueError('admission refused')


def unique_pairs(pairs):
    result = {}
    for k, v in pairs:
        require(k not in result)
        result[k] = v
    return result


def deadline(run, env, now, *, identity):
    # identity is a trusted code pin; only synthetic offline tests supply it.
    # No runtime flag, input, environment variable or API field releases pins.
    app_id, bot_id, login = identity
    require(type(app_id) is int and app_id > 0 and type(bot_id) is int
            and bot_id > 0 and bot_id != OWNER_ID and type(login) is str
            and re.fullmatch(r'[A-Za-z0-9-]+\[bot\]', login) is not None)
    require(type(run) is dict and type(env) is dict)
    sha, run_id = env.get('GITHUB_SHA'), env.get('GITHUB_RUN_ID')
    require(type(sha) is str and re.fullmatch('[0-9a-f]{40}', sha) is not None
            and sha != '0' * 40 and type(run_id) is str
            and re.fullmatch('[1-9][0-9]{0,24}', run_id) is not None)
    expected_env = {
        'GITHUB_REPOSITORY': REPO, 'GITHUB_REPOSITORY_ID': str(REPO_ID),
        'GITHUB_REPOSITORY_OWNER_ID': str(OWNER_ID),
        'GITHUB_WORKFLOW_REF': REPO + '/' + WORKFLOW + '@refs/heads/main',
        'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_REF': 'refs/heads/main',
        'GITHUB_ACTOR': login, 'GITHUB_ACTOR_ID': str(bot_id),
        'GITHUB_TRIGGERING_ACTOR': login, 'GITHUB_RUN_ATTEMPT': '1',
        'APPROVED_SHA': sha, 'CONFIRMATION': 'PREVIEW-DEV-WORKER',
        'APPROVED_TARGET': TARGET, 'APPROVED_DIGEST': DIGEST,
        'OPERATION': 'whatif',
    }
    require(all(type(env.get(k)) is str and env[k] == v for k, v in expected_env.items()))
    expected_run = {'id': int(run_id), 'run_attempt': 1, 'event': 'workflow_dispatch',
                    'head_branch': 'main', 'head_sha': sha, 'path': WORKFLOW}
    require(all(type(run.get(k)) is type(v) and run[k] == v for k, v in expected_run.items()))
    for field in ('actor', 'triggering_actor'):
        obj = run.get(field)
        require(type(obj) is dict and obj.get('login') == login
                and type(obj.get('id')) is int and obj['id'] == bot_id
                and obj.get('type') == 'Bot')
    for field in ('repository', 'head_repository'):
        obj = run.get(field)
        require(type(obj) is dict and obj.get('full_name') == REPO
                and type(obj.get('id')) is int and obj['id'] == REPO_ID)
        owner = obj.get('owner')
        require(type(owner) is dict and owner.get('login') == 'ScottMDavistv'
                and type(owner.get('id')) is int and owner['id'] == OWNER_ID)
    # GitHub run actor metadata does not independently attest an App ID/install.
    # External review must bind the pinned bot to the dedicated App and sole repo.
    created = dt.datetime.strptime(run['created_at'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=dt.timezone.utc)
    require(0 <= (now - created).total_seconds() < TTL_SECONDS)
    return int(created.timestamp()) + TTL_SECONDS


def main():
    try:
        # Refuse before reading any input until separately reviewed code pins exist.
        require(APP_ID > 0 and BOT_ID > 0 and BOT_LOGIN != '__UNRESOLVED_APPLICATION_PREVIEW_BOT__')
        require(sys.argv[1:] in ([], ['--check-pins']))
        if sys.argv[1:] == ['--check-pins']:
            # This is not run admission or approval evidence.
            print('IDENTITY_PINS_CONFIGURED')
            return 0
        raw = sys.stdin.buffer.read(MAX_BYTES + 1)
        require(0 < len(raw) <= MAX_BYTES)
        run = json.loads(raw, object_pairs_hook=unique_pairs)
        expires = deadline(run, dict(os.environ), dt.datetime.now(dt.timezone.utc),
                           identity=(APP_ID, BOT_ID, BOT_LOGIN))
        print(expires)  # Only bounded validated deadline; no raw metadata.
        return 0
    except Exception:
        print('Solo preview admission refused.', file=sys.stderr)
        return 78


if __name__ == '__main__':
    sys.exit(main())
