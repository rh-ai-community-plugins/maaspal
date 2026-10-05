.DEFAULT_GOAL := help

# Container image settings
REGISTRY       ?= quay.io/rh-ai-community-plugins
FRONTEND_IMAGE ?= maaspal
BFF_IMAGE      ?= maaspal-bff
CHART_NAME     ?= maaspal-chart  # used by help display only; chart-package/chart-push use chart/ directly
VERSION        ?=
BUILDER        ?= podman
IMAGE_TAG      ?= latest
SEVERITY       ?= HIGH,CRITICAL
NAMESPACE      ?= cp-maaspal
PYTHON         ?= python3

# ──────────────────────────────────────────────
# Install
# ──────────────────────────────────────────────

.PHONY: install install-frontend install-bff

install: install-frontend install-bff ## Install dependencies (frontend + BFF)

install-frontend:
	npm ci

install-bff: ## Install the Python BFF + harness with dev tools (use a virtualenv)
	cd bff && $(PYTHON) -m pip install -e ".[dev]"

# ──────────────────────────────────────────────
# Lint
# ──────────────────────────────────────────────

.PHONY: lint lint-frontend lint-bff lint-chart

lint: lint-frontend lint-bff lint-chart ## Lint source code (frontend + BFF + chart)

lint-frontend:
	npm run lint

lint-bff:
	cd bff && $(PYTHON) -m ruff check harness api

lint-chart:
	helm lint chart/

# ──────────────────────────────────────────────
# Typecheck
# ──────────────────────────────────────────────

.PHONY: typecheck typecheck-frontend typecheck-bff

typecheck: typecheck-frontend typecheck-bff ## Type checking (tsc + mypy)

typecheck-frontend:
	npm run typecheck

typecheck-bff:
	cd bff && $(PYTHON) -m mypy harness api

# ──────────────────────────────────────────────
# Test
# ──────────────────────────────────────────────

.PHONY: test test-frontend test-bff test-coverage

test: test-frontend test-bff ## Run tests (Jest + pytest)

test-frontend:
	npm test

test-bff:
	cd bff && $(PYTHON) -m pytest

test-coverage: ## Run frontend tests with coverage report
	npm run test:coverage

# ──────────────────────────────────────────────
# Validate (typecheck + lint + test)
# ──────────────────────────────────────────────

.PHONY: validate validate-frontend validate-bff

validate: validate-frontend validate-bff lint-chart ## Full validation: typecheck + lint + test (all)

validate-frontend: typecheck-frontend lint-frontend test-frontend

validate-bff: typecheck-bff lint-bff test-bff

# ──────────────────────────────────────────────
# Build
# ──────────────────────────────────────────────

.PHONY: build

build: ## Production frontend build (dist/remoteEntry.js)
	npm run build

# ──────────────────────────────────────────────
# Dev servers
# ──────────────────────────────────────────────

.PHONY: dev dev-standalone dev-bff

dev: ## Start frontend dev server (port 9500), proxying to a local dashboard on 8443
	npm run start:dev

dev-standalone: ## Start frontend dev server with no dashboard (open http://localhost:9500/maaspal)
	STANDALONE=true npm run start:dev

dev-bff: ## Start the FastAPI BFF on port 3000 with the access check off (local only)
	cd bff && MAASPAL_AUTH_MODE=off DATA_DIR=$${DATA_DIR:-/tmp/maaspal-data} DB_PATH=$${DB_PATH:-/tmp/maaspal-data/maaspal.db} \
		$(PYTHON) -m uvicorn api.main:app --reload --port 3000

# ──────────────────────────────────────────────
# Container images
# ──────────────────────────────────────────────

.PHONY: image-build image-build-frontend image-build-bff
.PHONY: image-push image-push-frontend image-push-bff image-scan

image-build: image-build-frontend image-build-bff ## Build container images

image-build-frontend:
	$(BUILDER) build --platform linux/amd64 -t $(REGISTRY)/$(FRONTEND_IMAGE):$(IMAGE_TAG) -f Containerfile .

image-build-bff:
	$(BUILDER) build --platform linux/amd64 -t $(REGISTRY)/$(BFF_IMAGE):$(IMAGE_TAG) -f bff/Containerfile bff/

image-push: ## Build and push container images (frontend + BFF)
	REGISTRY=$(REGISTRY) ./scripts/build-push.sh all $(VERSION)

image-push-frontend: ## Build and push frontend container image only
	REGISTRY=$(REGISTRY) ./scripts/build-push.sh frontend $(VERSION)

image-push-bff: ## Build and push BFF container image only
	REGISTRY=$(REGISTRY) ./scripts/build-push.sh bff $(VERSION)

image-scan: ## Build and scan images for vulnerabilities
	BUILDER=$(BUILDER) IMAGE_TAG=$(IMAGE_TAG) ./scripts/scan-image.sh all $(SEVERITY)

# ──────────────────────────────────────────────
# Helm chart
# ──────────────────────────────────────────────

.PHONY: chart-package chart-push deploy

chart-package: ## Package Helm chart into a .tgz archive
	helm package chart/

chart-push: ## Package and push Helm chart to OCI registry (requires Helm 3.8+)
	$(eval CHART_TGZ := $(shell helm package chart/ | awk '{print $$NF}'))
	helm push $(CHART_TGZ) oci://$(REGISTRY)
	@rm -f $(CHART_TGZ)

# Development deploy: pull policy Always plus a rollout restart, so re-pushing
# the same tag (e.g. 0.1.0-dev) actually reaches the running pods. Harness Jobs
# always pull fresh (api/k8s.py). Released installs keep the chart's
# IfNotPresent with versioned tags.
deploy: ## helm upgrade --install from chart/ into NAMESPACE (images from REGISTRY, tag IMAGE_TAG), then restart the pods
	helm upgrade --install maaspal chart/ -n $(NAMESPACE) --create-namespace \
		--set image.repository=$(REGISTRY)/$(FRONTEND_IMAGE) --set image.tag=$(IMAGE_TAG) \
		--set bff.image.repository=$(REGISTRY)/$(BFF_IMAGE) --set bff.image.tag=$(IMAGE_TAG) \
		--set image.pullPolicy=Always --set bff.image.pullPolicy=Always \
		--set namespace=$(NAMESPACE)
	oc label namespace $(NAMESPACE) maas.opendatahub.io/gateway-access=true --overwrite
	oc rollout restart deployment/maaspal deployment/maaspal-bff -n $(NAMESPACE)
	oc rollout status deployment/maaspal -n $(NAMESPACE) --timeout=300s
	oc rollout status deployment/maaspal-bff -n $(NAMESPACE) --timeout=300s

# ──────────────────────────────────────────────
# Clean
# ──────────────────────────────────────────────

.PHONY: clean

clean: ## Remove build artifacts and caches
	rm -rf dist/ coverage/
	find bff -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf bff/.mypy_cache bff/.ruff_cache bff/.pytest_cache

# ──────────────────────────────────────────────
# Help
# ──────────────────────────────────────────────

.PHONY: help

help: ## Show this help
	@printf "\n\033[1mTargets:\033[0m\n"
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'
	@printf "\n\033[1mVariables:\033[0m\n"
	@printf "  \033[33m%-20s\033[0m %s (default: %s)\n" "REGISTRY"       "Container image registry"             "$(REGISTRY)"
	@printf "  \033[33m%-20s\033[0m %s (default: %s)\n" "FRONTEND_IMAGE" "Frontend image name"                  "$(FRONTEND_IMAGE)"
	@printf "  \033[33m%-20s\033[0m %s (default: %s)\n" "BFF_IMAGE"      "BFF image name"                       "$(BFF_IMAGE)"
	@printf "  \033[33m%-20s\033[0m %s (default: %s)\n" "CHART_NAME"     "Helm chart name"                      "$(CHART_NAME)"
	@printf "  \033[33m%-20s\033[0m %s (default: %s)\n" "VERSION"        "Release version for image-push"       "auto-computed from git tags"
	@printf "  \033[33m%-20s\033[0m %s (default: %s)\n" "BUILDER"        "Container build tool"                 "$(BUILDER)"
	@printf "  \033[33m%-20s\033[0m %s (default: %s)\n" "IMAGE_TAG"      "Tag for image-build / image-scan / deploy" "$(IMAGE_TAG)"
	@printf "  \033[33m%-20s\033[0m %s (default: %s)\n" "NAMESPACE"      "Namespace for deploy"                 "$(NAMESPACE)"
	@printf "  \033[33m%-20s\033[0m %s (default: %s)\n" "PYTHON"         "Python with the BFF's deps installed" "$(PYTHON)"
	@printf "\n\033[1mExamples:\033[0m\n"
	@printf "  make validate                          # typecheck + lint + test (all)\n"
	@printf "  make image-build                       # build both container images\n"
	@printf "  make image-push VERSION=0.2.0          # build and push with explicit version\n"
	@printf "  make deploy REGISTRY=quay.io/me IMAGE_TAG=dev  # install from a personal registry\n"
	@printf "  make chart-push                        # package and push Helm chart to OCI registry\n"
	@printf "\n"
