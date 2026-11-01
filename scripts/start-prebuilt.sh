#!/bin/sh
# Compatibility entry point; deployment now uses the single compose.yaml.
set -eu
cd "$(dirname "$0")/.."
docker compose pull
docker compose up -d --wait --wait-timeout 240
