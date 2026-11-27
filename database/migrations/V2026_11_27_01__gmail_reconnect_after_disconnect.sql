-- =====================================================================
-- Migration V2026.11.27-01: a disconnected Gmail address can be connected by another DMS user
--
-- Before: gmail_accounts.email was globally UNIQUE, so once a user had connected an address no other
--         DMS user could ever connect it, even after it was disconnected.
-- After:  UNIQUE (email, user_id). sp_gmail_account_connect still refuses an address while another user
--         has it ACTIVE; after a disconnect the new user gets a row of their own (fresh sync) and the
--         previous owner's archived emails stay with the previous owner.
--
-- No data is changed. Existing rows already satisfy the new key (email was unique).
-- `flask install-procedures` (database/procedures.sql) applies the same change and the new procedure
-- automatically; run this script only where the application account may not ALTER tables.
--
--   mysql -u <dba> -p DMS < database/migrations/V2026_11_27_01__gmail_reconnect_after_disconnect.sql
--   then: flask install-procedures   (installs the updated sp_gmail_account_connect)
-- Rollback: database/migrations/V2026_11_27_01__gmail_reconnect_after_disconnect.rollback.sql
-- =====================================================================
DELIMITER $$

DROP PROCEDURE IF EXISTS mig_gmail_reconnect$$
CREATE PROCEDURE mig_gmail_reconnect()
BEGIN
  DECLARE v_name VARCHAR(64) DEFAULT NULL;
  IF NOT EXISTS (SELECT 1 FROM information_schema.statistics
                  WHERE table_schema = DATABASE() AND table_name = 'gmail_accounts'
                    AND index_name = 'uq_gmail_accounts_email_user') THEN
    ALTER TABLE gmail_accounts ADD UNIQUE KEY uq_gmail_accounts_email_user (email, user_id);
  END IF;

  SELECT index_name INTO v_name FROM information_schema.statistics
   WHERE table_schema = DATABASE() AND table_name = 'gmail_accounts' AND non_unique = 0
     AND index_name <> 'PRIMARY'
   GROUP BY index_name
  HAVING COUNT(*) = 1 AND MAX(column_name) = 'email'
   LIMIT 1;
  IF v_name IS NOT NULL THEN
    SET @ddl = CONCAT('ALTER TABLE gmail_accounts DROP INDEX `', v_name, '`');
    PREPARE stmt FROM @ddl;
    EXECUTE stmt;
    DEALLOCATE PREPARE stmt;
  END IF;
END$$

CALL mig_gmail_reconnect()$$
DROP PROCEDURE IF EXISTS mig_gmail_reconnect$$

DELIMITER ;
