COMPOSE ?= docker compose
RUN     := $(COMPOSE) run --rm app

.PHONY: setup build test shell chat eval regrade improve doctor demo-offline clean

setup: .env build          ## first-time setup: create .env, build image
	@echo "Setup done. Next: choose a model setup in .env (see README section 1), then: make doctor"

.env:
	cp .env.example .env

build:
	$(COMPOSE) build

test:                      ## deterministic unit tests (no LLM)
	$(RUN) pytest

shell:                     ## shell inside the container
	$(RUN) bash

doctor:                    ## show model per role, check keys (never printed) and connectivity
	$(RUN) python -m llm.doctor $(ARGS)

chat:                      ## talk to the agent (optional: make chat V=0 to pick a playbook version)
	$(RUN) python -m agent.chat $(V)

eval:                      ## run the scenario suite (RUN_ID=<id> resumes; ARGS="--only x --k 3" for options)
	$(RUN) python -m evals.harness $(if $(RUN_ID),--run-id $(RUN_ID),) $(ARGS)

regrade:                   ## re-apply code-side grading to a finished run from its traces (no model calls)
	$(RUN) python -m evals.regrade $(RUN_ID)

improve:                   ## eval -> triage -> 3 patches -> lint -> targeted -> full gate -> v(N+1) + report
	$(RUN) python -m evals.improve.loop $(if $(IMPROVE_ID),--improve-id $(IMPROVE_ID),) $(if $(BASELINE),--baseline-run $(BASELINE),) $(ARGS)

demo-offline:              ## improvement-loop machinery with deterministic fake models (no key, no network)
	$(RUN) python scripts/offline_demo.py

clean:
	rm -rf .pytest_cache runs/cache.sqlite
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
