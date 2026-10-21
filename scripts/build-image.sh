#!/usr/bin/env bash
set -euo pipefail
if [ "$#" -ne 2 ]; then
  echo 'Usage: scripts/build-image.sh RELEASE_ID IMAGE_TAG' >&2
  exit 2
fi
cd "$(dirname "$0")/.."
build_inputs=(Dockerfile .dockerignore pyproject.toml uv.lock src migrations frontend/package.json frontend/package-lock.json frontend/index.html frontend/tsconfig.json frontend/vite.config.ts frontend/src scripts/build-image.sh)
if [ -n "$(git status --porcelain -- "${build_inputs[@]}")" ]; then
  echo 'Build refused: commit all source/build inputs before labeling a release image.' >&2
  exit 2
fi
npm ci --prefix frontend
npm run build --prefix frontend
docker build --build-arg HTTP_PROXY --build-arg HTTPS_PROXY \
  --build-arg "RELEASE_ID=$1" --build-arg "SOURCE_REVISION=$(git rev-parse HEAD)" -t "$2" .
