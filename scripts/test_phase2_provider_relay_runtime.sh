#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE_IMAGE="gcr.io/distroless/python3-debian12@sha256:7d1042ce588ab97019fe95c24ffca7bc5a82ccdac572511d5e09bda4435c89c5"
RELAY_IMAGE="tickflow-phase2-provider-relay:runtime-v1"
PROXY_IMAGE="tickflow-phase2-egress-proxy:runtime-v1"
MOCK_IMAGE="tickflow-phase2-mock-provider:runtime-v1"
PYTHON="$ROOT/backend/.venv/bin/python"

cd "$ROOT"
docker version >/dev/null
docker image inspect "$BASE_IMAGE" >/dev/null

docker build --network none --pull=false --tag "$RELAY_IMAGE" \
  docker/phase2-provider-relay
docker build --network none --pull=false --tag "$PROXY_IMAGE" \
  docker/phase2-egress-proxy
docker build --network none --pull=false --tag "$MOCK_IMAGE" \
  --file docker/phase2-mock-provider/Dockerfile docker

PYTHONPATH="$ROOT/backend" "$PYTHON" \
  -m scripts.run_phase2_mock_provider_relay \
  --repo-root "$ROOT" \
  --reports-root "$ROOT/reports"

PYTHONPATH="$ROOT/backend" "$PYTHON" \
  -m scripts.validate_phase2_provider_relay \
  --artifact-root "$ROOT/reports/phase2_provider_relay"
