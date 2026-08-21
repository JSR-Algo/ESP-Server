#!/usr/bin/env bash
set -euo pipefail

bash -n deploy/deploy-vps.sh deploy/package-release.sh deploy/backup-db.sh deploy/server-only-remote.sh
python3 -m py_compile deploy/validate-env.py
python3 -m pytest deploy/tests/test_deploy_safety.py -q
