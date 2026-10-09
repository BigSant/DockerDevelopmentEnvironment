# Identical for all projects. Keep project settings in .env / .env.<environment>.
PROJECT_DOCKER_DIRECTORY := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
SETUP_DIRECTORY ?= $(firstword $(wildcard $(PROJECT_DOCKER_DIRECTORY)/../../setup $(PROJECT_DOCKER_DIRECTORY)/../../../setup))
ifeq ($(SETUP_DIRECTORY),)
$(error Shared setup not found; pass SETUP_DIRECTORY=/absolute/path/to/setup)
endif
include $(SETUP_DIRECTORY)/docker/project.mk
