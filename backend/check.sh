#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

echo "==> ruff check"
uv run ruff check .

echo "==> mypy"
uv run mypy app

echo "==> pytest"
uv run pytest
