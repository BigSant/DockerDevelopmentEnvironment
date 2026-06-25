-- Production PrestaShop post-import config (applied after the DB dump, run through envsubst).
-- Mirrors init.local.sql, but ENFORCES HTTPS for prod and intentionally leaves the shop's
-- real mail configuration (from the imported dump) untouched — prod must deliver through a
-- real MTA, not the local mailpit catcher.
--
-- NOTE: the %{DOMAIN} placeholder matches the convention used in init.local.sql. See
-- config/sql/ReadMe.md / doc/ARCHITECTURE.md for how the value is substituted.
UPDATE `ps_configuration` SET `value` = '1' WHERE `name` LIKE 'PS_SSL_ENABLED';
UPDATE `ps_configuration` SET `value` = '1' WHERE `name` LIKE 'PS_SSL_ENABLED_EVERYWHERE';
UPDATE `ps_configuration` SET `value` = '%{DOMAIN}' WHERE `name` LIKE 'PS_SHOP_DOMAIN';
UPDATE `ps_configuration` SET `value` = '%{DOMAIN}' WHERE `name` LIKE 'PS_SHOP_DOMAIN_SSL';

UPDATE `ps_shop_url` SET `domain`       = '%{DOMAIN}';
UPDATE `ps_shop_url` SET `domain_ssl`   = '%{DOMAIN}';
UPDATE `ps_shop_url` SET `physical_uri` = '/';
UPDATE `ps_shop_url` SET `virtual_uri`  = '';
