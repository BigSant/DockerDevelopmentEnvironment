-- Setup safe default after any DB load outside prod: known payment modules switch to their sandbox.
-- Only existing keys change; a module that is not installed is not affected. Project after-import
-- scripts handle other payment modules.
UPDATE `${DB_PREFIX}configuration` SET `value` = '1' WHERE `name` IN ('PAYPAL_SANDBOX', 'PAYSERA_GENERAL_TEST_MODE');
UPDATE `${DB_PREFIX}configuration` SET `value` = 'SANDBOX' WHERE `name` = 'PS_CHECKOUT_MODE';
UPDATE `${DB_PREFIX}configuration` SET `value` = 'test' WHERE `name` = 'ADYEN_MODE';
