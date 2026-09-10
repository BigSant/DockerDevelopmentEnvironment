# Included by the identical Makefile distributed to every project.
ENV ?= local
PROJECT_DIRECTORY ?= $(if $(filter app,$(notdir $(patsubst %/,%,$(dir $(PROJECT_DOCKER_DIRECTORY))))),$(abspath $(PROJECT_DOCKER_DIRECTORY)/../..),$(abspath $(PROJECT_DOCKER_DIRECTORY)/..))
PYTHON ?= python3
PROJECT_RUNNER := $(SETUP_DIRECTORY)/docker/project.py
.DEFAULT_GOAL := help
.PHONY: help check config up build down ps logs phpstan phpcs e2e

# Quote each value as one shell argument, including paths with whitespace.
quote = '$(subst ','"'"',$(1))'
RUN_PROJECT = $(PYTHON) $(call quote,$(PROJECT_RUNNER)) --docker-directory $(call quote,$(PROJECT_DOCKER_DIRECTORY)) --project-directory $(call quote,$(PROJECT_DIRECTORY)) --env $(call quote,$(ENV)) $(if $(filter undefined,$(origin PROFILES)),,--profiles $(call quote,$(PROFILES)))

help:
	@echo 'check / config: validate / render shared sources without starting containers'
	@echo 'build: explicitly build images; up: start using existing images; down / ps / logs'
	@echo 'phpstan / phpcs / e2e: run QA tools; ENV=local|stage|prod; PROFILES= selects core only'

check config up build down ps logs:
	@$(RUN_PROJECT) $@

phpstan phpcs e2e:
	@$(RUN_PROJECT) $@ $(if $(cmd),--command $(call quote,$(cmd)),)
