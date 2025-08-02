#!/bin/bash

# Docker Hub Deployment Script for AgentRun
# This script builds, tags, and pushes Docker images to Docker Hub with git SHA tracking

set -e  # Exit on any error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if Docker Hub username is provided
if [ -z "$1" ]; then
    print_error "Usage: $0 <DOCKERHUB_USERNAME> [--no-push]"
    print_info "Example: $0 myusername"
    print_info "Use --no-push flag to build and tag without pushing to Docker Hub"
    exit 1
fi

DOCKERHUB_USERNAME="$1"
NO_PUSH=false

# Check for --no-push flag
if [ "$2" = "--no-push" ]; then
    NO_PUSH=true
    print_warning "Running in no-push mode - images will be built and tagged but not pushed"
fi

# Get git information
GIT_SHA=$(git rev-parse HEAD)
GIT_SHORT_SHA=$(git rev-parse --short HEAD)
GIT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
GIT_TAG=$(git describe --tags --exact-match 2>/dev/null || echo "")

print_info "Git Information:"
echo "  Branch: $GIT_BRANCH"
echo "  SHA: $GIT_SHA"
echo "  Short SHA: $GIT_SHORT_SHA"
if [ -n "$GIT_TAG" ]; then
    echo "  Tag: $GIT_TAG"
fi

# Check if working directory is clean
if ! git diff-index --quiet HEAD --; then
    print_warning "Working directory has uncommitted changes!"
    read -p "Continue anyway? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        print_error "Aborted by user"
        exit 1
    fi
fi

# Ensure we're in the right directory
if [ ! -f "agentrun_plus/docker-compose.yml" ]; then
    print_error "docker-compose.yml not found in agentrun_plus/. Please run from project root."
    exit 1
fi

cd agentrun_plus

print_info "Cleaning up old images and containers..."
# Clean build - remove existing images and build cache
docker compose down --remove-orphans 2>/dev/null || true
docker system prune -f

print_info "Building images with clean cache..."
# Build with no cache to ensure fresh builds
docker compose build --no-cache --pull

# Define image names
API_IMAGE="agentrun_plus-api"
RUNNER_IMAGE="agentrun_plus-python_runner"

# Define tags
LATEST_TAG="latest"
SHA_TAG="sha-$GIT_SHORT_SHA"
BRANCH_TAG="branch-$GIT_BRANCH"

# If there's a git tag, use it
if [ -n "$GIT_TAG" ]; then
    VERSION_TAG="$GIT_TAG"
fi

print_info "Tagging images..."

# Tag API image
docker tag $API_IMAGE:latest $DOCKERHUB_USERNAME/agentrun-api:$LATEST_TAG
docker tag $API_IMAGE:latest $DOCKERHUB_USERNAME/agentrun-api:$SHA_TAG
docker tag $API_IMAGE:latest $DOCKERHUB_USERNAME/agentrun-api:$BRANCH_TAG

if [ -n "$GIT_TAG" ]; then
    docker tag $API_IMAGE:latest $DOCKERHUB_USERNAME/agentrun-api:$VERSION_TAG
    print_success "Tagged API image with version: $VERSION_TAG"
fi

# Tag Python Runner image
docker tag $RUNNER_IMAGE:latest $DOCKERHUB_USERNAME/agentrun-python-runner:$LATEST_TAG
docker tag $RUNNER_IMAGE:latest $DOCKERHUB_USERNAME/agentrun-python-runner:$SHA_TAG
docker tag $RUNNER_IMAGE:latest $DOCKERHUB_USERNAME/agentrun-python-runner:$BRANCH_TAG

if [ -n "$GIT_TAG" ]; then
    docker tag $RUNNER_IMAGE:latest $DOCKERHUB_USERNAME/agentrun-python-runner:$VERSION_TAG
    print_success "Tagged Python Runner image with version: $VERSION_TAG"
fi

print_success "All images tagged successfully!"

# Show tagged images
print_info "Tagged images:"
docker images | grep "$DOCKERHUB_USERNAME/agentrun"

if [ "$NO_PUSH" = true ]; then
    print_warning "Skipping push to Docker Hub (--no-push flag used)"
    print_info "To push manually, run:"
    echo "  docker push $DOCKERHUB_USERNAME/agentrun-api:$LATEST_TAG"
    echo "  docker push $DOCKERHUB_USERNAME/agentrun-api:$SHA_TAG"
    echo "  docker push $DOCKERHUB_USERNAME/agentrun-api:$BRANCH_TAG"
    if [ -n "$GIT_TAG" ]; then
        echo "  docker push $DOCKERHUB_USERNAME/agentrun-api:$VERSION_TAG"
    fi
    echo "  docker push $DOCKERHUB_USERNAME/agentrun-python-runner:$LATEST_TAG"
    echo "  docker push $DOCKERHUB_USERNAME/agentrun-python-runner:$SHA_TAG"
    echo "  docker push $DOCKERHUB_USERNAME/agentrun-python-runner:$BRANCH_TAG"
    if [ -n "$GIT_TAG" ]; then
        echo "  docker push $DOCKERHUB_USERNAME/agentrun-python-runner:$VERSION_TAG"
    fi
    exit 0
fi

# Check if user is logged into Docker Hub
if ! docker info | grep -q "Username"; then
    print_warning "Not logged into Docker Hub. Attempting login..."
    docker login
fi

print_info "Pushing images to Docker Hub..."

# Push API images
print_info "Pushing agentrun-api images..."
docker push $DOCKERHUB_USERNAME/agentrun-api:$LATEST_TAG
docker push $DOCKERHUB_USERNAME/agentrun-api:$SHA_TAG
docker push $DOCKERHUB_USERNAME/agentrun-api:$BRANCH_TAG

if [ -n "$GIT_TAG" ]; then
    docker push $DOCKERHUB_USERNAME/agentrun-api:$VERSION_TAG
fi

# Push Python Runner images
print_info "Pushing agentrun-python-runner images..."
docker push $DOCKERHUB_USERNAME/agentrun-python-runner:$LATEST_TAG
docker push $DOCKERHUB_USERNAME/agentrun-python-runner:$SHA_TAG
docker push $DOCKERHUB_USERNAME/agentrun-python-runner:$BRANCH_TAG

if [ -n "$GIT_TAG" ]; then
    docker push $DOCKERHUB_USERNAME/agentrun-python-runner:$VERSION_TAG
fi

print_success "All images pushed successfully!"

print_info "Deployment Summary:"
echo "  Docker Hub Username: $DOCKERHUB_USERNAME"
echo "  Git SHA: $GIT_SHORT_SHA"
echo "  Branch: $GIT_BRANCH"
if [ -n "$GIT_TAG" ]; then
    echo "  Version Tag: $GIT_TAG"
fi
echo ""
echo "Images available at:"
echo "  $DOCKERHUB_USERNAME/agentrun-api:latest"
echo "  $DOCKERHUB_USERNAME/agentrun-api:$SHA_TAG"
echo "  $DOCKERHUB_USERNAME/agentrun-api:$BRANCH_TAG"
if [ -n "$GIT_TAG" ]; then
    echo "  $DOCKERHUB_USERNAME/agentrun-api:$VERSION_TAG"
fi
echo "  $DOCKERHUB_USERNAME/agentrun-python-runner:latest"
echo "  $DOCKERHUB_USERNAME/agentrun-python-runner:$SHA_TAG"
echo "  $DOCKERHUB_USERNAME/agentrun-python-runner:$BRANCH_TAG"
if [ -n "$GIT_TAG" ]; then
    echo "  $DOCKERHUB_USERNAME/agentrun-python-runner:$VERSION_TAG"
fi

print_success "Deployment completed successfully!"
