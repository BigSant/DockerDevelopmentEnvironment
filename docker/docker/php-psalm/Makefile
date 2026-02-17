ENTRY_PATH=/usr/local/bin/entrypoint.sh
PROJECT_DIRECTORY=/var/www/html
PSALM_FLAGS = --show-info=false --threads=1
BASE_BRANCH ?= origin/main

baseline:
	cd $(PROJECT_DIRECTORY) && $(ENTRY_PATH) --threads=1 --set-baseline=/tmp/psalm/cache/psalm-baseline.xml

analysis:
	cd $(PROJECT_DIRECTORY) && $(ENTRY_PATH) $(PSALM_FLAGS) --diff

report:
	cd $(PROJECT_DIRECTORY) && $(ENTRY_PATH) $(PSALM_FLAGS) --diff --output-format=json > /tmp/psalm/cache/report.json
	echo /tmp/psalm/cache/report.json

git-analysis:
	@cd $(PROJECT_DIRECTORY) && \
	\
	echo "## Analyze only uncommitted changed PHP files (working tree vs HEAD)" && \
	changed_files=$$(GIT_DIR=$(PROJECT_DIRECTORY)/.git GIT_WORK_TREE=$(PROJECT_DIRECTORY) git diff --name-only --diff-filter=d HEAD 2>/dev/null) && \
	if [ -z "$$changed_files" ]; then \
		echo "No changed PHP files found."; \
	else \
		echo "Analyzing changed files:"; \
		echo "$$changed_files" | tr ' ' '\n'; \
		$(ENTRY_PATH) $(PSALM_FLAGS) $$changed_files; \
	fi && \
	\
	echo "## Analyze only staged PHP files (pre-commit use)" && \
	staged_files=$$(GIT_DIR=$(PROJECT_DIRECTORY)/.git GIT_WORK_TREE=$(PROJECT_DIRECTORY) git diff --name-only --diff-filter=d --cached 2>/dev/null) && \
	if [ -z "$$staged_files" ]; then \
		echo "No staged PHP files found."; \
	else \
		echo "Analyzing staged files:"; \
		echo "$$staged_files" | tr ' ' '\n'; \
		$(ENTRY_PATH) $(PSALM_FLAGS) $$staged_files; \
	fi && \
	\
	echo "## Analyze PHP files changed vs base branch (default: $(BASE_BRANCH))" && \
	branch_changed_files=$$(GIT_DIR=$(PROJECT_DIRECTORY)/.git GIT_WORK_TREE=$(PROJECT_DIRECTORY) git diff --name-only --diff-filter=d $(BASE_BRANCH)...HEAD 2>/dev/null) && \
	if [ -z "$$branch_changed_files" ]; then \
		echo "No changed PHP files vs $(BASE_BRANCH) (branch may not exist or be up to date)."; \
	else \
		echo "Analyzing files changed vs $(BASE_BRANCH):"; \
		echo "$$branch_changed_files" | tr ' ' '\n'; \
		$(ENTRY_PATH) $(PSALM_FLAGS) $$branch_changed_files; \
	fi
