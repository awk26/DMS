-- =====================================================================
-- ROLLBACK of V2026.11.27-01 (Gmail address reconnect by another user)
-- Restores the global UNIQUE(email) key. This FAILS if an address now has rows for more than one
-- user (i.e. someone already used the new behaviour). Find them first:
--   SELECT email, COUNT(*) FROM gmail_accounts GROUP BY email HAVING COUNT(*) > 1;
-- Those rows each hold their owner's archived emails, so do not delete them without a decision.
-- Also redeploy the previous application release (its procedures.sql has the old sp_gmail_account_connect).
-- =====================================================================
ALTER TABLE gmail_accounts ADD UNIQUE KEY uq_gmail_accounts_email (email);
ALTER TABLE gmail_accounts DROP INDEX uq_gmail_accounts_email_user;
