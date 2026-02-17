ENTRY_PATH=/usr/local/bin/entrypoint.sh
PROJECT_DIRECTORY=/var/www/html
HOST_PROJECT_PATH ?= /home/tomas/Projects/joldija/app/public
BASE_BRANCH ?= origin/main
DIR:=$(or $(wordlist 2, $(words $(MAKECMDGOALS)), $(MAKECMDGOALS)), $(dir))

dir-analysis:
ifndef DIR
	$(error DIR is required. Usage: make dir-analysis DIR=src/MyModule)
endif
	cd $(PROJECT_DIRECTORY) && $(ENTRY_PATH) --error-format=json $(PROJECT_DIRECTORY)/$(DIR) > /tmp/phpstan/cache/report.json

baseline:
	cd $(PROJECT_DIRECTORY) && $(ENTRY_PATH) --generate-baseline=/tmp/phpstan/cache/phpstan-baseline.neon

report:
	$(MAKE) report-raw
	$(MAKE) report-html

report-raw:
	cd $(PROJECT_DIRECTORY) && $(ENTRY_PATH) --error-format=json > /tmp/phpstan/cache/report.json

report-html:
	HOST_PROJECT_PATH=$(HOST_PROJECT_PATH) /usr/local/bin/generate-report.sh

git-analysis:
	@cd $(PROJECT_DIRECTORY) && \
	\
	echo "## Analyze only uncommitted changed PHP files (working tree vs HEAD)" && \
	changed_files=$$(GIT_DIR=$(PROJECT_DIRECTORY)/.git GIT_WORK_TREE=$(PROJECT_DIRECTORY) git diff --name-only --diff-filter=d HEAD 2>/dev/null | grep '\.php$$') && \
	if [ -z "$$changed_files" ]; then \
		echo "No changed PHP files found."; \
	else \
		echo "Analyzing changed files:"; \
		echo "$$changed_files" | tr ' ' '\n'; \
		$(ENTRY_PATH) --no-progress --error-format=table $$changed_files; \
	fi && \
	\
	echo "## Analyze only staged PHP files (pre-commit use)" && \
	staged_files=$$(GIT_DIR=$(PROJECT_DIRECTORY)/.git GIT_WORK_TREE=$(PROJECT_DIRECTORY) git diff --name-only --diff-filter=d --cached 2>/dev/null | grep '\.php$$') && \
	if [ -z "$$staged_files" ]; then \
		echo "No staged PHP files found."; \
	else \
		echo "Analyzing staged files:"; \
		echo "$$staged_files" | tr ' ' '\n'; \
		$(ENTRY_PATH) --no-progress --error-format=table $$staged_files; \
	fi && \
	\
	echo "## Analyze PHP files changed vs base branch (default: $(BASE_BRANCH))" && \
	branch_changed_files=$$(GIT_DIR=$(PROJECT_DIRECTORY)/.git GIT_WORK_TREE=$(PROJECT_DIRECTORY) git diff --name-only --diff-filter=d $(BASE_BRANCH)...HEAD 2>/dev/null | grep '\.php$$') && \
	if [ -z "$$branch_changed_files" ]; then \
		echo "No changed PHP files vs $(BASE_BRANCH) (branch may not exist or be up to date)."; \
	else \
		echo "Analyzing files changed vs $(BASE_BRANCH):"; \
		echo "$$branch_changed_files" | tr ' ' '\n'; \
		$(ENTRY_PATH) --no-progress --error-format=table $$branch_changed_files; \
	fi

git-action-analysis:
	cd $(PROJECT_DIRECTORY) && $(ENTRY_PATH) --no-progress --error-format=github

bitbucket-analysis:
	cd $(PROJECT_DIRECTORY) && $(ENTRY_PATH) --no-progress --error-format=table
