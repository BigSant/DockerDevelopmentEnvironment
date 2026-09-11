# Included by the identical Makefile distributed to every project.
ENV ?= local
PROJECT_DIRECTORY ?= $(if $(filter app,$(notdir $(patsubst %/,%,$(dir $(PROJECT_DOCKER_DIRECTORY))))),$(abspath $(PROJECT_DOCKER_DIRECTORY)/../..),$(abspath $(PROJECT_DOCKER_DIRECTORY)/..))
PYTHON ?= python3
PROJECT_RUNNER := $(SETUP_DIRECTORY)/docker/project.py
.DEFAULT_GOAL := help
.PHONY: init doctor pull shell
.PHONY: help check config up build down ps logs phpstan phpstan-baseline phpcs e2e doctrine db-import db-import-plan schema-export schema-check schema-hook-install

# Quote each value as one shell argument, including paths with whitespace.
quote = '$(subst ','"'"',$(1))'
RUN_PROJECT = $(PYTHON) $(call quote,$(PROJECT_RUNNER)) --docker-directory $(call quote,$(PROJECT_DOCKER_DIRECTORY)) --project-directory $(call quote,$(PROJECT_DIRECTORY)) --env $(call quote,$(ENV)) $(if $(filter undefined,$(origin PROFILES)),,--profiles $(call quote,$(PROFILES)))

help:
	@echo 'init / doctor: prepare local files / check readiness; pull: fetch external images; shell: PHP terminal'
	@echo 'check / config: validate / render shared sources without starting containers'
	@echo 'build: explicitly build images; up: start using existing images; down / ps / logs'
	@echo 'phpstan / phpcs / e2e: run QA tools; ENV=local|stage|prod; PROFILES= selects core only'
	@echo 'phpstan-baseline / doctrine cmd=status: baseline and schema tools'
	@echo 'db-import-plan file=dump.sql: preview; db-import file=dump.sql: import then run SQL hooks'
	@echo 'schema-export / schema-check: export DB table definitions / compare with staged Git files'
	@echo 'schema-hook-install: enable pre-commit schema-check in the schema repository'

init doctor pull shell check config up build down ps logs phpstan-baseline schema-export schema-check schema-hook-install:
	@$(RUN_PROJECT) $@

phpstan phpcs e2e doctrine:
	@$(RUN_PROJECT) $@ $(if $(cmd),--command $(call quote,$(cmd)),)

db-import db-import-plan:
	@$(RUN_PROJECT) $@ --dump $(call quote,$(file))
