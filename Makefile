PYTHON ?= $(shell command -v python3.12 || command -v python3)
INFRA  := infra
export PYTHONPATH := packages/eval_common:services/dispatcher:services/aggregator:services/worker

.PHONY: help install test lint eval-local docker-build synth deploy bootstrap fmt

help:
	@grep -E '^[a-zA-Z_-]+:.*?##' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-16s %s\n", $$1, $$2}'

install: ## Install Python package and dev extras
	$(PYTHON) -m pip install -e ".[dev]"

test: ## Run unit + moto integration tests
	$(PYTHON) -m pytest tests -q

lint: ## Ruff + mypy
	$(PYTHON) -m ruff check packages services tests
	$(PYTHON) -m ruff format --check packages services tests
	$(PYTHON) -m mypy packages/eval_common/eval_common

fmt: ## Auto-format
	$(PYTHON) -m ruff check --fix packages services tests
	$(PYTHON) -m ruff format packages services tests

eval-local: ## Score the sample golden set with the heuristic backend
	$(PYTHON) -m eval_common.local_eval datasets/golden_dataset_sample.json

docker-build: ## Build the Fargate worker image
	docker build -f services/worker/Dockerfile -t aqg-evaluator:local \
		--build-arg INSTALL_EVAL_LIBS=$(or $(INSTALL_EVAL_LIBS),false) .

synth: ## CDK synth
	cd $(INFRA) && npm ci && npx cdk synth

deploy: ## CDK deploy (requires AWS credentials)
	cd $(INFRA) && npm ci && npx cdk deploy --all --require-approval never

bootstrap: ## CDK bootstrap the target account/region
	cd $(INFRA) && npx cdk bootstrap
