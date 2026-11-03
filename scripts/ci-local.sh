#!/usr/bin/env bash
# Linux CI steps for a machine without GitHub Actions (this is what we can run here).
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"

python3 -m py_compile start host/*.py
python3 tests/run.py
./start doctor
./start verify-release
./start --help >/dev/null
bash untether/scripts/verify-release.sh
(
  cd host/cmd/usbscsi
  go build -trimpath -o /tmp/usbscsi-ci .
)
echo "ci-local: ok"
