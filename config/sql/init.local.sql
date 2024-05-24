UPDATE `ps_configuration` SET `value` = '0' WHERE `name` LIKE 'PS_SSL_ENABLED';
UPDATE `ps_configuration` SET `value` = '0' WHERE `name` LIKE 'PS_SSL_ENABLED_EVERYWHERE';
UPDATE `ps_configuration` SET `value` = '%{DOMAIN}' WHERE `name` LIKE 'PS_SHOP_DOMAIN_SSL';

UPDATE `ps_configuration` SET `value` = '3' WHERE `name` LIKE 'PS_MAIL_METHOD';
UPDATE `ps_configuration` SET `value` = '1' WHERE `name` LIKE 'PS_MAIL_SERVER';
UPDATE `ps_configuration` SET `value` = '1' WHERE `name` LIKE 'PS_MAIL_USER';
UPDATE `ps_configuration` SET `value` = '1' WHERE `name` LIKE 'PS_MAIL_PASSWD';

UPDATE `ps_shop_url` SET `domain` = '%{DOMAIN}';
UPDATE `ps_shop_url` SET `domain_ssl` = '%{DOMAIN}';
UPDATE `ps_shop_url` SET `physical_uri` = '/';
UPDATE `ps_shop_url` SET `virtual_uri` = '';
