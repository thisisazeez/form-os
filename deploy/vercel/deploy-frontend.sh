#!/bin/sh
# Deploy the static frontend assets (Vite islands, widget, fonts, brand SVGs) to Vercel.
# Set the Vercel project's Root Directory to `frontend` before running this.
set -eu

cd "$(CDPATH= cd -- "$(dirname -- "$0")/../../frontend" && pwd)"
vercel --prod
