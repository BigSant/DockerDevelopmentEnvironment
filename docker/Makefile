ROOT_DIRECTORY:=$(shell dirname $(realpath $(firstword $(MAKEFILE_LIST))))
PROJECT_DIRECTORY=$(PROJECT_DIRECTORY)
PROJECT_DATA_DIRECTORY=$(PROJECT_DIRECTORY)/data
PROJECT_APP_DIRECTORY=$(PROJECT_DIRECTORY)/app
PROJECT_DOCKER_DIRECTORY=$(PROJECT_APP_DIRECTORY)/docker
PROJECT_CONFIG_DIRECTORY=$(PROJECT_APP_DIRECTORY)/config
PROJECT_WEB_DIRECTORY=$(PROJECT_APP_DIRECTORY)/public

# Specific action variables
SQL_DUMP_FILE:=$(SQL_DUMP_FILE)
SQL_DUMP_DATABASE:=$(SQL_DUMP_DATABASE)
SQL_DUMP_DROP_DATABASE:=$(SQL_DUMP_DROP_DATABASE)
SQL_DUMP_INITIALIZE:=$(SQL_DUMP_INITIALIZE)

# All profiles — activated only when rendering the full project compose snapshot
# (config-docker-compose) so the snapshot stays complete regardless of the runtime
# selection. Keep in sync with the profiles: keys in docker/*/docker-compose.yml.
ALL_PROFILES := build_only,phpcs,phpstan,mailpit,pma,cron,playwright

local:
	make config-local
	make build-local
	make up-local

build-local:
	make build-docker-compose env=local

up-local:
	make up-docker-compose env=local

down-local:
	make down-docker-compose env=local

config-local:
	make config-docker-compose env=local

db-init-local:
	make db-init env=local

prod:
	make config-prod
	make build-prod
	make up-prod

build-prod:
	make build-docker-compose env=prod

up-prod:
	make up-docker-compose env=prod

down-prod:
	make down-docker-compose env=prod

config-prod:
	make config-docker-compose env=prod

stage:
	make config-stage
	make build-stage
	make up-stage

build-stage:
	make build-docker-compose env=stage

up-stage:
	make up-docker-compose env=stage

down-stage:
	make down-docker-compose env=stage

config-stage:
	make config-docker-compose env=stage

db-init:
	make generate-env-file env=${env}

	$(eval DB_USER   := $(shell grep '^DATABASE_USER='   /tmp/.env | cut -d= -f2))
	$(eval DB_PASS   := $(shell grep '^DATABASE_PASSWORD=' /tmp/.env | cut -d= -f2))

	$(eval TARGET_DB := $(SQL_DUMP_DATABASE))
	$(eval TARGET_DB := $(or $(TARGET_DB), $(shell grep '^DATABASE_NAME=' /tmp/.env | cut -d= -f2)))

	$(eval INITIALIZE := $(SQL_DUMP_INITIALIZE))
	$(eval INITIALIZE := $(or $(INITIALIZE), "1"))

ifeq ("$(SQL_DUMP_DROP_DATABASE)","1")
	echo "==> Dropping and recreating: ${TARGET_DB}"
	docker compose -f /tmp/docker-compose.yml --env-file /tmp/.env exec -T database mysql --user="$(DB_USER)" --password="$(DB_PASS)" --get-server-public-key -e "DROP DATABASE IF EXISTS \`$(TARGET_DB)\`; CREATE DATABASE \`$(TARGET_DB)\`;"
endif

	echo "==> Creating table: ${TARGET_DB}"
	docker compose -f /tmp/docker-compose.yml --env-file /tmp/.env exec -T database mysql --user="$(DB_USER)" --password="$(DB_PASS)" --get-server-public-key -e "CREATE DATABASE IF NOT EXISTS \`$(TARGET_DB)\`;"

	echo "==> Uploading dump: $SQL_DUMP_FILE"
ifeq ("$(shell command -v pv 2>/dev/null)", "")
	docker compose -f /tmp/docker-compose.yml --env-file /tmp/.env exec -T database mysql --user="$(DB_USER)" --password="$(DB_PASS)" --get-server-public-key "$(TARGET_DB)" < "$(SQL_DUMP_FILE)"
else
	pv "$(SQL_DUMP_FILE)" | docker compose -f /tmp/docker-compose.yml --env-file /tmp/.env exec -T database mysql --user="$(DB_USER)" --password="$(DB_PASS)" --get-server-public-key "$(TARGET_DB)"
endif

	@if [ "$(INITIALIZE)" = "1" ]; then \
		echo "==> Running init configuration"; \
		if [ -s "$(PROJECT_CONFIG_DIRECTORY)/sql/init.sql" ]; then \
			echo "==> Running init.sql" && \
			bash -c 'set -a && source /tmp/.env && set +a && \
				envsubst < "$(PROJECT_CONFIG_DIRECTORY)/sql/init.sql" | \
				docker compose -f /tmp/docker-compose.yml --env-file /tmp/.env \
					exec -T database mysql \
					--user="$(DB_USER)" --password="$(DB_PASS)" --get-server-public-key \
					"$(TARGET_DB)"'; \
		fi; \
		if [ -s "$(PROJECT_CONFIG_DIRECTORY)/sql/init.$(env).sql" ]; then \
			echo "==> Running init.$(env).sql" && \
			bash -c 'set -a && source /tmp/.env && set +a && \
				envsubst < "$(PROJECT_CONFIG_DIRECTORY)/sql/init.$(env).sql" | \
				docker compose -f /tmp/docker-compose.yml --env-file /tmp/.env \
					exec -T database mysql \
					--user="$(DB_USER)" --password="$(DB_PASS)" --get-server-public-key \
					"$(TARGET_DB)"'; \
		fi; \
	fi

# -----------------------------------------------------------------
build-docker-compose:
	make run-docker-compose env=${env} action="build php-fpm-base" profile=build_only
	make run-docker-compose env=${env} action=build

up-docker-compose:
	make run-docker-compose env=${env} action="up -d"

down-docker-compose:
	make run-docker-compose env=${env} action=down

docker-clean-builds:
	docker rmi -f $$(docker images -a -q)

config-docker-compose:
ifneq ("$(wildcard $(PROJECT_DOCKER_DIRECTORY)/docker-compose.yml)","")
	rm $(PROJECT_DOCKER_DIRECTORY)/docker-compose.yml
endif
	make run-docker-compose env=${env} action="config -o $(PROJECT_DOCKER_DIRECTORY)/docker-compose.yml" profile=$(ALL_PROFILES)

run-docker-compose:
	make generate-env-file env=${env}
	make generate-compose-yml env=${env}
	export ENV_FILE=/tmp/.env BUILDX_NO_DEFAULT_ATTESTATIONS=1 BUILDX_METADATA_PROVENANCE=disabled && \
	PROFILES="${profile}" && \
	if [ -z "$$PROFILES" ]; then PROFILES="$$(grep '^COMPOSE_PROFILES=' /tmp/.env | tail -n1 | cut -d= -f2-)"; fi && \
	if [ -z "$$PROFILES" ]; then PROFILES="$$(grep "^COMPOSE_PROFILES_$$(echo '${env}' | tr a-z A-Z)=" /tmp/.env | tail -n1 | cut -d= -f2-)"; fi && \
	COMPOSE_PROFILES="$$PROFILES" docker compose -f /tmp/docker-compose.yml --env-file /tmp/.env ${action}

# -----------------------------------------------------------------
# QA / test tools — run EPHEMERALLY (docker compose run --rm) so they use RAM only
# during the run and never linger as idle containers. They are in NO COMPOSE_PROFILES
# default, so `make up` never starts them; `run` enables the service on demand even
# though its profile is inactive. Override the inner command via *_CMD
# (e.g. PHPSTAN_CMD=git-analysis); QA_ENV selects which env config to use.
QA_ENV      ?= local
PHPSTAN_CMD ?= report
PHPCS_CMD   ?= check
E2E_CMD     ?= npx playwright test

phpstan:
	make generate-env-file env=$(QA_ENV)
	make generate-compose-yml env=$(QA_ENV)
	export ENV_FILE=/tmp/.env BUILDX_NO_DEFAULT_ATTESTATIONS=1 BUILDX_METADATA_PROVENANCE=disabled && \
	COMPOSE_PROFILES=phpstan docker compose -f /tmp/docker-compose.yml --env-file /tmp/.env \
	run --rm --no-deps --entrypoint make php-phpstan $(PHPSTAN_CMD)

phpcs:
	make generate-env-file env=$(QA_ENV)
	make generate-compose-yml env=$(QA_ENV)
	export ENV_FILE=/tmp/.env BUILDX_NO_DEFAULT_ATTESTATIONS=1 BUILDX_METADATA_PROVENANCE=disabled && \
	COMPOSE_PROFILES=phpcs docker compose -f /tmp/docker-compose.yml --env-file /tmp/.env \
	run --rm --no-deps --entrypoint make php-cs $(PHPCS_CMD)

e2e:
	make generate-env-file env=$(QA_ENV)
	make generate-compose-yml env=$(QA_ENV)
	export ENV_FILE=/tmp/.env BUILDX_NO_DEFAULT_ATTESTATIONS=1 BUILDX_METADATA_PROVENANCE=disabled && \
	COMPOSE_PROFILES=playwright docker compose -f /tmp/docker-compose.yml --env-file /tmp/.env \
	run --rm --entrypoint sh playwright -c "$(E2E_CMD)"

# -----------------------------------------------------------------
generate-env-file:
	cp $(ROOT_DIRECTORY)/.env /tmp/.env
ifneq ("$(wildcard $(PROJECT_DOCKER_DIRECTORY)/.env)","")
	cp /tmp/.env /tmp/.env.tmp
	sort -u -t '=' -k 1,1 $(PROJECT_DOCKER_DIRECTORY)/.env /tmp/.env.tmp > /tmp/.env
endif
ifneq ("$(wildcard $(PROJECT_DOCKER_DIRECTORY)/.env.${env})","")
	cp /tmp/.env /tmp/.env.tmp
	sort -u -t '=' -k 1,1 $(PROJECT_DOCKER_DIRECTORY)/.env.${env} /tmp/.env.tmp > /tmp/.env
endif
	echo "\n"ROOT_DIRECTORY=$(ROOT_DIRECTORY) >> /tmp/.env
	echo "\n"PROJECT_DIRECTORY=$(PROJECT_DIRECTORY) >> /tmp/.env
	echo "\n"PROJECT_DATA_DIRECTORY=$(PROJECT_DATA_DIRECTORY) >> /tmp/.env
	echo "\n"PROJECT_APP_DIRECTORY=$(PROJECT_APP_DIRECTORY) >> /tmp/.env
	echo "\n"PROJECT_DOCKER_DIRECTORY=$(PROJECT_DOCKER_DIRECTORY) >> /tmp/.env
	echo "\n"PROJECT_CONFIG_DIRECTORY=$(PROJECT_CONFIG_DIRECTORY) >> /tmp/.env
	echo "\n"PROJECT_WEB_DIRECTORY=$(PROJECT_WEB_DIRECTORY) >> /tmp/.env
	echo "\n"ENV=${env} >> /tmp/.env
	echo "\n"HOST_UID=$(shell id -u) >> /tmp/.env
	echo "\n"HOST_GID=$(shell id -g) >> /tmp/.env
ifneq ("$(wildcard $(PROJECT_DOCKER_DIRECTORY)/Dockerfile)","")
	echo "\n"DOCKERFILE_DIRECTORY=$(PROJECT_DOCKER_DIRECTORY) >> /tmp/.env
else
	echo "\n"DOCKERFILE_DIRECTORY=$(ROOT_DIRECTORY) >> /tmp/.env
endif

generate-compose-yml:
	make load-qt
	cp $(ROOT_DIRECTORY)/docker-compose.yml /tmp/docker-compose.yml
ifneq ("$(wildcard $(PROJECT_DOCKER_DIRECTORY)/docker-compose.yml)","")
	$(ROOT_DIRECTORY)/qt '. *= load("$(PROJECT_DOCKER_DIRECTORY)/docker-compose.yml")' /tmp/docker-compose.yml > /tmp/docker-compose.tmp && mv /tmp/docker-compose.tmp /tmp/docker-compose.yml
endif
ifneq ("$(wildcard $(PROJECT_DOCKER_DIRECTORY)/docker-compose.${env}.yml)","")
	$(ROOT_DIRECTORY)/qt '. *= load("$(PROJECT_DOCKER_DIRECTORY)/docker-compose.${env}.yml")' /tmp/docker-compose.yml > /tmp/docker-compose.tmp && mv /tmp/docker-compose.tmp /tmp/docker-compose.yml
endif

load-qt:
ifeq ("$(wildcard $(ROOT_DIRECTORY)/qt)","")
	curl -fL https://github.com/mikefarah/yq/releases/download/v4.44.3/yq_linux_amd64 -o $(ROOT_DIRECTORY)/qt
	echo "a2c097180dd884a8d50c956ee16a9cec070f30a7947cf4ebf87d5f36213e9ed7  $(ROOT_DIRECTORY)/qt" | sha256sum -c - || { rm -f $(ROOT_DIRECTORY)/qt; exit 1; }
	chmod +x $(ROOT_DIRECTORY)/qt
endif
