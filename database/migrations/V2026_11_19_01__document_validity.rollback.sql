-- =====================================================================
-- ROLLBACK of V2026.11.19-01 (Document Validity Management & Expiry Monitoring)
-- DESTRUCTIVE: permanently removes all validity data, AI analysis history, acceptance exceptions,
-- notifications and scheduler history. Existing DMS tables and data are not touched.
-- Back up these tables first, e.g.:
--   mysqldump DMS document_validity_jobs document_validity_exceptions document_validity_analyses document_validity document_validity_subscriptions folder_validity_settings notifications notification_preferences validity_scheduler_runs > validity_backup.sql
-- Only needed for a full removal: rolling back the APPLICATION alone (previous release) is enough,
-- because older releases simply ignore these tables. Stored evidence files under
-- DMS_STORAGE_DIR/documents/ that belong to exceptions are not removed by this script.
-- =====================================================================
SET FOREIGN_KEY_CHECKS = 0;
DROP TABLE IF EXISTS `document_validity_jobs`;
DROP TABLE IF EXISTS `document_validity_exceptions`;
DROP TABLE IF EXISTS `document_validity_analyses`;
DROP TABLE IF EXISTS `document_validity`;
DROP TABLE IF EXISTS `document_validity_subscriptions`;
DROP TABLE IF EXISTS `folder_validity_settings`;
DROP TABLE IF EXISTS `notifications`;
DROP TABLE IF EXISTS `notification_preferences`;
DROP TABLE IF EXISTS `validity_scheduler_runs`;
SET FOREIGN_KEY_CHECKS = 1;
