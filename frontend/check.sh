#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

npm run lint
npm run typecheck
npm run test
npm run build
