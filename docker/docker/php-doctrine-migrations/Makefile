ENTRY_PATH=/usr/local/bin/entrypoint.sh
VERSION ?=

status:
	$(ENTRY_PATH) status

migrate:
	$(ENTRY_PATH) migrate --no-interaction

rollback:
	$(ENTRY_PATH) migrate prev --no-interaction

diff:
	$(ENTRY_PATH) diff

generate:
	$(ENTRY_PATH) generate

execute:
ifndef VERSION
	$(error VERSION is required. Usage: make execute VERSION=DoctrineMigrations\\Version20240101000000)
endif
	$(ENTRY_PATH) execute --up "$(VERSION)" --no-interaction

execute-down:
ifndef VERSION
	$(error VERSION is required. Usage: make execute-down VERSION=DoctrineMigrations\\Version20240101000000)
endif
	$(ENTRY_PATH) execute --down "$(VERSION)" --no-interaction

list:
	$(ENTRY_PATH) list

latest:
	$(ENTRY_PATH) latest

current:
	$(ENTRY_PATH) current

up-to-date:
	$(ENTRY_PATH) up-to-date

sync-metadata:
	$(ENTRY_PATH) sync-metadata-storage
