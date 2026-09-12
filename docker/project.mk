# Included by the identical Makefile distributed to every project.
ENV ?= local
PROJECT_DIRECTORY ?= $(if $(filter app,$(notdir $(patsubst %/,%,$(dir $(PROJECT_DOCKER_DIRECTORY))))),$(abspath $(PROJECT_DOCKER_DIRECTORY)/../..),$(abspath $(PROJECT_DOCKER_DIRECTORY)/..))
PYTHON ?= python3
PROJECT_RUNNER := $(SETUP_DIRECTORY)/docker/project.py
.DEFAULT_GOAL := help
.PHONY: init doctor pull shell ide-init ide-refresh db-backup db-prepare bootstrap test-init setup-info
.PHONY: db-fixtures-plan db-fixtures-load
.PHONY: help check config up build down ps logs phpstan phpstan-baseline phpcs e2e doctrine doctrine-diff doctrine-build db-import db-import-plan schema-export schema-check schema-hook-install restart composer npm cache-clear db-backup-prune runtime-info

# Quote each value as one shell argument, including paths with whitespace.
quote = '$(subst ','"'"',$(1))'
RUN_PROJECT = $(PYTHON) $(call quote,$(PROJECT_RUNNER)) --docker-directory $(call quote,$(PROJECT_DOCKER_DIRECTORY)) --project-directory $(call quote,$(PROJECT_DIRECTORY)) --env $(call quote,$(ENV)) $(if $(filter undefined,$(origin PROFILES)),,--profiles $(call quote,$(PROFILES)))

help:
	@echo 'bootstrap: prepare/check host, TLS and IDE; test-init: isolated test checkout and env'
	@echo 'db-backup [file=backup.sql.gz]; setup-info: shared version'
	@echo 'ide-init: prepare PhpStorm project settings; ide-refresh: refresh its private Compose file'
	@echo 'init / doctor: prepare local files / check readiness; pull: fetch external images; shell: PHP terminal'
	@echo 'check / config: validate / render shared sources without starting containers'
	@echo 'build: explicitly build images; up: start using existing images; down / ps / logs'
	@echo 'restart [service=php-fpm]: apply configuration; logs [service=database] [follow=1] [tail=100]'
	@echo 'runtime-info: resolved cache/mail policy; cache-clear: clear application and PHP caches'
	@echo 'composer / npm [dir=themes/example/_dev] [cmd="install"]: run package tools in application sources'
	@echo 'db-backup-prune [apply=1]: preview / prune old backups'
	@echo 'phpstan / phpcs / e2e: run QA tools; ENV=local|test|stage|prod; PROFILES= selects core only'
	@echo 'phpstan-baseline / doctrine cmd=status: baseline and schema tools'
	@echo 'doctrine-build: build only the optional migration tool, independently of application PHP'
	@echo 'doctrine-diff [ref=HEAD]: generate a migration from committed schema to the local DB'
	@echo 'db-import-plan file=dump.sql: preview; db-import file=dump.sql: import then run SQL hooks'
	@echo 'db-fixtures-plan / db-fixtures-load set=local|test: preview / load common + selected SQL fixtures'
	@echo 'db-import file=dump.sql db-fixtures=local: append fixtures after the import hooks'
	@echo 'db-prepare ENV=test: start only the isolated database before importing test data'
	@echo 'db-import file=dump.sql.gz backup=1: save a backup before importing a compressed dump'
	@echo 'schema-export / schema-check: export DB table definitions / compare with staged Git files'
	@echo 'schema-hook-install: enable pre-commit schema-check in the schema repository'

init doctor pull shell ide-refresh check config up build down ps phpstan-baseline schema-export schema-check schema-hook-install bootstrap setup-info db-prepare cache-clear runtime-info doctrine-build:
	@$(RUN_PROJECT) $@

restart:
	@$(RUN_PROJECT) $@ $(if $(service),--service $(call quote,$(service)),)

logs:
	@$(RUN_PROJECT) $@ $(if $(service),--service $(call quote,$(service)),) $(if $(filter 1,$(follow)),--follow,) $(if $(tail),--tail $(call quote,$(tail)),)

composer npm:
	@$(RUN_PROJECT) $@ $(if $(cmd),--command $(call quote,$(cmd)),) $(if $(dir),--workdir $(call quote,$(dir)),)

db-backup-prune:
	@$(RUN_PROJECT) $@ $(if $(filter 1,$(apply)),--apply,)

ide-init:
	@$(RUN_PROJECT) $@ $(if $(IDE_DOCKER_SERVER),--docker-server $(call quote,$(IDE_DOCKER_SERVER)),) $(if $(IDE_CONFIG_DIRECTORY),--ide-config-directory $(call quote,$(IDE_CONFIG_DIRECTORY)),)

phpstan phpcs e2e doctrine:
	@$(RUN_PROJECT) $@ $(if $(cmd),--command $(call quote,$(cmd)),)

doctrine-diff:
	@$(RUN_PROJECT) $@ $(if $(ref),--ref $(call quote,$(ref)),)

db-import db-import-plan:
	$(if $(filter undefined,$(origin fixtures)),,$(error Use db-fixtures=SET instead of fixtures=SET))
	@$(RUN_PROJECT) $@ --dump $(call quote,$(file)) $(if $(filter 1,$(backup)),--backup,) $(if $(filter undefined,$(origin db-fixtures)),,--db-fixtures $(call quote,$(db-fixtures)))

db-fixtures-plan db-fixtures-load:
	@$(RUN_PROJECT) $@ --db-fixtures $(call quote,$(set))

db-backup:
	@$(RUN_PROJECT) $@ $(if $(file),--output $(call quote,$(file)),)

test-init:
	@$(RUN_PROJECT) $@ $(if $(filter 1,$(refresh)),--refresh-test,)
