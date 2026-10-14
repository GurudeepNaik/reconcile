#!/bin/bash
# Deploy the live demo to Vercel (FastAPI function + dashboard served by it, Neon Postgres).
# Builds the dashboard into public/ first: Vercel bundles uploaded files into the Python
# function, not files generated during its own build step.
#   scripts/deploy-vercel.sh            # production
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

# A copy without .git: deploys from the CLI are then not tied to a commit author.
rsync -a --exclude .git --exclude node_modules --exclude .venv --exclude .pnpm-store \
  --exclude __pycache__ --exclude dist --exclude public --exclude '*.db' --exclude .env.local \
  "$ROOT/" "$STAGE/"
(cd "$STAGE/frontend" && npx -y pnpm@9.15.9 install --frozen-lockfile \
  && npx -y pnpm@9.15.9 exec vite build --outDir ../public --emptyOutDir)
cd "$STAGE" && npx -y vercel@64.0.2 deploy --prod --yes "$@"
