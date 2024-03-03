ROOT_DIR:=$(shell dirname $(realpath $(firstword $(MAKEFILE_LIST))))

run-ps:
	docker network create prestashop-net-${ps_instance} || true
	docker run -ti --name some-mysql-${ps_instance} --network prestashop-net-${ps_instance} --platform ${platform} -e APP_ENV=ci -e MYSQL_ROOT_PASSWORD=admin -e MYSQL_DATABASE=prestashop -p 4420:3306 -d mariadb:10.7.4
	docker run -ti -v $(ROOT_DIR):/var/www/html/modules/prestashopdevcon --platform ${platform} --name some-prestashop-${ps_instance} --network prestashop-net-${ps_instance} -e PS_DEV_MODE=1 -e DB_SERVER=some-mysql-${ps_instance} -e PS_INSTALL_AUTO=1 -e DB_NAME=prestashop -e PS_DOMAIN=localhost:8080 -e PS_FOLDER_ADMIN=admin1 -p 8080:80 -d prestashop/prestashop:${ps_instance}

install-module:
	docker exec -i some-prestashop-${ps_instance} sh -c "cd /var/www/html && php bin/console prestashop:module install prestashopdevcon"

reset-module:
	docker exec -i some-prestashop-${ps_instance} sh -c "cd /var/www/html && php bin/console prestashop:module reset prestashopdevcon"

run-wiremock:
	docker run -d -ti --rm --name wiremock-${ps_instance} --network prestashop-net-${ps_instance} -v $(ROOT_DIR)/wiremock:/home/wiremock -p 8443:8080 wiremock/wiremock

change-permissions:
	docker exec -i some-prestashop-${ps_instance} sh -c "chmod -R 777 /var/www/html"

run-integration-tests:
	make install-module
	make change-permissions
	docker exec -i some-prestashop-${ps_instance} sh -c "cd /var/www/html/modules/prestashopdevcon && php vendor/bin/phpunit -c tests/phpunit.xml --testsuite Integration"

run-tests-github-actions:
	make run-ps ps_instance=${ps_instance} platform=linux/amd64
	make run-wiremock ps_instance=${ps_instance}
	sleep 2m
	make run-integration-tests ps_instance=${ps_instance}
