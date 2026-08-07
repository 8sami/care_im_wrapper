.PHONY: clean clean-build clean-pyc clean-test coverage coverage-html dist docs docs-open help install lint lint-fix schema test test-one typecheck
.DEFAULT_GOAL := help

define BROWSER_PYSCRIPT
import os, webbrowser, sys

from urllib.request import pathname2url

webbrowser.open("file://" + pathname2url(os.path.abspath(sys.argv[1])))
endef
export BROWSER_PYSCRIPT

define PRINT_HELP_PYSCRIPT
import re, sys

for line in sys.stdin:
	match = re.match(r'^([a-zA-Z_-]+):.*?## (.*)$$', line)
	if match:
		target, help = match.groups()
		print("%-20s %s" % (target, help))
endef
export PRINT_HELP_PYSCRIPT

BROWSER := python -c "$$BROWSER_PYSCRIPT"

help:
	@python -c "$$PRINT_HELP_PYSCRIPT" < $(MAKEFILE_LIST)

clean: clean-build clean-pyc clean-test ## remove all build, test, coverage and Python artifacts

clean-build: ## remove build artifacts
	rm -fr build/
	rm -fr dist/
	rm -fr .eggs/
	find . -name '*.egg-info' -exec rm -fr {} +
	find . -name '*.egg' -exec rm -f {} +

clean-pyc: ## remove Python file artifacts
	find . -name '*.pyc' -exec rm -f {} +
	find . -name '*.pyo' -exec rm -f {} +
	find . -name '*~' -exec rm -f {} +
	find . -name '__pycache__' -exec rm -fr {} +

clean-test: ## remove test and coverage artifacts
	rm -fr .tox/
	rm -f .coverage
	rm -fr htmlcov/
	rm -fr .pytest_cache

# The plugin imports care, so tests and docs run inside the backend container, which has
# CARE and its dependencies installed. Run these from the care repo root's docker stack.
DOCKER_RUN := docker compose -f ../docker-compose.yaml exec -T
BACKEND    := $(DOCKER_RUN) -w /app/care_im_wrapper backend

lint: ## check style with ruff
	.venv/bin/ruff check .
	.venv/bin/ruff format --check .

lint-fix: ## auto-fix style with ruff
	.venv/bin/ruff check --fix .
	.venv/bin/ruff format .

typecheck: ## run basedpyright
	.venv/bin/basedpyright

test: ## run the test suite in the backend container
	$(BACKEND) python /app/manage.py test tests --top-level-directory /app/care_im_wrapper

test-one: ## run one module, e.g. make test-one T=tests.test_api_notifications
	$(BACKEND) python /app/manage.py test $(T) --top-level-directory /app/care_im_wrapper

coverage: ## run the test suite with a coverage report
	$(BACKEND) python -m coverage run --source=/app/care_im_wrapper/src/care_im_wrapper \
		/app/manage.py test tests --top-level-directory /app/care_im_wrapper
	$(BACKEND) python -m coverage report --skip-empty

coverage-html: coverage ## write an HTML coverage report to htmlcov/
	$(BACKEND) python -m coverage html
	$(BROWSER) htmlcov/index.html

docs: ## generate Sphinx HTML documentation, including the API reference
	$(DOCKER_RUN) -w /app/care_im_wrapper/docs backend sh -c "rm -rf _build reference && sphinx-build -M html . _build"
	@echo "Docs written to docs/_build/html/index.html"

docs-open: docs ## build the docs and open them in a browser
	$(BROWSER) docs/_build/html/index.html

schema: ## write CARE's OpenAPI schema (including this plugin's routes) to schema.yaml
	$(DOCKER_RUN) -w /app backend python manage.py spectacular --file /app/care_im_wrapper/schema.yaml
	@echo "OpenAPI schema written to schema.yaml"

release: dist ## package and upload a release
	twine upload dist/*

dist: clean ## builds source and wheel package
	python setup.py sdist
	python setup.py bdist_wheel
	ls -l dist

install: clean ## install the package to the active Python's site-packages
	python setup.py install
