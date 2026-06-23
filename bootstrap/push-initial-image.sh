#!/usr/bin/env bash
# Build the application image from the local app repo and push it to ECR as
# :latest, so the platform stacks have a real, health-check-passing image to
# deploy against (ECS service + CodePipeline ECR source). Run ONCE, after the
# bootstrap stack creates the ECR repo. Your app CI later overwrites :latest.
#
# Usage:   ./bootstrap/push-initial-image.sh
# Override defaults via env, e.g.:  APP_DIR=/path/to/app REGION=eu-west-1 ./bootstrap/push-initial-image.sh
set -euo pipefail

PROJECT="${PROJECT:-todo-app}"
ENVIRONMENT="${ENVIRONMENT:-prod}"
REGION="${AWS_REGION:-eu-north-1}"
TAG="${TAG:-latest}"

# Local app repo (sibling of this infra repo by default), Dockerfile at its root.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${APP_DIR:-$SCRIPT_DIR/../../aws-rds-redis-app}"

REPO="${PROJECT}-${ENVIRONMENT}"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
REGISTRY="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"
TARGET="${REGISTRY}/${REPO}:${TAG}"

[ -f "$APP_DIR/Dockerfile" ] || { echo "No Dockerfile at $APP_DIR" >&2; exit 1; }

echo ">> Building $APP_DIR -> $TARGET"

# Authenticate Docker to your ECR registry
aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "$REGISTRY"

# Build for linux/amd64 (Fargate's default) so the image runs even when built
# on an Apple Silicon / arm64 host. The Dockerfile is multi-stage (it compiles
# the Spring Boot jar itself), so the build context is just the repo root.
docker build --platform linux/amd64 -t "$TARGET" "$APP_DIR"
docker push "$TARGET"

echo ">> Done. $TARGET is now in ECR."
