.DEFAULT_GOAL := help

.PHONY: help test smoke install-ui build-ui

PYTHON := $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
FRONTEND_DIR := agentflow/web/frontend

help:
	@printf '%s\n' \
	  'Available targets:' \
	  '  test       Run the test suite' \
	  '  smoke      Run the default smoke pipeline' \
	  '  install-ui Install frontend dependencies' \
	  '  build-ui   Build the frontend dashboard'

test:
	$(PYTHON) -m pytest -q

smoke:
	$(PYTHON) -m agentflow run examples/airflow_like.py --output summary

install-ui:
	cd $(FRONTEND_DIR) && npm install

build-ui:
	cd $(FRONTEND_DIR) && npm run build
