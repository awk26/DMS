-- =====================================================================================
-- DMS data cleanup: audit logs, emails + email images, and leftover junk data.
-- USERS, ROLES, FOLDERS, DOCUMENTS, SHARES and SETTINGS are NOT deleted.
--
-- Run on database `DMS` (MySQL 8). TAKE A BACKUP FIRST:
--   mysqldump -h 10.1.190.43 -u dms_user -p --routines --triggers DMS > DMS_backup.sql
-- Stop the backend (python app.py) while this runs, so Gmail sync does not write in between.
-- =====================================================================================
USE DMS;

-- -------------------------------------------------------------------------------------
-- STEP 0 - PREVIEW: how many rows each step will delete (read-only, safe to run)
-- -------------------------------------------------------------------------------------
SELECT 'activity_log (audit)'          AS what, COUNT(*) AS rows_to_delete FROM activity_log
UNION ALL SELECT 'folder_activity (history)', COUNT(*) FROM folder_activity
UNION ALL SELECT 'archived_emails',           COUNT(*) FROM archived_emails
UNION ALL SELECT 'email_images',              COUNT(*) FROM email_images
UNION ALL SELECT 'email_attachments',         COUNT(*) FROM email_attachments
UNION ALL SELECT 'pending_imports',           COUNT(*) FROM pending_imports
UNION ALL SELECT 'search_entries (emails)',   COUNT(*) FROM search_entries WHERE kind = 'email'
UNION ALL SELECT 'search_entries (orphan docs)', COUNT(*) FROM search_entries s
          WHERE s.kind = 'document'
            AND NOT EXISTS (SELECT 1 FROM documents d WHERE d.id = s.ref_id AND d.deleted_at IS NULL)
UNION ALL SELECT 'access_entries (emails / orphans / expired)', COUNT(*) FROM access_entries a
          WHERE a.target_type = 'email'
             OR (a.expires_at IS NOT NULL AND a.expires_at < UTC_TIMESTAMP())
             OR (a.target_type = 'document' AND NOT EXISTS (SELECT 1 FROM documents d WHERE d.id = a.target_id))
             OR (a.target_type = 'folder'   AND NOT EXISTS (SELECT 1 FROM folders  f WHERE f.id = a.target_id))
             OR (a.principal_type = 'user'  AND NOT EXISTS (SELECT 1 FROM users    u WHERE u.id = a.principal_id))
UNION ALL SELECT 'favourites (orphans)', COUNT(*) FROM favourites v
          WHERE (v.target_type = 'document' AND NOT EXISTS (SELECT 1 FROM documents d WHERE d.id = v.target_id))
             OR (v.target_type = 'folder'   AND NOT EXISTS (SELECT 1 FROM folders  f WHERE f.id = v.target_id))
UNION ALL SELECT 'recent_items',                       COUNT(*) FROM recent_items
UNION ALL SELECT 'api_tokens (revoked / expired)',     COUNT(*) FROM api_tokens
          WHERE revoked_at IS NOT NULL OR (expires_at IS NOT NULL AND expires_at < UTC_TIMESTAMP());


-- -------------------------------------------------------------------------------------
-- STEP 1 - AUDIT LOGS
-- activity_log is append-only: triggers block DELETE/UPDATE. TRUNCATE does not fire
-- triggers, so it empties the table and keeps the protection for normal use.
-- (TRUNCATE commits immediately and cannot be rolled back.)
-- -------------------------------------------------------------------------------------
TRUNCATE TABLE activity_log;
TRUNCATE TABLE folder_activity;          -- per-folder history ("Folder history" page)


-- -------------------------------------------------------------------------------------
-- STEPS 2 + 3 in one transaction (rolled back completely if anything fails)
-- -------------------------------------------------------------------------------------
START TRANSACTION;

-- STEP 2 - EMAILS and EMAIL IMAGES (children first, then the emails)
DELETE FROM email_images;
DELETE FROM email_attachments;
DELETE FROM search_entries  WHERE kind = 'email';
DELETE FROM access_entries  WHERE target_type = 'email';      -- email shares
DELETE FROM pending_imports;
DELETE FROM archived_emails;
-- Mailbox connections (gmail_accounts) are KEPT, so users stay connected and only NEW mail
-- is archived from now on. Uncomment to also disconnect every mailbox:
-- DELETE FROM gmail_accounts;

-- STEP 3 - JUNK / ORPHANED DATA (rows that point at things that no longer exist)
DELETE s FROM search_entries s
 WHERE s.kind = 'document'
   AND NOT EXISTS (SELECT 1 FROM documents d WHERE d.id = s.ref_id AND d.deleted_at IS NULL);

DELETE a FROM access_entries a
 WHERE (a.expires_at IS NOT NULL AND a.expires_at < UTC_TIMESTAMP())                 -- expired shares
    OR (a.target_type = 'document' AND NOT EXISTS (SELECT 1 FROM documents d WHERE d.id = a.target_id))
    OR (a.target_type = 'folder'   AND NOT EXISTS (SELECT 1 FROM folders  f WHERE f.id = a.target_id))
    OR (a.principal_type = 'user'  AND NOT EXISTS (SELECT 1 FROM users    u WHERE u.id = a.principal_id));

DELETE v FROM favourites v
 WHERE (v.target_type = 'document' AND NOT EXISTS (SELECT 1 FROM documents d WHERE d.id = v.target_id))
    OR (v.target_type = 'folder'   AND NOT EXISTS (SELECT 1 FROM folders  f WHERE f.id = v.target_id));

DELETE FROM recent_items;                                      -- "Recent" history only
DELETE FROM api_tokens
 WHERE revoked_at IS NOT NULL OR (expires_at IS NOT NULL AND expires_at < UTC_TIMESTAMP());

COMMIT;


-- -------------------------------------------------------------------------------------
-- STEP 4 - CHECK: everything below should be 0
-- -------------------------------------------------------------------------------------
SELECT (SELECT COUNT(*) FROM activity_log)    AS audit_logs,
       (SELECT COUNT(*) FROM archived_emails) AS emails,
       (SELECT COUNT(*) FROM email_images)    AS email_images,
       (SELECT COUNT(*) FROM users)           AS users_still_there,      -- NOT 0: users are kept
       (SELECT COUNT(*) FROM documents)       AS documents_still_there;  -- NOT 0: documents are kept

-- -------------------------------------------------------------------------------------
-- NOTES
-- * Email files stay on disk. Delete these two folders inside the backend's storage folder
--   (DMS-BE\storage) after running this script:
--     PowerShell:  Remove-Item -Recurse -Force .\storage\emails, .\storage\emailimg
-- * Recycle bin (deleted documents/folders): empty it from the app instead
--   (Admin -> Recycle Bin -> "Empty recycle bin"). That also deletes the files from storage,
--   which SQL cannot do.
-- * Optional - forget remembered login devices (users may need device approval again
--   if a device policy is switched on):
--     DELETE FROM user_devices;
-- =====================================================================================
