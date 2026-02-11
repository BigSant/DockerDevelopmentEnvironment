ENTRY_PATH=/usr/local/bin/entrypoint.sh

baseline:
	$(ENTRY_PATH) --set-baseline=/tmp/analysis/psalm/psalm-baseline.xml --threads=1 --disable-extension=parallel

analysis:
	$(ENTRY_PATH)

report:
	$(ENTRY_PATH) --output-format=json > /tmp/analysis/psalm/report.json
