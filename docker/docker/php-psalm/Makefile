ENTRY_PATH=/usr/local/bin/entrypoint.sh
PROJECT_DIRECTORY=/var/www/html

baseline:
	cd $(PROJECT_DIRECTORY) && $(ENTRY_PATH) --set-baseline=/tmp/psalm/config/psalm-baseline.xml --threads=1 --disable-extension=parallel

analysis:
	cd $(PROJECT_DIRECTORY) && $(ENTRY_PATH) --diff

report:
	cd $(PROJECT_DIRECTORY) && $(ENTRY_PATH) --diff --output-format=json > /tmp/psalm/cache/report.json
	echo /tmp/psalm/cache/report.json
