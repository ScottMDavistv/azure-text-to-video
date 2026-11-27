#!/usr/bin/env python3
"""Offline fail-closed check for the hard-disabled solo preview transition."""
import hashlib
from pathlib import Path
import sys
import check_ci_isolation as policy


def main():
    try:
        import yaml
        root = Path(__file__).resolve().parents[1]
        for path, digest in ((policy.GUARDED_AZURE, policy.PREVIEW_CALLER_BYTES),
                             (policy.PREVIEW_CALLEE, policy.PREVIEW_CALLEE_BYTES)):
            raw = (root / path).read_bytes()
            if hashlib.sha256(raw).hexdigest() != digest:
                raise ValueError('byte drift')
            doc = policy.strict_load(yaml, raw.decode())
            if any(job.get('if') != '${{ false }}' or 'id-token' in job.get('permissions', {})
                   for job in doc['jobs'].values()):
                raise ValueError('not disabled')
            if b'az deployment group create' in raw or b'infra/main.bicep' in raw:
                raise ValueError('apply or broad template')
        legacy = (root / '.github/workflows/deploy-infrastructure.yml').read_text()
        if 'az deployment' in legacy or 'exit 1' not in legacy:
            raise ValueError('legacy route not denied')
        if policy.scan(root):
            raise ValueError('isolation check')
    except Exception:
        print('Azure preview governance refused.', file=sys.stderr)
        return 1
    print('Hard-disabled preview governance passed (offline; no activation proof).')
    return 0


if __name__ == '__main__':
    sys.exit(main())
