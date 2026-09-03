#!/usr/bin/env bash
# Build and optionally push the sandbox-runner Docker image.
#
# Usage:
#   ./scripts/build-sandbox-runner.sh [--push] [--tag TAG]
#
# Environment:
#   SANDBOX_RUNNER_IMAGE  Default image name (default: gameforge/sandbox-runner:latest)

set -euo pipefail

IMAGE="${SANDBOX_RUNNER_IMAGE:-gameforge/sandbox-runner:latest}"
TAG="latest"
PUSH=false

for arg in "$@"; do
  case "$arg" in
    --push) PUSH=true ;;
    --tag=*) TAG="${arg#*=}" ;;
    --tag) TAG="$2"; shift ;;
  esac
done

IMAGE_WITH_TAG="${IMAGE%:*}:$TAG"

echo "Building $IMAGE_WITH_TAG ..."
docker build -t "$IMAGE_WITH_TAG" -f docker/sandbox-runner/Dockerfile .

if [ "$PUSH" = true ]; then
  echo "Pushing $IMAGE_WITH_TAG ..."
  docker push "$IMAGE_WITH_TAG"
fi

echo "Done."
