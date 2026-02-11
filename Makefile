ROOT_DIRECTORY:=$(shell dirname $(realpath $(firstword $(MAKEFILE_LIST))))
PROJECT_DIRECTORY=$(PROJECT_DIRECTORY)

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

# -----------------------------------------------------------------
build-docker-compose:
	make run-docker-compose env=${env} action=build profile=build_only

up-docker-compose:
	make run-docker-compose env=${env} action=up

down-docker-compose:
	make run-docker-compose env=${env} action=down

config-docker-compose:
ifneq ("$(wildcard $(PROJECT_DIRECTORY)/docker-compose.yml)","")
	rm $(PROJECT_DIRECTORY)/docker-compose.yml
endif
	make run-docker-compose env=${env} action="config -o $(PROJECT_DIRECTORY)/docker-compose.yml"

run-docker-compose:
	make generate-env-file env=${env}
	make generate-compose-yml env=${env}
	export ENV_FILE=/tmp/.env && COMPOSE_PROFILES=${profile} docker compose -f /tmp/docker-compose.yml --env-file /tmp/.env ${action}

run-kubernetes:
	make generate-compose-full-yml env=${env} profile=build_only
	make generate-kubernetes-yml
	#kubectl apply -f $(PROJECT_DIRECTORY)

# -----------------------------------------------------------------
generate-env-file:
	cp $(ROOT_DIRECTORY)/.env /tmp/.env
ifneq ("$(wildcard $(PROJECT_DIRECTORY)/.env)","")
	cp /tmp/.env /tmp/.env.tmp
	sort -u -t '=' -k 1,1 $(PROJECT_DIRECTORY)/.env /tmp/.env.tmp > /tmp/.env
endif
ifneq ("$(wildcard $(PROJECT_DIRECTORY)/.env.${env})","")
	cp /tmp/.env /tmp/.env.tmp
	sort -u -t '=' -k 1,1 $(PROJECT_DIRECTORY)/.env.${env} /tmp/.env.tmp > /tmp/.env
endif
	echo "\n"ROOT_DIRECTORY=$(ROOT_DIRECTORY) >> /tmp/.env
	echo "\n"PROJECT_DIRECTORY=$(PROJECT_DIRECTORY) >> /tmp/.env
	echo "\n"ENV=${env} >> /tmp/.env
ifneq ("$(wildcard $(PROJECT_DIRECTORY)/Dockerfile)","")
	echo "\n"DOCKERFILE_DIRECTORY=$(PROJECT_DIRECTORY) >> /tmp/.env
else
	echo "\n"DOCKERFILE_DIRECTORY=$(ROOT_DIRECTORY) >> /tmp/.env
endif

generate-compose-yml:
	make load-qt
	cp $(ROOT_DIRECTORY)/docker-compose.yml /tmp/docker-compose.yml
ifneq ("$(wildcard $(PROJECT_DIRECTORY)/docker-compose.yml)","")
	$(ROOT_DIRECTORY)/qt '. *= load("$(PROJECT_DIRECTORY)/docker-compose.yml")' /tmp/docker-compose.yml > /tmp/docker-compose.yml
endif
ifneq ("$(wildcard $(PROJECT_DIRECTORY)/docker-compose.${env}.yml)","")
	$(ROOT_DIRECTORY)/qt '. *= load("$(PROJECT_DIRECTORY)/docker-compose.${env}.yml")' /tmp/docker-compose.yml > /tmp/docker-compose.yml
endif

generate-compose-full-yml:
	make generate-env-file env=${env}
	make generate-compose-yml env=${env}
	export ENV_FILE=/tmp/.env && COMPOSE_PROFILES=${profile} docker compose -f /tmp/docker-compose.yml --env-file /tmp/.env config > /tmp/docker-full-compose.yml

generate-kubernetes-yml:
	make load-kompose
	$(ROOT_DIRECTORY)/kompose convert -f /tmp/docker-full-compose.yml --out /tmp/docker-kubernetes-compose.yml

docker-clean-builds:
	docker rmi -f $$(docker images -a -q)

load-qt:
ifeq ("$(wildcard $(ROOT_DIRECTORY)/qt)","")
	curl -L https://github.com/mikefarah/yq/releases/download/v4.44.3/yq_linux_amd64 -o $(ROOT_DIRECTORY)/qt
	chmod +x $(ROOT_DIRECTORY)/qt
endif

load-kompose:
ifeq ("$(wildcard $(ROOT_DIRECTORY)/kompose)","")
	curl -L https://github.com/kubernetes/kompose/releases/download/v1.34.0/kompose-linux-amd64 -o $(ROOT_DIRECTORY)/kompose
	chmod +x $(ROOT_DIRECTORY)/kompose
endif
