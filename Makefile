ROOT_DIR:=$(shell dirname $(realpath $(firstword $(MAKEFILE_LIST))))

build-local:
	make build-docker-compose env=local

up-local:
	make up-docker-compose env=local

down-local:
	make down-docker-compose env=local

sql-fix:
	path="$(realpath $(firstword $(path)))"
	sed -i 's/utf8mb4_unicode_520_ci/utf8mb4_unicode_ci/g' $(path)
	sed -i 's/utf8mb4_0900_ai_ci/utf8mb4_general_ci/g' $(path)
	echo 'DONE, upload SQL'

build-prod:
	make build-docker-compose env=prod

up-prod:
	make up-docker-compose env=prod

down-prod:
	make down-docker-compose env=prod

build-stage:
	make build-docker-compose env=stage

up-stage:
	make up-docker-compose env=stage

down-stage:
	make down-docker-compose env=stage

build-docker-compose:
	make run-docker-compose env=${env} action=build profile=build_only

up-docker-compose:
	make run-docker-compose env=${env} action=up

down-docker-compose:
	make run-docker-compose env=${env} action=down

run-docker-compose:
	make generate-env-file env=${env}
	export ENV_FILE=/tmp/.env && COMPOSE_PROFILES=${profile} docker compose -f $(ROOT_DIR)/docker-compose.yml --env-file /tmp/.env ${action}

generate-env-file:
	sort -u -t '=' -k 1,1 $(ROOT_DIR)/.env.${env} $(ROOT_DIR)/.env > /tmp/.env
	echo "\n"COMPOSE_PROJECT_NAME=$$\{PROJECT_NAME\}-$$\{ENV\}  >> /tmp/.env

docker-clean-builds:
	docker rmi -f $$(docker images -a -q)
