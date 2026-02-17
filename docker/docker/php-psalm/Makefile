ENTRY_PATH=/usr/local/bin/entrypoint.sh
PROJECT_DIRECTORY=/var/www/html

baseline:
	cd $(PROJECT_DIRECTORY) && $(ENTRY_PATH) --set-baseline=/tmp/psalm/cache/psalm-baseline.xml --threads=1 --disable-extension=parallel

analysis:
	cd $(PROJECT_DIRECTORY) && $(ENTRY_PATH) --diff

report:
	cd $(PROJECT_DIRECTORY) && $(ENTRY_PATH) --diff --output-format=json > /tmp/psalm/cache/report.json
	echo /tmp/psalm/cache/report.json

git-analysis:
	cd $(PROJECT_DIRECTORY)
	BASE=$$(git rev-parse --verify origin/main 2>/dev/null && echo "origin/main" || git rev-parse --verify origin/master 2>/dev/null && echo "origin/master" || echo "HEAD^");
	FILES=$$(git diff --name-only $$BASE...HEAD -- '*.php' 2>/dev/null); echo "$$FILES"; $(ENTRY_PATH) $$FILES;

#git-report:
#    @cd $(PROJECT_DIRECTORY) && \
#    if [ ! -d .git ]; then \
#        echo "[git-report] No git repository found, falling back to full report..."; \
#        $(MAKE) -C /psalm report; \
#    else \
#        BASE=...; FILES=...; \
#        $(ENTRY_PATH) --output-format=json $$FILES > /tmp/psalm/cache/git-report.json; \
#        echo /tmp/psalm/cache/git-report.json; \
#    fi