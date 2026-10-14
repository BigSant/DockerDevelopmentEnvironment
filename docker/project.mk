# Included by the identical Makefile distributed to every project.
ENV ?= local
PROJECT_DIRECTORY ?= $(if $(filter app,$(notdir $(patsubst %/,%,$(dir $(PROJECT_DOCKER_DIRECTORY))))),$(abspath $(PROJECT_DOCKER_DIRECTORY)/../..),$(abspath $(PROJECT_DOCKER_DIRECTORY)/..))
PYTHON ?= python3
PROJECT_RUNNER := $(SETUP_DIRECTORY)/docker/project.py
.DEFAULT_GOAL := help
.PHONY: init doctor pull shell ide-init ide-refresh db-backup db-pull db-prepare bootstrap test-init setup-info
.PHONY: db-fixtures-plan db-fixtures-load
image-prune:
	@$(RUN_PROJECT) $@ $(if $(filter 1,$(apply)),--apply,) $(if $(filter 1,$(cache)),--cache,)

.PHONY: app-prepare image-prune db-restrict-grants db-monitor-user setup schema-resolve db-migrate db-rollback migration-draft db-dataset db-load test-prepare test-sync phpunit release deploy-images deploy cron-reload pipeline-deploy deploy-variables module-pull
.PHONY: help check config up build down ps logs phpstan phpstan-baseline phpcs e2e doctrine doctrine-diff doctrine-build db-import db-import-plan schema-export schema-check schema-hook-install git-hooks restart composer npm cache-clear db-backup-prune runtime-info

# Quote each value as one shell argument, including paths with whitespace.
quote = '$(subst ','"'"',$(1))'
RUN_PROJECT = $(PYTHON) $(call quote,$(PROJECT_RUNNER)) --docker-directory $(call quote,$(PROJECT_DOCKER_DIRECTORY)) --project-directory $(call quote,$(PROJECT_DIRECTORY)) --env $(call quote,$(ENV)) $(if $(filter undefined,$(origin PROFILES)),,--profiles $(call quote,$(PROFILES)))

help:
	@echo 'setup: prepare this project from a clean clone (init, bootstrap + schema hook, build, app-prepare, up); the DB is left alone'
	@echo 'app-prepare: only the application step (profile files such as PS_RELEASE_PATHS into the code); runs after build'
	@echo 'bootstrap: prepare/check host, TLS and IDE; test-init: isolated test checkout and env'
	@echo 'db-backup [file=backup.sql.gz] [full=1]: export without the data of registry skip tables (full=1: all); setup-info: shared version'
	@echo 'db-pull [from=prod|stage] [full=1] [fresh=1] [import=1] [backup=1] [configure=1]: server backup over SSH'
	@echo '        (newest registry backup; full=1 every table; fresh=1 a new one made now; asks for the server once)'
	@echo 'ide-init: prepare PhpStorm project settings; ide-refresh: refresh its private Compose file'
	@echo 'init / doctor: prepare local files / check readiness; pull: fetch external images; shell: PHP terminal'
	@echo 'check / config: validate / render shared sources without starting containers'
	@echo 'build: explicitly build images; up: start using existing images; down / ps / logs'
	@echo 'restart [service=php-fpm]: apply configuration; logs [service=database] [follow=1] [tail=100]'
	@echo 'runtime-info: resolved cache/mail policy; cache-clear: clear application and PHP caches'
	@echo 'composer / npm [dir=themes/example/_dev] [cmd="install"]: run package tools in application sources'
	@echo 'db-backup-prune [apply=1]: preview / prune old backups'
	@echo 'image-prune [apply=1] [cache=1]: preview / remove unused outdated setup images (and build cache)'
	@echo 'phpstan / phpcs / e2e: run QA tools; ENV=local|test|stage|prod; PROFILES= selects core only'
	@echo 'phpstan-baseline / doctrine cmd=status: baseline and schema tools'
	@echo 'doctrine-build: build only the optional migration tool, independently of application PHP'
	@echo 'doctrine-diff [ref=HEAD]: generate a migration from committed schema to the local DB'
	@echo 'db-import-plan file=dump.sql: preview; db-import file=dump.sql: import then run SQL hooks'
	@echo 'db-fixtures-plan / db-fixtures-load set=local|test: preview / load common + selected SQL fixtures'
	@echo 'db-import file=dump.sql db-fixtures=local: append fixtures after the import hooks'
	@echo 'db-prepare ENV=test: start only the isolated database before importing test data'
	@echo 'db-restrict-grants: one-time: own database (+ DATABASE_EXTRA_SCHEMAS) privileges only, separate root password (pre-v2 databases)'
	@echo 'db-import file=dump.sql.gz backup=1: save a backup before importing a compressed dump'
	@echo 'db-import file=dump.sql skip=1: leave out the data of tables the registry marks skip (default: everything)'
	@echo 'schema-export / schema-check: export DB table definitions / compare with staged Git files'
	@echo 'schema-hook-install: pre-commit hook that exports the schema and stops a commit that changes it'
	@echo 'git-hooks: pre-commit (schema check, then bin/pre-commit) and pre-push (bin/pre-push) in this clone'
	@echo 'schema-resolve: classify database tables the table registry does not know (include / drop)'
	@echo 'db-migrate [dry=1]: apply pending guarded migrations (database/migrations; dry=1 prints the SQL)'
	@echo 'migration-draft [name=short-name]: export the schema and draft the migrations from the last commit (no commit needed)'
	@echo 'db-rollback [steps=N|to=NAME] [dry=1]: run the rollback part of the latest applied migrations, newest first'
	@echo 'db-monitor-user: create/update the DB monitor account (performance_schema, sys, PROCESS only)'
	@echo 'db-dataset [set=dev]: rebuild the anonymised base data set from the local LIVE copy'
	@echo 'db-load [set=dev] [replace=1] [backup=1]: load schema + data set + fixtures, then the after-import sequence'
	@echo 'test-prepare [set=dev]: refresh the isolated ENV=test copy, load the mock data set into its DB and start it'
	@echo 'test-sync: refresh the ENV=test application copy from the working tree (PhpStorm runs it before tests)'
	@echo 'phpunit [module=NAME] [filter=PATTERN] [sync=0]: run PHPUnit suites in ENV=test only (e2e also runs there)'
	@echo 'release ENV=stage|prod: build images here from committed setup, write the image lock and release compose'
	@echo 'deploy-images ENV=stage|prod [transfer=0]: load the release images into DEPLOY_CONTEXT and start them there'
	@echo 'deploy ENV=stage|prod ref=TAG [backup=1] | rollback=1: release the code, (back up), rsync, hook, upgrade, migrate'
	@echo 'cron-reload [ENV=stage|prod]: the running cron container takes config/cron/ now (checked, no restart)'
	@echo 'module-pull ENV=stage|prod name=MODULE: copy a module that exists only on the target into the shop checkout'
	@echo 'pipeline-deploy [ENV=stage|prod]: the Bitbucket / GitHub Actions deploy step in the build root (REF, IMAGES, CRON, ROLLBACK, BACKUP)'
	@echo 'deploy-variables [ENV=stage|prod] [from=FILE [replace=1]] [token=PATH] [names=1]: check / fill Bitbucket Deployment variables or GitHub environment secrets'

setup app-prepare init doctor pull shell ide-refresh check config up build down ps phpstan-baseline schema-export schema-check schema-hook-install git-hooks schema-resolve test-sync db-monitor-user bootstrap setup-info db-prepare db-restrict-grants cache-clear runtime-info doctrine-build:
	@$(RUN_PROJECT) $@

restart:
	@$(RUN_PROJECT) $@ $(if $(service),--service $(call quote,$(service)),)

logs:
	@$(RUN_PROJECT) $@ $(if $(service),--service $(call quote,$(service)),) $(if $(filter 1,$(follow)),--follow,) $(if $(tail),--tail $(call quote,$(tail)),)

composer npm:
	@$(RUN_PROJECT) $@ $(if $(cmd),--command $(call quote,$(cmd)),) $(if $(dir),--workdir $(call quote,$(dir)),)

db-migrate:
	@$(RUN_PROJECT) $@ $(if $(filter 1,$(dry)),--dry,)

migration-draft:
	@$(RUN_PROJECT) $@ $(if $(name),--name $(call quote,$(name)),)

db-rollback:
	@$(RUN_PROJECT) $@ $(if $(steps),--steps $(call quote,$(steps)),) $(if $(to),--to $(call quote,$(to)),) $(if $(filter 1,$(dry)),--dry,)

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
	@$(RUN_PROJECT) $@ --dump $(call quote,$(file)) $(if $(filter 1,$(backup)),--backup,) $(if $(filter 1,$(skip)),--skip,) $(if $(filter undefined,$(origin db-fixtures)),,--db-fixtures $(call quote,$(db-fixtures)))

db-fixtures-plan db-fixtures-load:
	@$(RUN_PROJECT) $@ --db-fixtures $(call quote,$(set))

release:
	@$(RUN_PROJECT) $@ $(if $(filter 1,$(allow_dirty)),--allow-dirty,)

deploy:
	@$(RUN_PROJECT) $@ $(if $(ref),--release-ref $(call quote,$(ref)),) $(if $(filter 1,$(backup)),--backup,) $(if $(filter 1,$(rollback)),--rollback,)

pipeline-deploy:
	@$(RUN_PROJECT) $@

deploy-variables:
	@$(RUN_PROJECT) $@ $(if $(from),--from $(call quote,$(from)),) $(if $(filter 1,$(replace)),--replace,) $(if $(token),--token-file $(call quote,$(token)),) $(if $(filter 1,$(names)),--names,)

module-pull:
	@$(RUN_PROJECT) $@ --name $(call quote,$(name))

deploy-images:
	@$(RUN_PROJECT) $@ $(if $(filter 0,$(transfer)),--no-transfer,)

cron-reload:
	@$(RUN_PROJECT) $@

test-prepare:
	@$(RUN_PROJECT) $@ $(if $(set),--set $(call quote,$(set)),)

phpunit:
	@$(RUN_PROJECT) $@ $(if $(module),--module $(call quote,$(module)),) $(if $(filter),--filter $(call quote,$(filter)),) $(if $(filter 0,$(sync)),--no-sync,)

db-dataset:
	@$(RUN_PROJECT) $@ $(if $(set),--set $(call quote,$(set)),)

db-load:
	@$(RUN_PROJECT) $@ $(if $(set),--set $(call quote,$(set)),) $(if $(filter 1,$(replace)),--replace,) $(if $(filter 1,$(backup)),--backup,)

db-backup:
	@$(RUN_PROJECT) $@ $(if $(file),--output $(call quote,$(file)),) $(if $(filter 1,$(full)),--full,)

db-pull:
	@$(RUN_PROJECT) $@ $(if $(from),--from $(call quote,$(from)),) $(if $(filter 1,$(import)),--import,) $(if $(filter 1,$(backup)),--backup,) $(if $(filter 1,$(configure)),--configure,) $(if $(filter 1,$(full)),--full,) $(if $(filter 1,$(fresh)),--fresh,)

test-init:
	@$(RUN_PROJECT) $@ $(if $(filter 1,$(refresh)),--refresh-test,)
