#!/bin/sh
# Build the React islands, widget bundle, fonts and brand SVGs once.
# Shared by the Vercel API build, the Render API build and the standalone static deploys.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
cd "$ROOT/frontend"

if [ -d node_modules ]; then
  npm run build
else
  npm ci
  npm run build
fi
