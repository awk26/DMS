-- =====================================================================
-- DMS stored procedures (MySQL 8.0.19+). Python calls them through dms/sp.py.
-- Install: `flask --app app install-procedures`, or in Workbench:
--          SQL Editor tab -> paste -> Ctrl+Shift+Enter (Execute All)
-- Is script ko dobara chalana safe hai (DROP ... IF EXISTS).
-- =====================================================================
USE DMS;

DELIMITER $$

-- ------------------------------------------------- extra indexes (sirf tab banenge jab na hon)
DROP PROCEDURE IF EXISTS sp__add_index_if_missing$$
CREATE PROCEDURE sp__add_index_if_missing(IN p_table VARCHAR(64), IN p_index VARCHAR(64), IN p_cols VARCHAR(255))
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.statistics
                 WHERE table_schema = DATABASE() AND table_name = p_table AND index_name = p_index) THEN
    SET @ddl = CONCAT('ALTER TABLE `', p_table, '` ADD KEY `', p_index, '` (', p_cols, ')');
    PREPARE stmt FROM @ddl;
    EXECUTE stmt;
    DEALLOCATE PREPARE stmt;
  END IF;
END$$

CALL sp__add_index_if_missing('folders', 'ix_folders_parent_live', 'parent_id, deleted_at, name')$$
CALL sp__add_index_if_missing('documents', 'ix_documents_folder_live', 'folder_id, deleted_at, name')$$
CALL sp__add_index_if_missing('archived_emails', 'ix_archived_emails_account_imported', 'account_id, imported_at')$$
DROP PROCEDURE IF EXISTS sp__add_index_if_missing$$


-- ------------------------------------------------- Gmail: har user ki apni mailbox (schema upgrade)
-- gmail_accounts.user_id = mailbox kis DMS user ki hai; token_enc = us user ka encrypted OAuth token.
DROP PROCEDURE IF EXISTS sp__ddl_if_missing$$
CREATE PROCEDURE sp__ddl_if_missing(IN p_kind VARCHAR(10), IN p_table VARCHAR(64), IN p_name VARCHAR(64),
                                    IN p_ddl TEXT)
BEGIN
  DECLARE v_have INT DEFAULT 0;
  IF p_kind = 'column' THEN
    SELECT COUNT(*) INTO v_have FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = p_table AND column_name = p_name;
  ELSEIF p_kind = 'fk' THEN
    SELECT COUNT(*) INTO v_have FROM information_schema.table_constraints
    WHERE table_schema = DATABASE() AND table_name = p_table AND constraint_name = p_name;
  ELSE
    SELECT COUNT(*) INTO v_have FROM information_schema.statistics
    WHERE table_schema = DATABASE() AND table_name = p_table AND index_name = p_name;
  END IF;
  IF v_have = 0 THEN
    SET @ddl = p_ddl;
    PREPARE stmt FROM @ddl;
    EXECUTE stmt;
    DEALLOCATE PREPARE stmt;
  END IF;
END$$

CALL sp__ddl_if_missing('column', 'gmail_accounts', 'user_id',
  'ALTER TABLE gmail_accounts ADD COLUMN user_id INT NULL AFTER email')$$
CALL sp__ddl_if_missing('column', 'gmail_accounts', 'token_enc',
  'ALTER TABLE gmail_accounts ADD COLUMN token_enc TEXT NULL AFTER user_id')$$
CALL sp__ddl_if_missing('index', 'gmail_accounts', 'ix_gmail_accounts_user_id',
  'ALTER TABLE gmail_accounts ADD KEY ix_gmail_accounts_user_id (user_id)')$$
CALL sp__ddl_if_missing('fk', 'gmail_accounts', 'fk_gmail_accounts_user',
  'ALTER TABLE gmail_accounts ADD CONSTRAINT fk_gmail_accounts_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE SET NULL')$$
DROP PROCEDURE IF EXISTS sp__ddl_if_missing$$

-- Jis mailbox ka koi maalik nahi (purana single-mailbox setup), wo na sync hogi na kisi ko dikhegi,
-- jab tak uska maalik DMS mein login karke wahi Gmail dobara connect na kare.
UPDATE gmail_accounts SET is_active = 0 WHERE user_id IS NULL$$


-- ================================================================= 1. ACTIVITY LOG
DROP PROCEDURE IF EXISTS sp_activity_log_add$$
CREATE PROCEDURE sp_activity_log_add(
  IN p_category VARCHAR(20), IN p_action VARCHAR(40), IN p_message VARCHAR(500),
  IN p_entity_id INT, IN p_user_id INT, IN p_user_name VARCHAR(200), IN p_ip VARCHAR(64))
BEGIN
  INSERT INTO activity_log (category, action, message, entity_id, user_id, user_name, ip, created_at)
  VALUES (p_category, p_action, COALESCE(LEFT(p_message, 500), ''), p_entity_id, p_user_id,
          p_user_name, p_ip, UTC_TIMESTAMP(6));
END$$

-- @audit-begin  (this block is also installed automatically on app start-up when missing: dms/sp.py)
-- Audit entry with the affected entity and what changed (details = JSON). Used by dms/services/activity.py.
DROP PROCEDURE IF EXISTS sp_audit_log_add$$
CREATE PROCEDURE sp_audit_log_add(
  IN p_category VARCHAR(20), IN p_action VARCHAR(40), IN p_message VARCHAR(500),
  IN p_entity_type VARCHAR(20), IN p_entity_id INT, IN p_entity_name VARCHAR(500), IN p_details TEXT,
  IN p_user_id INT, IN p_user_name VARCHAR(200), IN p_ip VARCHAR(64))
BEGIN
  INSERT INTO activity_log (category, action, message, entity_type, entity_id, entity_name, details_json,
                            user_id, user_name, ip, created_at)
  VALUES (p_category, p_action, COALESCE(LEFT(p_message, 500), ''), p_entity_type, p_entity_id,
          LEFT(p_entity_name, 500), p_details, p_user_id, p_user_name, p_ip, UTC_TIMESTAMP(6));
END$$

-- The audit log is append-only: existing entries can never be changed or removed.
DROP TRIGGER IF EXISTS trg_activity_log_no_update$$
CREATE TRIGGER trg_activity_log_no_update BEFORE UPDATE ON activity_log FOR EACH ROW
BEGIN
  SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Audit log entries cannot be modified.';
END$$

DROP TRIGGER IF EXISTS trg_activity_log_no_delete$$
CREATE TRIGGER trg_activity_log_no_delete BEFORE DELETE ON activity_log FOR EACH ROW
BEGIN
  SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Audit log entries cannot be deleted.';
END$$
-- @audit-end


-- ================================================================= 2. USERS / LOGIN
DROP PROCEDURE IF EXISTS sp_user_find_by_login$$
CREATE PROCEDURE sp_user_find_by_login(IN p_login VARCHAR(320))
BEGIN
  DECLARE v_login VARCHAR(320) DEFAULT LOWER(TRIM(COALESCE(p_login, '')));
  SELECT id, username, email, display_name, password_hash, auth_source, is_active,
         mfa_enabled, failed_logins, locked_until, last_login_at, home_folder_id
  FROM users
  WHERE v_login <> '' AND (username = v_login OR email = v_login)
  LIMIT 1;
END$$

DROP PROCEDURE IF EXISTS sp_user_create$$
CREATE PROCEDURE sp_user_create(
  IN p_username VARCHAR(150), IN p_email VARCHAR(320), IN p_display_name VARCHAR(200),
  IN p_password_hash VARCHAR(255), IN p_auth_source VARCHAR(20), IN p_role_name VARCHAR(80))
BEGIN
  DECLARE v_username VARCHAR(150) DEFAULT LOWER(TRIM(COALESCE(p_username, '')));
  DECLARE v_email    VARCHAR(320) DEFAULT NULLIF(LOWER(TRIM(COALESCE(p_email, ''))), '');
  DECLARE v_role_id  INT DEFAULT NULL;
  DECLARE v_user_id  INT DEFAULT NULL;
  DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN ROLLBACK; RESIGNAL; END;

  IF v_username = '' THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Choose a user name.';
  END IF;
  -- p_role_name NULL = koi role nahi
  IF p_role_name IS NOT NULL THEN
    SELECT id INTO v_role_id FROM roles WHERE name = p_role_name LIMIT 1;
    IF v_role_id IS NULL THEN
      SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'The requested role is unavailable.';
    END IF;
  END IF;

  START TRANSACTION;
  IF EXISTS (SELECT 1 FROM users
             WHERE username IN (v_username, v_email) OR email IN (v_username, v_email)) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'An account with this user name or email already exists.';
  END IF;
  INSERT INTO users (username, email, display_name, password_hash, auth_source, is_active, mfa_enabled,
                     mfa_recovery_json, failed_logins, created_at)
  VALUES (v_username, v_email, LEFT(COALESCE(p_display_name, ''), 200), p_password_hash,
          COALESCE(p_auth_source, 'local'), 1, 0, '[]', 0, UTC_TIMESTAMP(6));
  SET v_user_id = LAST_INSERT_ID();
  IF v_role_id IS NOT NULL THEN
    INSERT INTO user_roles (user_id, role_id) VALUES (v_user_id, v_role_id);
  END IF;
  COMMIT;
  SELECT v_user_id AS user_id;
END$$

DROP PROCEDURE IF EXISTS sp_user_login_success$$
CREATE PROCEDURE sp_user_login_success(IN p_user_id INT, IN p_method VARCHAR(20), IN p_ip VARCHAR(64))
BEGIN
  DECLARE v_username VARCHAR(150) DEFAULT NULL;
  DECLARE v_label VARCHAR(200) DEFAULT NULL;
  UPDATE users SET failed_logins = 0, locked_until = NULL, last_login_at = UTC_TIMESTAMP(6)
  WHERE id = p_user_id;
  SELECT username, COALESCE(NULLIF(display_name, ''), username) INTO v_username, v_label
  FROM users WHERE id = p_user_id;
  IF v_username IS NULL THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'User not found.';
  END IF;
  CALL sp_activity_log_add('security', 'login',
       CONCAT('''', v_username, ''' signed in (', COALESCE(p_method, 'password'), ')'),
       p_user_id, p_user_id, v_label, p_ip);
END$$

-- Threshold / minutes Python settings se bhejega (default 5 / 15)
DROP PROCEDURE IF EXISTS sp_user_login_failed$$
CREATE PROCEDURE sp_user_login_failed(IN p_user_id INT, IN p_threshold INT, IN p_lock_minutes INT,
                                      IN p_ip VARCHAR(64))
BEGIN
  DECLARE v_failed INT DEFAULT NULL;
  DECLARE v_username VARCHAR(150) DEFAULT NULL;
  DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN ROLLBACK; RESIGNAL; END;

  START TRANSACTION;
  SELECT failed_logins + 1, username INTO v_failed, v_username
  FROM users WHERE id = p_user_id FOR UPDATE;
  IF v_failed IS NULL THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'User not found.';
  END IF;
  IF v_failed >= p_threshold THEN
    UPDATE users SET failed_logins = 0,
                     locked_until = UTC_TIMESTAMP(6) + INTERVAL p_lock_minutes MINUTE
    WHERE id = p_user_id;
    CALL sp_activity_log_add('security', 'account_locked',
         CONCAT('Account ''', v_username, ''' locked after failed sign-ins'), p_user_id, NULL, NULL, p_ip);
  ELSE
    UPDATE users SET failed_logins = v_failed WHERE id = p_user_id;
  END IF;
  COMMIT;
  SELECT failed_logins, locked_until FROM users WHERE id = p_user_id;
END$$

DROP PROCEDURE IF EXISTS sp_user_get_roles$$
CREATE PROCEDURE sp_user_get_roles(IN p_user_id INT)
BEGIN
  SELECT r.id, r.name, r.capabilities, r.is_system
  FROM roles r JOIN user_roles ur ON ur.role_id = r.id
  WHERE ur.user_id = p_user_id
  ORDER BY r.name;
END$$

DROP PROCEDURE IF EXISTS sp_user_set_password$$
CREATE PROCEDURE sp_user_set_password(IN p_user_id INT, IN p_password_hash VARCHAR(255), IN p_ip VARCHAR(64))
BEGIN
  DECLARE v_username VARCHAR(150) DEFAULT NULL;
  DECLARE v_label VARCHAR(200) DEFAULT NULL;
  SELECT username, COALESCE(NULLIF(display_name, ''), username) INTO v_username, v_label
  FROM users WHERE id = p_user_id;
  IF v_username IS NULL THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'User not found.';
  END IF;
  UPDATE users SET password_hash = p_password_hash, failed_logins = 0, locked_until = NULL
  WHERE id = p_user_id;
  CALL sp_activity_log_add('security', 'password_changed',
       CONCAT('''', v_username, ''' changed their password'), p_user_id, p_user_id, v_label, p_ip);
END$$


-- ================================================================= 3. FOLDERS
DROP PROCEDURE IF EXISTS sp_folder_create$$
CREATE PROCEDURE sp_folder_create(
  IN p_name VARCHAR(255), IN p_parent_id INT, IN p_user_id INT, IN p_user_name VARCHAR(200))
BEGIN
  DECLARE v_name VARCHAR(255) DEFAULT TRIM(COALESCE(p_name, ''));
  DECLARE v_id INT DEFAULT NULL;
  DECLARE v_now DATETIME(6) DEFAULT UTC_TIMESTAMP(6);
  DECLARE v_msg VARCHAR(500);
  DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN ROLLBACK; RESIGNAL; END;

  IF v_name = '' THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Enter a folder name.';
  END IF;

  START TRANSACTION;
  IF p_parent_id IS NOT NULL AND NOT EXISTS
     (SELECT 1 FROM folders WHERE id = p_parent_id AND deleted_at IS NULL) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'The parent folder does not exist.';
  END IF;
  IF EXISTS (SELECT 1 FROM folders
             WHERE parent_id <=> p_parent_id AND name = v_name AND deleted_at IS NULL) THEN
    SET v_msg = CONCAT('A folder named ''', v_name, ''' already exists here.');
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = v_msg;
  END IF;

  INSERT INTO folders (name, parent_id, created_by_id, created_at, updated_at)
  VALUES (v_name, p_parent_id, p_user_id, v_now, v_now);
  SET v_id = LAST_INSERT_ID();
  INSERT INTO folder_activity (folder_id, folder_name, action, details_json, user_name, created_at)
  VALUES (v_id, v_name, 'created', JSON_OBJECT('parent_id', p_parent_id), p_user_name, v_now);
  COMMIT;
  SELECT v_id AS folder_id;
END$$

DROP PROCEDURE IF EXISTS sp_folder_rename$$
CREATE PROCEDURE sp_folder_rename(IN p_folder_id INT, IN p_new_name VARCHAR(255), IN p_user_name VARCHAR(200))
BEGIN
  DECLARE v_name VARCHAR(255) DEFAULT TRIM(COALESCE(p_new_name, ''));
  DECLARE v_old VARCHAR(255) DEFAULT NULL;
  DECLARE v_parent INT DEFAULT NULL;
  DECLARE v_msg VARCHAR(500);
  DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN ROLLBACK; RESIGNAL; END;

  IF v_name = '' THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Enter a folder name.';
  END IF;

  START TRANSACTION;
  SELECT name, parent_id INTO v_old, v_parent
  FROM folders WHERE id = p_folder_id AND deleted_at IS NULL FOR UPDATE;
  IF v_old IS NULL THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Folder not found.';
  END IF;
  IF EXISTS (SELECT 1 FROM folders WHERE parent_id <=> v_parent AND name = v_name
             AND deleted_at IS NULL AND id <> p_folder_id) THEN
    SET v_msg = CONCAT('A folder named ''', v_name, ''' already exists here.');
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = v_msg;
  END IF;
  UPDATE folders SET name = v_name, updated_at = UTC_TIMESTAMP(6) WHERE id = p_folder_id;
  INSERT INTO folder_activity (folder_id, folder_name, action, details_json, user_name, created_at)
  VALUES (p_folder_id, v_name, 'renamed', JSON_OBJECT('from', v_old, 'to', v_name), p_user_name, UTC_TIMESTAMP(6));
  COMMIT;
  SELECT p_folder_id AS folder_id, v_name AS name;
END$$

-- p_parent_id NULL = root folders
DROP PROCEDURE IF EXISTS sp_folder_list_subfolders$$
CREATE PROCEDURE sp_folder_list_subfolders(IN p_parent_id INT)
BEGIN
  SELECT f.id, f.name, f.inherit_permissions, f.created_at, f.updated_at,
         (SELECT COUNT(*) FROM folders c WHERE c.parent_id = f.id AND c.deleted_at IS NULL) AS sub_count
  FROM folders f
  WHERE f.parent_id <=> p_parent_id AND f.deleted_at IS NULL
  ORDER BY f.name;
END$$

DROP PROCEDURE IF EXISTS sp_folder_list_documents$$
CREATE PROCEDURE sp_folder_list_documents(IN p_folder_id INT, IN p_limit INT, IN p_offset INT)
BEGIN
  SELECT d.id, d.name, d.extension, d.document_type_id, d.created_at, d.updated_at,
         d.checked_out_by_id, d.checked_out_at,
         v.id AS version_id, v.major, v.minor, v.size, v.mime_type, v.scan_status,
         u.display_name AS created_by_name
  FROM documents d
  LEFT JOIN document_versions v ON v.id = d.current_version_id
  LEFT JOIN users u ON u.id = d.created_by_id
  WHERE d.folder_id <=> p_folder_id AND d.deleted_at IS NULL
  ORDER BY d.name
  LIMIT p_limit OFFSET p_offset;
END$$

-- Folder + saare subfolders + unke documents trash mein. Restore ke liye batch id lautata hai.
DROP PROCEDURE IF EXISTS sp_folder_soft_delete$$
CREATE PROCEDURE sp_folder_soft_delete(IN p_folder_id INT, IN p_user_id INT, IN p_user_name VARCHAR(200))
BEGIN
  DECLARE v_batch VARCHAR(32) DEFAULT REPLACE(UUID(), '-', '');
  DECLARE v_now DATETIME(6) DEFAULT UTC_TIMESTAMP(6);
  DECLARE v_name VARCHAR(255) DEFAULT NULL;
  DECLARE EXIT HANDLER FOR SQLEXCEPTION
  BEGIN
    ROLLBACK;
    DROP TEMPORARY TABLE IF EXISTS tmp_del_folders;
    RESIGNAL;
  END;

  SELECT name INTO v_name FROM folders WHERE id = p_folder_id AND deleted_at IS NULL;
  IF v_name IS NULL THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Folder not found.';
  END IF;

  DROP TEMPORARY TABLE IF EXISTS tmp_del_folders;
  CREATE TEMPORARY TABLE tmp_del_folders (id INT PRIMARY KEY);
  INSERT INTO tmp_del_folders (id)
    WITH RECURSIVE tree AS (
      SELECT id FROM folders WHERE id = p_folder_id
      UNION ALL
      SELECT c.id FROM folders c JOIN tree t ON c.parent_id = t.id WHERE c.deleted_at IS NULL
    )
    SELECT id FROM tree;

  IF EXISTS (SELECT 1 FROM documents d JOIN tmp_del_folders t ON t.id = d.folder_id
             WHERE d.deleted_at IS NULL AND d.checked_out_by_id IS NOT NULL
               AND d.checked_out_by_id <> COALESCE(p_user_id, 0)) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'A document in this folder is checked out by another user.';
  END IF;

  START TRANSACTION;
  UPDATE folders f JOIN tmp_del_folders t ON t.id = f.id
     SET f.deleted_at = v_now, f.deleted_batch = v_batch, f.deleted_by_id = p_user_id;
  UPDATE documents d JOIN tmp_del_folders t ON t.id = d.folder_id
     SET d.deleted_at = v_now, d.deleted_batch = v_batch, d.deleted_by_id = p_user_id
   WHERE d.deleted_at IS NULL;
  INSERT INTO folder_activity (folder_id, folder_name, action, details_json, user_name, created_at)
  VALUES (p_folder_id, v_name, 'deleted', JSON_OBJECT('batch', v_batch), p_user_name, v_now);
  COMMIT;

  DROP TEMPORARY TABLE IF EXISTS tmp_del_folders;
  SELECT v_batch AS deleted_batch;
END$$


-- ================================================================= 4. DOCUMENTS / VERSIONS
-- Python pehle file encrypt karke storage mein likhe, phir ye SP call kare.
DROP PROCEDURE IF EXISTS sp_document_create$$
CREATE PROCEDURE sp_document_create(
  IN p_folder_id INT, IN p_name VARCHAR(255), IN p_extension VARCHAR(16), IN p_document_type_id INT,
  IN p_user_id INT, IN p_user_name VARCHAR(200),
  IN p_storage_key VARCHAR(255), IN p_original_filename VARCHAR(255), IN p_mime_type VARCHAR(127),
  IN p_size BIGINT, IN p_sha256 VARCHAR(64), IN p_note VARCHAR(500), IN p_idempotency_key VARCHAR(64),
  IN p_scan_status VARCHAR(20), IN p_scan_engine VARCHAR(40))
proc: BEGIN
  DECLARE v_doc_id INT DEFAULT NULL;
  DECLARE v_ver_id INT DEFAULT NULL;
  DECLARE v_now DATETIME(6) DEFAULT UTC_TIMESTAMP(6);
  DECLARE v_msg VARCHAR(500);
  DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN ROLLBACK; RESIGNAL; END;

  -- Same request dobara aaye (network retry) to wahi document lautao
  IF p_idempotency_key IS NOT NULL THEN
    SELECT document_id, id INTO v_doc_id, v_ver_id
    FROM document_versions WHERE idempotency_key = p_idempotency_key LIMIT 1;
    IF v_ver_id IS NOT NULL THEN
      SELECT v_doc_id AS document_id, v_ver_id AS version_id, 1 AS was_duplicate;
      LEAVE proc;
    END IF;
  END IF;

  START TRANSACTION;
  IF p_folder_id IS NOT NULL AND NOT EXISTS
     (SELECT 1 FROM folders WHERE id = p_folder_id AND deleted_at IS NULL) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'The folder does not exist.';
  END IF;
  IF EXISTS (SELECT 1 FROM documents
             WHERE folder_id <=> p_folder_id AND name = p_name AND deleted_at IS NULL) THEN
    SET v_msg = CONCAT('A document named ''', p_name, ''' already exists in this folder.');
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = v_msg;
  END IF;

  INSERT INTO documents (name, extension, folder_id, document_type_id, created_by_id, created_at, updated_at)
  VALUES (p_name, COALESCE(p_extension, ''), p_folder_id, p_document_type_id, p_user_id, v_now, v_now);
  SET v_doc_id = LAST_INSERT_ID();

  INSERT INTO document_versions (document_id, version_number, major, minor, storage_key, original_filename,
         mime_type, size, sha256, note, idempotency_key, created_by_id, scan_status, scan_engine, created_at)
  VALUES (v_doc_id, 1, 1, 0, p_storage_key, p_original_filename, p_mime_type, p_size, p_sha256, p_note,
          p_idempotency_key, p_user_id, COALESCE(p_scan_status, 'clean'), p_scan_engine, v_now);
  SET v_ver_id = LAST_INSERT_ID();

  UPDATE documents SET current_version_id = v_ver_id WHERE id = v_doc_id;
  CALL sp_activity_log_add('document', 'upload', CONCAT('Uploaded ''', p_name, ''''),
                           v_doc_id, p_user_id, p_user_name, NULL);
  COMMIT;
  SELECT v_doc_id AS document_id, v_ver_id AS version_id, 0 AS was_duplicate;
END$$

-- p_is_major = 1 -> 2.0 jaisa, 0 -> 1.1 jaisa
DROP PROCEDURE IF EXISTS sp_document_add_version$$
CREATE PROCEDURE sp_document_add_version(
  IN p_document_id INT, IN p_is_major TINYINT, IN p_user_id INT, IN p_user_name VARCHAR(200),
  IN p_storage_key VARCHAR(255), IN p_original_filename VARCHAR(255), IN p_mime_type VARCHAR(127),
  IN p_size BIGINT, IN p_sha256 VARCHAR(64), IN p_note VARCHAR(500), IN p_idempotency_key VARCHAR(64),
  IN p_scan_status VARCHAR(20), IN p_scan_engine VARCHAR(40))
proc: BEGIN
  DECLARE v_name VARCHAR(255) DEFAULT NULL;
  DECLARE v_locked_by INT DEFAULT NULL;
  DECLARE v_next INT DEFAULT 1;
  DECLARE v_major INT DEFAULT NULL;
  DECLARE v_minor INT DEFAULT NULL;
  DECLARE v_ver_id INT DEFAULT NULL;
  DECLARE v_now DATETIME(6) DEFAULT UTC_TIMESTAMP(6);
  DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN ROLLBACK; RESIGNAL; END;

  IF p_idempotency_key IS NOT NULL THEN
    SELECT id INTO v_ver_id FROM document_versions WHERE idempotency_key = p_idempotency_key LIMIT 1;
    IF v_ver_id IS NOT NULL THEN
      SELECT p_document_id AS document_id, v_ver_id AS version_id, 1 AS was_duplicate;
      LEAVE proc;
    END IF;
  END IF;

  START TRANSACTION;
  -- Row lock: do log ek saath upload karein to version number nahi takrayega
  SELECT name, checked_out_by_id INTO v_name, v_locked_by
  FROM documents WHERE id = p_document_id AND deleted_at IS NULL FOR UPDATE;
  IF v_name IS NULL THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Document not found.';
  END IF;
  IF v_locked_by IS NOT NULL AND v_locked_by <> COALESCE(p_user_id, 0) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This document is checked out by another user.';
  END IF;

  SELECT COALESCE(MAX(version_number), 0) + 1 INTO v_next
  FROM document_versions WHERE document_id = p_document_id;
  SELECT COALESCE(v.major, v.version_number), COALESCE(v.minor, 0) INTO v_major, v_minor
  FROM documents d JOIN document_versions v ON v.id = d.current_version_id
  WHERE d.id = p_document_id;
  SET v_major = COALESCE(v_major, 0);
  SET v_minor = COALESCE(v_minor, 0);
  IF p_is_major = 1 THEN
    SET v_major = v_major + 1;
    SET v_minor = 0;
  ELSE
    SET v_minor = v_minor + 1;
  END IF;

  INSERT INTO document_versions (document_id, version_number, major, minor, storage_key, original_filename,
         mime_type, size, sha256, note, idempotency_key, created_by_id, scan_status, scan_engine, created_at)
  VALUES (p_document_id, v_next, v_major, v_minor, p_storage_key, p_original_filename, p_mime_type, p_size,
          p_sha256, p_note, p_idempotency_key, p_user_id, COALESCE(p_scan_status, 'clean'), p_scan_engine, v_now);
  SET v_ver_id = LAST_INSERT_ID();

  UPDATE documents SET current_version_id = v_ver_id, updated_at = v_now WHERE id = p_document_id;
  CALL sp_activity_log_add('document', 'new_version',
       CONCAT('New version ', v_major, '.', v_minor, ' of ''', v_name, ''''),
       p_document_id, p_user_id, p_user_name, NULL);
  COMMIT;
  SELECT p_document_id AS document_id, v_ver_id AS version_id, v_major AS major, v_minor AS minor,
         0 AS was_duplicate;
END$$

DROP PROCEDURE IF EXISTS sp_document_get$$
CREATE PROCEDURE sp_document_get(IN p_document_id INT)
BEGIN
  SELECT d.id, d.name, d.extension, d.folder_id, d.current_version_id, d.document_type_id,
         d.inherit_permissions, d.checked_out_by_id, d.checked_out_at, d.deleted_at,
         d.created_by_id, d.created_at, d.updated_at,
         f.name AS folder_name,
         v.version_number, v.major, v.minor, v.storage_key, v.original_filename, v.mime_type,
         v.size, v.sha256, v.scan_status,
         cu.display_name AS created_by_name, co.display_name AS checked_out_by_name
  FROM documents d
  LEFT JOIN folders f           ON f.id  = d.folder_id
  LEFT JOIN document_versions v ON v.id  = d.current_version_id
  LEFT JOIN users cu            ON cu.id = d.created_by_id
  LEFT JOIN users co            ON co.id = d.checked_out_by_id
  WHERE d.id = p_document_id;
END$$

DROP PROCEDURE IF EXISTS sp_document_list_versions$$
CREATE PROCEDURE sp_document_list_versions(IN p_document_id INT)
BEGIN
  SELECT v.id, v.version_number, v.major, v.minor, v.original_filename, v.mime_type, v.size,
         v.note, v.scan_status, v.restored_from, v.created_at, u.display_name AS created_by_name
  FROM document_versions v
  LEFT JOIN users u ON u.id = v.created_by_id
  WHERE v.document_id = p_document_id
  ORDER BY v.version_number DESC;
END$$

DROP PROCEDURE IF EXISTS sp_document_checkout$$
CREATE PROCEDURE sp_document_checkout(IN p_document_id INT, IN p_user_id INT, IN p_user_name VARCHAR(200))
BEGIN
  UPDATE documents SET checked_out_by_id = p_user_id, checked_out_at = UTC_TIMESTAMP(6)
  WHERE id = p_document_id AND deleted_at IS NULL AND checked_out_by_id IS NULL;
  IF ROW_COUNT() = 0 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This document is already checked out or does not exist.';
  END IF;
  CALL sp_activity_log_add('document', 'checkout', 'Checked out', p_document_id, p_user_id, p_user_name, NULL);
END$$

-- p_is_admin = 1 -> admin kisi aur ka lock bhi khol sakta hai
DROP PROCEDURE IF EXISTS sp_document_checkin$$
CREATE PROCEDURE sp_document_checkin(IN p_document_id INT, IN p_user_id INT, IN p_is_admin TINYINT,
                                     IN p_user_name VARCHAR(200))
BEGIN
  UPDATE documents SET checked_out_by_id = NULL, checked_out_at = NULL
  WHERE id = p_document_id AND checked_out_by_id IS NOT NULL
    AND (checked_out_by_id = p_user_id OR p_is_admin = 1);
  IF ROW_COUNT() = 0 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This document is not checked out by you.';
  END IF;
  CALL sp_activity_log_add('document', 'checkin', 'Checked in', p_document_id, p_user_id, p_user_name, NULL);
END$$

DROP PROCEDURE IF EXISTS sp_document_soft_delete$$
CREATE PROCEDURE sp_document_soft_delete(IN p_document_id INT, IN p_user_id INT, IN p_user_name VARCHAR(200))
BEGIN
  DECLARE v_batch VARCHAR(32) DEFAULT REPLACE(UUID(), '-', '');
  DECLARE v_name VARCHAR(255) DEFAULT NULL;
  DECLARE v_locked_by INT DEFAULT NULL;
  DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN ROLLBACK; RESIGNAL; END;

  START TRANSACTION;
  SELECT name, checked_out_by_id INTO v_name, v_locked_by
  FROM documents WHERE id = p_document_id AND deleted_at IS NULL FOR UPDATE;
  IF v_name IS NULL THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Document not found.';
  END IF;
  IF v_locked_by IS NOT NULL AND v_locked_by <> COALESCE(p_user_id, 0) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This document is checked out by another user.';
  END IF;
  UPDATE documents SET deleted_at = UTC_TIMESTAMP(6), deleted_batch = v_batch, deleted_by_id = p_user_id
  WHERE id = p_document_id;
  CALL sp_activity_log_add('document', 'delete', CONCAT('Moved ''', v_name, ''' to trash'),
                           p_document_id, p_user_id, p_user_name, NULL);
  COMMIT;
  SELECT v_batch AS deleted_batch;
END$$


-- ================================================================= 5. TRASH RESTORE
DROP PROCEDURE IF EXISTS sp_trash_restore$$
CREATE PROCEDURE sp_trash_restore(IN p_batch VARCHAR(32), IN p_user_id INT, IN p_user_name VARCHAR(200))
BEGIN
  DECLARE v_folders INT DEFAULT 0;
  DECLARE v_docs INT DEFAULT 0;
  DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN ROLLBACK; RESIGNAL; END;

  START TRANSACTION;
  UPDATE folders SET deleted_at = NULL, deleted_batch = NULL, deleted_by_id = NULL
  WHERE deleted_batch = p_batch;
  SET v_folders = ROW_COUNT();
  UPDATE documents SET deleted_at = NULL, deleted_batch = NULL, deleted_by_id = NULL
  WHERE deleted_batch = p_batch;
  SET v_docs = ROW_COUNT();
  IF v_folders + v_docs = 0 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Nothing to restore for this item.';
  END IF;
  CALL sp_activity_log_add('document', 'restore',
       CONCAT('Restored ', v_folders, ' folder(s) and ', v_docs, ' document(s) from trash'),
       NULL, p_user_id, p_user_name, NULL);
  COMMIT;
  SELECT v_folders AS folders_restored, v_docs AS documents_restored;
END$$


-- ================================================================= 6. SEARCH
DROP PROCEDURE IF EXISTS sp_search_upsert$$
CREATE PROCEDURE sp_search_upsert(IN p_kind VARCHAR(10), IN p_ref_id INT,
                                  IN p_title TEXT, IN p_body MEDIUMTEXT, IN p_meta TEXT)
BEGIN
  INSERT INTO search_entries (kind, ref_id, title, body, meta, updated_at)
  VALUES (p_kind, p_ref_id, COALESCE(p_title, ''), COALESCE(p_body, ''), COALESCE(p_meta, ''), UTC_TIMESTAMP(6))
  AS new
  ON DUPLICATE KEY UPDATE title = new.title, body = new.body, meta = new.meta, updated_at = new.updated_at;
END$$

DROP PROCEDURE IF EXISTS sp_search_delete$$
CREATE PROCEDURE sp_search_delete(IN p_kind VARCHAR(10), IN p_ref_id INT)
BEGIN
  DELETE FROM search_entries WHERE kind = p_kind AND ref_id = p_ref_id;
END$$

-- p_kind NULL = sab, 'document' ya 'email'. Permission filter Python karega.
DROP PROCEDURE IF EXISTS sp_search$$
CREATE PROCEDURE sp_search(IN p_query VARCHAR(500), IN p_kind VARCHAR(10), IN p_limit INT, IN p_offset INT)
BEGIN
  SELECT e.kind, e.ref_id, LEFT(e.title, 300) AS title, LEFT(e.body, 300) AS snippet,
         MATCH(e.title, e.body, e.meta) AGAINST (p_query IN BOOLEAN MODE) AS score
  FROM search_entries e
  WHERE MATCH(e.title, e.body, e.meta) AGAINST (p_query IN BOOLEAN MODE)
    AND (p_kind IS NULL OR e.kind = p_kind)
  ORDER BY score DESC
  LIMIT p_limit OFFSET p_offset;
END$$


-- ================================================================= 7. FAVOURITES / RECENT
-- Har user ke sirf last 50 recent rakhta hai
DROP PROCEDURE IF EXISTS sp_recent_touch$$
CREATE PROCEDURE sp_recent_touch(IN p_user_id INT, IN p_document_id INT)
BEGIN
  INSERT INTO recent_items (user_id, document_id, viewed_at)
  VALUES (p_user_id, p_document_id, UTC_TIMESTAMP(6)) AS new
  ON DUPLICATE KEY UPDATE viewed_at = new.viewed_at;

  DELETE FROM recent_items
  WHERE user_id = p_user_id
    AND viewed_at < (SELECT x.viewed_at FROM (
          SELECT viewed_at FROM recent_items WHERE user_id = p_user_id
          ORDER BY viewed_at DESC LIMIT 1 OFFSET 49) x);
END$$

DROP PROCEDURE IF EXISTS sp_recent_list$$
CREATE PROCEDURE sp_recent_list(IN p_user_id INT, IN p_limit INT)
BEGIN
  SELECT d.id, d.name, d.extension, d.folder_id, r.viewed_at
  FROM recent_items r JOIN documents d ON d.id = r.document_id
  WHERE r.user_id = p_user_id AND d.deleted_at IS NULL
  ORDER BY r.viewed_at DESC
  LIMIT p_limit;
END$$

-- Result: 1 = ab favourite hai, 0 = hata diya
DROP PROCEDURE IF EXISTS sp_favourite_toggle$$
CREATE PROCEDURE sp_favourite_toggle(IN p_user_id INT, IN p_target_type VARCHAR(10), IN p_target_id INT)
BEGIN
  DELETE FROM favourites
  WHERE user_id = p_user_id AND target_type = p_target_type AND target_id = p_target_id;
  IF ROW_COUNT() = 0 THEN
    INSERT INTO favourites (user_id, target_type, target_id, created_at)
    VALUES (p_user_id, p_target_type, p_target_id, UTC_TIMESTAMP(6));
    SELECT 1 AS is_favourite;
  ELSE
    SELECT 0 AS is_favourite;
  END IF;
END$$


-- ================================================================= 8. ADMIN STATS
DROP PROCEDURE IF EXISTS sp_admin_stats$$
CREATE PROCEDURE sp_admin_stats()
BEGIN
  SELECT
    (SELECT COUNT(*) FROM documents WHERE deleted_at IS NULL)          AS documents,
    (SELECT COUNT(*) FROM folders   WHERE deleted_at IS NULL)          AS folders,
    (SELECT COUNT(*) FROM documents WHERE deleted_at IS NOT NULL)      AS in_trash,
    (SELECT COALESCE(SUM(size), 0) FROM document_versions)             AS storage_bytes,
    (SELECT COUNT(*) FROM archived_emails)                             AS emails,
    (SELECT COUNT(*) FROM users WHERE is_active = 1)                   AS active_users,
    (SELECT COUNT(*) FROM users WHERE locked_until > UTC_TIMESTAMP(6)) AS locked_users;
END$$


-- ================================================================= 9. GMAIL (har user ki apni mailbox)
-- Har user sirf apni connect ki hui mailbox ki emails dekh sakta hai; admin ko bhi doosron ki
-- emails nahi dikhti, sirf mailbox ka sync status (sp_gmail_accounts_admin_list).

DROP PROCEDURE IF EXISTS sp_gmail_account_connect$$
CREATE PROCEDURE sp_gmail_account_connect(
  IN p_user_id INT, IN p_email VARCHAR(320), IN p_token_enc TEXT, IN p_user_name VARCHAR(200), IN p_ip VARCHAR(64))
BEGIN
  DECLARE v_email VARCHAR(320) DEFAULT LOWER(TRIM(COALESCE(p_email, '')));
  DECLARE v_id    INT DEFAULT NULL;
  DECLARE v_owner INT DEFAULT NULL;
  DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN ROLLBACK; RESIGNAL; END;

  IF p_user_id IS NULL OR v_email = '' OR COALESCE(p_token_enc, '') = '' THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'A user, a Gmail address and an authorization are required.';
  END IF;

  START TRANSACTION;
  SELECT id, user_id INTO v_id, v_owner FROM gmail_accounts WHERE email = v_email FOR UPDATE;
  IF v_owner IS NOT NULL AND v_owner <> p_user_id THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This Gmail account is already connected by another DMS user.';
  END IF;

  -- Ek user ki ek hi mailbox sync hoti hai. Pehle wali band hoti hai, par uski emails usi user ki rehti hain.
  UPDATE gmail_accounts
     SET is_active = 0, token_enc = NULL, last_sync_status = 'disconnected'
   WHERE user_id = p_user_id AND is_active = 1 AND email <> v_email;

  IF v_id IS NULL THEN
    INSERT INTO gmail_accounts (email, user_id, token_enc, history_id, is_active, connected_at,
                                last_sync_status, legacy_imported)
    VALUES (v_email, p_user_id, p_token_enc, NULL, 1, UTC_TIMESTAMP(6), 'never', 1);
    SET v_id = LAST_INSERT_ID();
  ELSE
    -- Wahi mailbox dobara connect hui: history_id rehta hai, sync wahin se aage chalega.
    UPDATE gmail_accounts
       SET user_id = p_user_id, token_enc = p_token_enc, is_active = 1, connected_at = UTC_TIMESTAMP(6),
           last_sync_status = IF(history_id IS NULL, 'never', 'connected'), last_error = NULL,
           legacy_imported = 1
     WHERE id = v_id;
  END IF;
  CALL sp_activity_log_add('gmail', 'connected', CONCAT('Gmail account ', v_email, ' connected'),
                           v_id, p_user_id, p_user_name, p_ip);
  COMMIT;
  SELECT v_id AS account_id;
END$$

DROP PROCEDURE IF EXISTS sp_gmail_account_disconnect$$
CREATE PROCEDURE sp_gmail_account_disconnect(IN p_user_id INT, IN p_user_name VARCHAR(200), IN p_ip VARCHAR(64))
BEGIN
  DECLARE v_count  INT DEFAULT 0;
  DECLARE v_emails VARCHAR(2000) DEFAULT NULL;
  SELECT COUNT(*), GROUP_CONCAT(email ORDER BY id SEPARATOR ', ') INTO v_count, v_emails
    FROM gmail_accounts WHERE user_id = p_user_id AND is_active = 1;
  -- Token hat jaata hai; archived emails DMS mein usi user ki rehti hain.
  UPDATE gmail_accounts SET is_active = 0, token_enc = NULL, last_sync_status = 'disconnected'
   WHERE user_id = p_user_id AND is_active = 1;
  IF v_count > 0 THEN
    CALL sp_activity_log_add('gmail', 'disconnected', CONCAT('Gmail account ', v_emails, ' disconnected'),
                             NULL, p_user_id, p_user_name, p_ip);
  END IF;
  SELECT v_count AS disconnected;
END$$

DROP PROCEDURE IF EXISTS sp_gmail_account_save_token$$
CREATE PROCEDURE sp_gmail_account_save_token(IN p_account_id INT, IN p_token_enc TEXT)
BEGIN
  -- Refresh ke baad naya token. Beech mein disconnect hua ho to token wapas nahi likha jaata.
  UPDATE gmail_accounts SET token_enc = p_token_enc
   WHERE id = p_account_id AND is_active = 1 AND token_enc IS NOT NULL;
END$$

DROP PROCEDURE IF EXISTS sp_gmail_accounts_due$$
CREATE PROCEDURE sp_gmail_accounts_due(IN p_limit INT)
BEGIN
  -- Sync ke liye taiyaar mailboxes, jo sabse pehle sync hui thi wo pehle.
  -- reauth_required wali tab tak ruki rehti hain jab tak user dobara connect na kare.
  SELECT id FROM gmail_accounts
  WHERE is_active = 1 AND user_id IS NOT NULL AND token_enc IS NOT NULL
    AND last_sync_status <> 'reauth_required'
    AND (lock_until IS NULL OR lock_until < UTC_TIMESTAMP(6))
  ORDER BY last_sync_at IS NOT NULL, last_sync_at, id
  LIMIT p_limit;
END$$

DROP PROCEDURE IF EXISTS sp_gmail_status_for_user$$
CREATE PROCEDURE sp_gmail_status_for_user(IN p_user_id INT)
BEGIN
  -- User ki mailbox (chalu wali, warna sabse nayi) aur uski apni emails ki ginti.
  SELECT a.id AS account_id, a.email, a.is_active, a.connected_at, a.last_sync_at, a.last_success_at,
         a.last_sync_status, a.last_error,
         (SELECT COUNT(*) FROM pending_imports p WHERE p.account_id = a.id AND p.status = 'pending') AS pending,
         (SELECT COUNT(*) FROM pending_imports p WHERE p.account_id = a.id AND p.status = 'failed')  AS failed,
         (SELECT COUNT(*) FROM archived_emails e JOIN gmail_accounts x ON x.id = e.account_id
           WHERE x.user_id = p_user_id) AS archived_total
  FROM gmail_accounts a
  WHERE a.user_id = p_user_id
  ORDER BY a.is_active DESC, a.connected_at DESC, a.id DESC
  LIMIT 1;
END$$

DROP PROCEDURE IF EXISTS sp_gmail_accounts_admin_list$$
CREATE PROCEDURE sp_gmail_accounts_admin_list(IN p_limit INT, IN p_offset INT)
BEGIN
  -- Admin ke liye sirf mailbox ka haal (kaun, kab sync, errors). Email ka content yahan nahi aata.
  SELECT a.id AS account_id, a.email, a.user_id, u.username, a.is_active, a.connected_at, a.last_sync_at,
         a.last_success_at, a.last_sync_status, a.last_error,
         (SELECT COUNT(*) FROM archived_emails e WHERE e.account_id = a.id) AS archived_total,
         (SELECT COUNT(*) FROM pending_imports p WHERE p.account_id = a.id AND p.status = 'pending') AS pending,
         (SELECT COUNT(*) FROM pending_imports p WHERE p.account_id = a.id AND p.status = 'failed')  AS failed,
         COUNT(*) OVER () AS total
  FROM gmail_accounts a
  LEFT JOIN users u ON u.id = a.user_id
  ORDER BY a.email
  LIMIT p_limit OFFSET p_offset;
END$$

DROP PROCEDURE IF EXISTS sp_gmail_retry_failed$$
CREATE PROCEDURE sp_gmail_retry_failed(IN p_user_id INT)
BEGIN
  -- p_user_id NULL = sabki mailboxes (admin); warna sirf us user ki.
  UPDATE pending_imports p
    JOIN gmail_accounts a ON a.id = p.account_id
     SET p.status = 'pending', p.attempts = 0
   WHERE p.status = 'failed' AND (p_user_id IS NULL OR a.user_id = p_user_id);
  SELECT ROW_COUNT() AS requeued;
END$$

DROP PROCEDURE IF EXISTS sp_email_list$$
CREATE PROCEDURE sp_email_list(
  IN p_user_id INT, IN p_q VARCHAR(200), IN p_state VARCHAR(20), IN p_with_attachments TINYINT,
  IN p_sort VARCHAR(20), IN p_limit INT, IN p_offset INT)
BEGIN
  -- Sirf p_user_id ki apni mailboxes ki emails. Har row: total + us page ki email id (khali page par id NULL).
  DECLARE v_q    VARCHAR(200) DEFAULT TRIM(COALESCE(p_q, ''));
  DECLARE v_like VARCHAR(210) DEFAULT CONCAT('%', TRIM(COALESCE(p_q, '')), '%');
  DECLARE v_sort VARCHAR(20)  DEFAULT COALESCE(p_sort, 'imported');
  WITH f AS (
    SELECT e.id, e.subject, e.sender, e.received_at, e.imported_at
    FROM archived_emails e
    JOIN gmail_accounts a ON a.id = e.account_id
    WHERE a.user_id = p_user_id
      AND (v_q = '' OR e.subject LIKE v_like OR e.sender LIKE v_like
           OR e.recipients LIKE v_like OR e.snippet LIKE v_like)
      AND (COALESCE(p_state, '') = '' OR e.gmail_state = p_state)
      AND (COALESCE(p_with_attachments, 0) = 0 OR e.attachment_count > 0)
  ), ranked AS (
    SELECT id, ROW_NUMBER() OVER (ORDER BY
             CASE WHEN v_sort = 'subject'  THEN LOWER(subject) END ASC,
             CASE WHEN v_sort = 'sender'   THEN LOWER(sender)  END ASC,
             CASE WHEN v_sort = 'received' THEN received_at    END DESC,
             CASE WHEN v_sort NOT IN ('subject', 'sender', 'received') THEN imported_at END DESC,
             id DESC) AS rn
    FROM f
  )
  SELECT t.total, r.id
  FROM (SELECT COUNT(*) AS total FROM f) t
  LEFT JOIN ranked r ON r.rn > p_offset AND r.rn <= p_offset + p_limit
  ORDER BY r.rn;
END$$

DROP PROCEDURE IF EXISTS sp_email_get_owned$$
CREATE PROCEDURE sp_email_get_owned(IN p_user_id INT, IN p_email_id INT)
BEGIN
  -- Email tabhi milti hai jab wo p_user_id ki apni mailbox ki ho.
  SELECT e.id
  FROM archived_emails e
  JOIN gmail_accounts a ON a.id = e.account_id
  WHERE e.id = p_email_id AND a.user_id = p_user_id;
END$$

DELIMITER ;

-- ================================================================= CHECK: 36 aane chahiye
SELECT COUNT(*) AS total_procedures
FROM information_schema.routines
WHERE routine_schema = 'DMS' AND routine_type = 'PROCEDURE';
