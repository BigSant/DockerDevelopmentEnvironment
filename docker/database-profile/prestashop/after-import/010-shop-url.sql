-- Setup safe default after any DB load outside prod: the shop answers on this environment's domain.
-- ${DOMAIN}, ${DB_PREFIX} and ${PS_SSL} come from the project env (PS_SSL=on|off, default on).
UPDATE `${DB_PREFIX}configuration` SET `value` = '${DOMAIN}'
WHERE `name` IN ('PS_SHOP_DOMAIN', 'PS_SHOP_DOMAIN_SSL');

UPDATE `${DB_PREFIX}configuration` SET `value` = '${PS_SSL}'
WHERE `name` IN ('PS_SSL_ENABLED', 'PS_SSL_ENABLED_EVERYWHERE');

-- Only the default shop: other shops of a multistore keep their own URLs.
UPDATE `${DB_PREFIX}shop_url` SET `domain` = '${DOMAIN}', `domain_ssl` = '${DOMAIN}', `physical_uri` = '/', `virtual_uri` = ''
WHERE `main` = 1 AND `id_shop` = (SELECT `value` FROM (SELECT `value` FROM `${DB_PREFIX}configuration`
                                   WHERE `name` = 'PS_SHOP_DEFAULT' ORDER BY `id_configuration` LIMIT 1) AS `default_shop`);
