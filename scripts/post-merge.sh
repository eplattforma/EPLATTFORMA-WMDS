#!/bin/bash
set -e

if command -v uv >/dev/null 2>&1; then
    uv sync --frozen 2>/dev/null || uv sync
fi

echo "post-merge: dependencies synced; schema migrations run on app startup"
