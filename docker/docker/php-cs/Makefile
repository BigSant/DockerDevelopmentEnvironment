ENTRY_PATH=/usr/local/bin/entrypoint.sh
PROJECT_DIRECTORY=/var/www/html
BASE_BRANCH ?= origin/main
DIR:=$(or $(wordlist 2, $(words $(MAKECMDGOALS)), $(MAKECMDGOALS)), $(dir))

fix:
	$(ENTRY_PATH) fix $(PROJECT_DIRECTORY)

check:
	$(ENTRY_PATH) fix --dry-run --diff $(PROJECT_DIRECTORY)

dir-fix:
ifndef DIR
	$(error DIR is required. Usage: make dir-fix DIR=src/MyModule)
endif
	$(ENTRY_PATH) fix $(PROJECT_DIRECTORY)/$(DIR)

dir-check:
ifndef DIR
	$(error DIR is required. Usage: make dir-check DIR=src/MyModule)
endif
	$(ENTRY_PATH) fix --dry-run --diff $(PROJECT_DIRECTORY)/$(DIR)

git-fix:
	@cd $(PROJECT_DIRECTORY) && \
	\
	echo "## Fix only uncommitted changed PHP files (working tree vs HEAD)" && \
	changed_files=$$(GIT_DIR=$(PROJECT_DIRECTORY)/.git GIT_WORK_TREE=$(PROJECT_DIRECTORY) git diff --name-only --diff-filter=d HEAD 2>/dev/null | grep '\.php$$') && \
	if [ -z "$$changed_files" ]; then \
		echo "No changed PHP files found."; \
	else \
		echo "Fixing changed files:"; \
		echo "$$changed_files" | tr ' ' '\n'; \
		$(ENTRY_PATH) fix $$changed_files; \
	fi && \
	\
	echo "## Fix only staged PHP files (pre-commit use)" && \
	staged_files=$$(GIT_DIR=$(PROJECT_DIRECTORY)/.git GIT_WORK_TREE=$(PROJECT_DIRECTORY) git diff --name-only --diff-filter=d --cached 2>/dev/null | grep '\.php$$') && \
	if [ -z "$$staged_files" ]; then \
		echo "No staged PHP files found."; \
	else \
		echo "Fixing staged files:"; \
		echo "$$staged_files" | tr ' ' '\n'; \
		$(ENTRY_PATH) fix $$staged_files; \
	fi && \
	\
	echo "## Fix PHP files changed vs base branch (default: $(BASE_BRANCH))" && \
	branch_changed_files=$$(GIT_DIR=$(PROJECT_DIRECTORY)/.git GIT_WORK_TREE=$(PROJECT_DIRECTORY) git diff --name-only --diff-filter=d $(BASE_BRANCH)...HEAD 2>/dev/null | grep '\.php$$') && \
	if [ -z "$$branch_changed_files" ]; then \
		echo "No changed PHP files vs $(BASE_BRANCH) (branch may not exist or be up to date)."; \
	else \
		echo "Fixing files changed vs $(BASE_BRANCH):"; \
		echo "$$branch_changed_files" | tr ' ' '\n'; \
		$(ENTRY_PATH) fix $$branch_changed_files; \
	fi

git-check:
	@cd $(PROJECT_DIRECTORY) && \
	\
	echo "## Check only uncommitted changed PHP files (working tree vs HEAD)" && \
	changed_files=$$(GIT_DIR=$(PROJECT_DIRECTORY)/.git GIT_WORK_TREE=$(PROJECT_DIRECTORY) git diff --name-only --diff-filter=d HEAD 2>/dev/null | grep '\.php$$') && \
	if [ -z "$$changed_files" ]; then \
		echo "No changed PHP files found."; \
	else \
		echo "Checking changed files:"; \
		echo "$$changed_files" | tr ' ' '\n'; \
		$(ENTRY_PATH) fix --dry-run --diff $$changed_files; \
	fi && \
	\
	echo "## Check only staged PHP files (pre-commit use)" && \
	staged_files=$$(GIT_DIR=$(PROJECT_DIRECTORY)/.git GIT_WORK_TREE=$(PROJECT_DIRECTORY) git diff --name-only --diff-filter=d --cached 2>/dev/null | grep '\.php$$') && \
	if [ -z "$$staged_files" ]; then \
		echo "No staged PHP files found."; \
	else \
		echo "Checking staged files:"; \
		echo "$$staged_files" | tr ' ' '\n'; \
		$(ENTRY_PATH) fix --dry-run --diff $$staged_files; \
	fi && \
	\
	echo "## Check PHP files changed vs base branch (default: $(BASE_BRANCH))" && \
	branch_changed_files=$$(GIT_DIR=$(PROJECT_DIRECTORY)/.git GIT_WORK_TREE=$(PROJECT_DIRECTORY) git diff --name-only --diff-filter=d $(BASE_BRANCH)...HEAD 2>/dev/null | grep '\.php$$') && \
	if [ -z "$$branch_changed_files" ]; then \
		echo "No changed PHP files vs $(BASE_BRANCH) (branch may not exist or be up to date)."; \
	else \
		echo "Checking files changed vs $(BASE_BRANCH):"; \
		echo "$$branch_changed_files" | tr ' ' '\n'; \
		$(ENTRY_PATH) fix --dry-run --diff $$branch_changed_files; \
	fi

git-action-check:
	cd $(PROJECT_DIRECTORY) && $(ENTRY_PATH) fix --dry-run --diff --format=checkstyle

bitbucket-check:
	cd $(PROJECT_DIRECTORY) && $(ENTRY_PATH) fix --dry-run --diff
