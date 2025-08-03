#!/bin/bash

# Docker Compose Helper Script for AgentRun
# Simplifies running different environment configurations

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

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

print_usage() {
    echo "Usage: $0 <environment> [action] [options]"
    echo ""
    echo "Environments:"
    echo "  dev       - Development (builds images locally)"
    echo "  prod      - Production (uses Docker Hub images)"
    echo "  test-dev  - Development + Testing (exposes python_runner)"
    echo "  test-prod - Production + Testing (exposes python_runner)"
    echo ""
    echo "Actions:"
    echo "  up        - Start services (default)"
    echo "  down      - Stop services"
    echo "  build     - Build images (dev only)"
    echo "  logs      - Show logs"
    echo "  ps        - Show running containers"
    echo ""
    echo "Options:"
    echo "  -d        - Run in detached mode"
    echo "  --pull    - Pull latest images (prod only)"
    echo ""
    echo "Examples:"
    echo "  $0 dev                    # Start development environment"
    echo "  $0 prod                   # Start production environment"
    echo "  $0 test-dev               # Start development with testing enabled"
    echo "  $0 dev down               # Stop development environment"
    echo "  $0 prod up -d             # Start production in background"
    echo "  $0 dev build              # Build development images"
}

if [ -z "$1" ]; then
    print_error "Environment not specified"
    print_usage
    exit 1
fi

ENVIRONMENT="$1"
ACTION="${2:-up}"
shift 2 || true  # Remove first two arguments, ignore error if less than 2 args
OPTIONS="$@"

# Validate environment
case $ENVIRONMENT in
    dev)
        COMPOSE_FILES="-f docker-compose.base.yml -f docker-compose.dev.yml"
        ;;
    prod)
        COMPOSE_FILES="-f docker-compose.base.yml -f docker-compose.prod.yml --env-file .env.prod"
        if [ ! -f ".env.prod" ]; then
            print_warning ".env.prod file not found. Using default values."
            print_info "Create .env.prod and set DOCKERHUB_USERNAME variable"
            COMPOSE_FILES="-f docker-compose.base.yml -f docker-compose.prod.yml"
        fi
        ;;
    test-dev)
        COMPOSE_FILES="-f docker-compose.base.yml -f docker-compose.dev.yml -f docker-compose.test.yml"
        ;;
    test-prod)
        COMPOSE_FILES="-f docker-compose.base.yml -f docker-compose.prod.yml -f docker-compose.test.yml --env-file .env.prod"
        if [ ! -f ".env.prod" ]; then
            print_warning ".env.prod file not found. Using default values."
            COMPOSE_FILES="-f docker-compose.base.yml -f docker-compose.prod.yml -f docker-compose.test.yml"
        fi
        ;;
    *)
        print_error "Invalid environment: $ENVIRONMENT"
        print_usage
        exit 1
        ;;
esac

# Validate action for specific environments
if [ "$ACTION" = "build" ] && [[ "$ENVIRONMENT" =~ prod ]]; then
    print_error "Build action not available for production environment (uses pre-built images)"
    exit 1
fi

# Execute docker compose command
print_info "Running: docker compose $COMPOSE_FILES $ACTION $OPTIONS"
print_info "Environment: $ENVIRONMENT"

if [[ "$ENVIRONMENT" =~ prod ]] && [ -f ".env.prod" ]; then
    print_info "Using .env.prod for production environment variables"
fi

if ! [ -z ${DOCKER_COMPOSE_CMD} ]; then
    ${DOCKER_COMPOSE_CMD} $COMPOSE_FILES $ACTION $OPTIONS
else
    docker compose $COMPOSE_FILES $ACTION $OPTIONS
fi

if [ $? -eq 0 ]; then
    print_success "Command completed successfully"
else
    print_error "Command failed"
    exit 1
fi
