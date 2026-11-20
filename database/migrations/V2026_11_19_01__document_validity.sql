-- =====================================================================
-- Migration V2026.11.19-01: Document Validity Management & Expiry Monitoring
-- Additive only: creates 9 new tables; no existing table, column, procedure or row is changed.
-- Idempotent (CREATE TABLE IF NOT EXISTS / guarded constraint), safe to run more than once.
--
-- The application also creates these tables itself on start-up (db.create_all()), exactly like every
-- other DMS table. Run this script only where the application account may not create tables
-- (DBA-controlled environments), BEFORE starting the new application version.
--
--   mysql -u <dba> -p DMS < database/migrations/V2026_11_19_01__document_validity.sql
-- Rollback: database/migrations/V2026_11_19_01__document_validity.rollback.sql
-- =====================================================================

-- validity_scheduler_runs
CREATE TABLE IF NOT EXISTS validity_scheduler_runs (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	run_date DATE NOT NULL, 
	status VARCHAR(10) NOT NULL, 
	worker_id VARCHAR(80), 
	lease_until DATETIME, 
	attempts INTEGER NOT NULL, 
	started_at DATETIME NOT NULL, 
	finished_at DATETIME, 
	documents_checked INTEGER NOT NULL, 
	notifications_created INTEGER NOT NULL, 
	resumed INTEGER NOT NULL, 
	errors INTEGER NOT NULL, 
	error_text VARCHAR(1000), 
	PRIMARY KEY (id), 
	UNIQUE (run_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- notification_preferences
CREATE TABLE IF NOT EXISTS notification_preferences (
	user_id INTEGER NOT NULL, 
	email_mode VARCHAR(12) NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (user_id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- notifications
CREATE TABLE IF NOT EXISTS notifications (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	user_id INTEGER NOT NULL, 
	category VARCHAR(20) NOT NULL, 
	kind VARCHAR(30) NOT NULL, 
	severity VARCHAR(10) NOT NULL, 
	title VARCHAR(300) NOT NULL, 
	message VARCHAR(1000) NOT NULL, 
	link VARCHAR(300), 
	entity_type VARCHAR(20), 
	entity_id INTEGER, 
	details_json TEXT, 
	dedupe_key VARCHAR(120) NOT NULL, 
	read_at DATETIME, 
	created_at DATETIME NOT NULL, 
	email_status VARCHAR(10) NOT NULL, 
	email_attempts INTEGER NOT NULL, 
	email_next_attempt_at DATETIME, 
	email_sent_at DATETIME, 
	email_error VARCHAR(300), 
	PRIMARY KEY (id), 
	CONSTRAINT uq_notification_dedupe UNIQUE (user_id, dedupe_key), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- folder_validity_settings
CREATE TABLE IF NOT EXISTS folder_validity_settings (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	folder_id INTEGER NOT NULL, 
	enabled BOOL NOT NULL, 
	enabled_at DATETIME, 
	updated_by_id INTEGER, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (folder_id), 
	FOREIGN KEY(folder_id) REFERENCES folders (id) ON DELETE CASCADE, 
	FOREIGN KEY(updated_by_id) REFERENCES users (id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- document_validity_subscriptions
CREATE TABLE IF NOT EXISTS document_validity_subscriptions (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	document_id INTEGER NOT NULL, 
	user_id INTEGER NOT NULL, 
	subscribed BOOL NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_validity_subscription UNIQUE (document_id, user_id), 
	FOREIGN KEY(document_id) REFERENCES documents (id) ON DELETE CASCADE, 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- document_validity
CREATE TABLE IF NOT EXISTS document_validity (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	document_id INTEGER NOT NULL, 
	version_id INTEGER, 
	document_type VARCHAR(200), 
	title VARCHAR(500), 
	document_number VARCHAR(200), 
	vessel_name VARCHAR(200), 
	issuing_authority VARCHAR(300), 
	issue_date DATE, 
	expiry_date DATE, 
	has_expiry BOOL, 
	confidence FLOAT, 
	verification_status VARCHAR(30) NOT NULL, 
	review_reasons_json TEXT NOT NULL, 
	source VARCHAR(10), 
	analysis_id INTEGER, 
	verified_by_id INTEGER, 
	verified_at DATETIME, 
	monitoring_state VARCHAR(10) NOT NULL, 
	paused_until DATE, 
	state_reason VARCHAR(500), 
	state_changed_by_id INTEGER, 
	state_changed_at DATETIME, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (document_id), 
	FOREIGN KEY(document_id) REFERENCES documents (id) ON DELETE CASCADE, 
	FOREIGN KEY(version_id) REFERENCES document_versions (id) ON DELETE SET NULL, 
	FOREIGN KEY(verified_by_id) REFERENCES users (id) ON DELETE SET NULL, 
	FOREIGN KEY(state_changed_by_id) REFERENCES users (id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- document_validity_analyses
CREATE TABLE IF NOT EXISTS document_validity_analyses (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	document_id INTEGER NOT NULL, 
	version_id INTEGER, 
	job_id INTEGER, 
	sha256 VARCHAR(64), 
	status VARCHAR(20) NOT NULL, 
	model VARCHAR(80), 
	result_json TEXT, 
	verification_status VARCHAR(30), 
	confidence FLOAT, 
	error VARCHAR(500), 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(document_id) REFERENCES documents (id) ON DELETE CASCADE, 
	FOREIGN KEY(version_id) REFERENCES document_versions (id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- document_validity_exceptions
CREATE TABLE IF NOT EXISTS document_validity_exceptions (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	document_id INTEGER NOT NULL, 
	version_id INTEGER, 
	scope VARCHAR(10) NOT NULL, 
	port_name VARCHAR(200), 
	authority VARCHAR(300), 
	reason TEXT NOT NULL, 
	reference_number VARCHAR(200), 
	start_date DATE NOT NULL, 
	end_date DATE, 
	indefinite_justification TEXT, 
	status VARCHAR(10) NOT NULL, 
	evidence_storage_key VARCHAR(255), 
	evidence_filename VARCHAR(255), 
	evidence_mime VARCHAR(127), 
	evidence_size BIGINT, 
	evidence_sha256 VARCHAR(64), 
	created_by_id INTEGER, 
	created_at DATETIME NOT NULL, 
	updated_by_id INTEGER, 
	updated_at DATETIME NOT NULL, 
	withdrawn_by_id INTEGER, 
	withdrawn_at DATETIME, 
	withdrawn_reason VARCHAR(500), 
	PRIMARY KEY (id), 
	FOREIGN KEY(document_id) REFERENCES documents (id) ON DELETE CASCADE, 
	FOREIGN KEY(version_id) REFERENCES document_versions (id) ON DELETE SET NULL, 
	UNIQUE (evidence_storage_key), 
	FOREIGN KEY(created_by_id) REFERENCES users (id) ON DELETE SET NULL, 
	FOREIGN KEY(updated_by_id) REFERENCES users (id) ON DELETE SET NULL, 
	FOREIGN KEY(withdrawn_by_id) REFERENCES users (id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- document_validity_jobs
CREATE TABLE IF NOT EXISTS document_validity_jobs (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	document_id INTEGER NOT NULL, 
	version_id INTEGER NOT NULL, 
	status VARCHAR(12) NOT NULL, 
	attempts INTEGER NOT NULL, 
	max_attempts INTEGER NOT NULL, 
	next_attempt_at DATETIME NOT NULL, 
	locked_by VARCHAR(80), 
	locked_until DATETIME, 
	last_error VARCHAR(500), 
	`trigger` VARCHAR(20) NOT NULL, 
	requested_by_id INTEGER, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	finished_at DATETIME, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_validity_job_version UNIQUE (version_id), 
	FOREIGN KEY(document_id) REFERENCES documents (id) ON DELETE CASCADE, 
	FOREIGN KEY(version_id) REFERENCES document_versions (id) ON DELETE CASCADE, 
	FOREIGN KEY(requested_by_id) REFERENCES users (id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

DELIMITER $$
DROP PROCEDURE IF EXISTS sp__validity_ddl_if_missing$$
CREATE PROCEDURE sp__validity_ddl_if_missing(IN p_kind VARCHAR(10), IN p_table VARCHAR(64), IN p_name VARCHAR(64),
                                              IN p_ddl TEXT)
BEGIN
  DECLARE v_have INT DEFAULT 0;
  IF p_kind = 'fk' THEN
    SELECT COUNT(*) INTO v_have FROM information_schema.referential_constraints
    WHERE constraint_schema = DATABASE() AND table_name = p_table AND referenced_table_name = p_name;
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

CALL sp__validity_ddl_if_missing('index', 'notifications', 'ix_notifications_user_unread', 'ALTER TABLE `notifications` ADD KEY `ix_notifications_user_unread` (`user_id`, `read_at`, `created_at`)')$$
CALL sp__validity_ddl_if_missing('index', 'notifications', 'ix_notifications_email', 'ALTER TABLE `notifications` ADD KEY `ix_notifications_email` (`email_status`, `email_next_attempt_at`)')$$
CALL sp__validity_ddl_if_missing('index', 'document_validity_subscriptions', 'ix_document_validity_subscriptions_user_id', 'ALTER TABLE `document_validity_subscriptions` ADD KEY `ix_document_validity_subscriptions_user_id` (`user_id`)')$$
CALL sp__validity_ddl_if_missing('index', 'document_validity', 'ix_document_validity_expiry', 'ALTER TABLE `document_validity` ADD KEY `ix_document_validity_expiry` (`expiry_date`)')$$
CALL sp__validity_ddl_if_missing('index', 'document_validity', 'ix_document_validity_status', 'ALTER TABLE `document_validity` ADD KEY `ix_document_validity_status` (`verification_status`, `monitoring_state`)')$$
CALL sp__validity_ddl_if_missing('index', 'document_validity_analyses', 'ix_document_validity_analyses_sha256', 'ALTER TABLE `document_validity_analyses` ADD KEY `ix_document_validity_analyses_sha256` (`sha256`)')$$
CALL sp__validity_ddl_if_missing('index', 'document_validity_analyses', 'ix_document_validity_analyses_document_id', 'ALTER TABLE `document_validity_analyses` ADD KEY `ix_document_validity_analyses_document_id` (`document_id`)')$$
CALL sp__validity_ddl_if_missing('index', 'document_validity_analyses', 'ix_document_validity_analyses_version_id', 'ALTER TABLE `document_validity_analyses` ADD KEY `ix_document_validity_analyses_version_id` (`version_id`)')$$
CALL sp__validity_ddl_if_missing('index', 'document_validity_exceptions', 'ix_validity_exceptions_doc', 'ALTER TABLE `document_validity_exceptions` ADD KEY `ix_validity_exceptions_doc` (`document_id`, `status`)')$$
CALL sp__validity_ddl_if_missing('index', 'document_validity_jobs', 'ix_validity_jobs_due', 'ALTER TABLE `document_validity_jobs` ADD KEY `ix_validity_jobs_due` (`status`, `next_attempt_at`)')$$
CALL sp__validity_ddl_if_missing('index', 'document_validity_jobs', 'ix_document_validity_jobs_document_id', 'ALTER TABLE `document_validity_jobs` ADD KEY `ix_document_validity_jobs_document_id` (`document_id`)')$$
CALL sp__validity_ddl_if_missing('fk', 'document_validity', 'document_validity_analyses',
  'ALTER TABLE document_validity ADD CONSTRAINT fk_document_validity_analysis FOREIGN KEY (analysis_id) REFERENCES document_validity_analyses (id) ON DELETE SET NULL')$$
DROP PROCEDURE IF EXISTS sp__validity_ddl_if_missing$$
DELIMITER ;
