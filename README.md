# Archive DMS — Document Management System with Gmail Email Archiving

A Flask API backend for a shared, access-controlled document repository. It
stores documents with full major/minor version history, encrypts all content at rest, scans every
upload for malware, provides full-text search across documents and archived email, and archives
emails from Gmail as complete, unmodified `.eml` files.

The Flask app runs API-only by default. Angular and other clients use `/api/v1`; the legacy
`dms/templates` and `dms/static` source files are retained in the repository but are not registered
or served. OIDC, SAML, and Gmail provider callback endpoints remain available for OAuth redirects.

## BRD Phase 1 traceability

All paths are under `/api/v1`. Acceptance tests that run against the API only (legacy UI disabled)
are in `tests/test_api_brd.py`, one test per requirement group.

| ID | Requirement | API endpoints | Implementation |
|---|---|---|---|
| FR-FLD-01 | Create, rename, move, copy, delete folders, unlimited depth | `POST /folders`, `PATCH /folders/{id}` (`name`, `parent_id`), `POST /folders/{id}/copy`, `DELETE /folders/{id}` | `services/folders.py` |
| FR-FLD-02 | Permission inheritance with override at any level | `PATCH /permissions/{kind}/{id}/inheritance`, `POST .../grant`, `GET /permissions/{kind}/{id}` | `security/permissions.py`, `services/access.py` |
| FR-FLD-03 | Naming conventions, invalid characters, duplicates | enforced on every create/rename/move/upload | `validation.validate_name`; `folder_name_pattern`, `document_name_pattern`, `name_max_length` settings |
| FR-FLD-04 | Breadcrumbs, favourites, recent items, shortcuts | `breadcrumbs` in `GET /folders/{id}`, `GET /folders/root` and `GET /documents/{id}`; `GET/PUT/DELETE /favourites`; `GET /recent`; `POST /documents/{id}/links`, `DELETE /documents/links/{id}`, `shortcuts` in folder listings | `services/documents.py` (`add_link`, `record_view`) |
| FR-FLD-05 | Recycle bin, restorable for a configurable period (default 30 days) | `GET/DELETE /recycle-bin`, `POST /folders/{id}/restore`, `POST /documents/{id}/restore`, `.../purge`; `deleted_at`/`expires_at` on items | `services/retention.py` (expiry by the maintenance job), `recycle_bin_days` setting (minimum 30) |
| FR-DOC-01 | Single/multiple upload, desktop sync client, mobile app | `POST /documents` (one or several `file` parts); personal access tokens (`POST /account/tokens`); `GET /documents?changed_since=` change feed | `services/documents.upload_document` |
| FR-DOC-02 | Common file types; admin allow/block lists and size limits | `PUT /admin/settings` (`allowed_extensions`, `blocked_extensions`, `max_upload_mb`, `type_size_limits`) | `validation.KNOWN_TYPES` (content-checked) |
| FR-DOC-03 | In-browser preview (PDF, Office, images, video) | `GET /documents/{id}/preview` (PDF, images, text, video/audio with range requests, Office as PDF when LibreOffice is installed), `GET /documents/{id}/render` (sanitised HTML for Office), `preview` block in `GET /documents/{id}` | `services/preview.py`; frontends in `DMS_CORS_ALLOWED_ORIGINS` may embed previews in an iframe |
| FR-DOC-04 | Check-in / check-out | `POST /documents/{id}/checkout`, `.../checkin`, `.../release` (records managers can break another user's lock) | `services/documents.ensure_unlocked` guards every change |
| FR-DOC-05 | Malware scan before availability | every upload, new version and attachment import | `security/malware.py`: ClamAV, Microsoft Defender or built-in signatures |
| FR-VER-01 | New version on every modification, full history | `POST /documents/{id}/versions`, check-in, restore; `versions` in `GET /documents/{id}` | nothing is overwritten |
| FR-VER-02 | Major/minor numbering, mandatory change comments | `major=1` and `note` on new versions | `require_version_comment` setting: `none` / `major` / `all` |
| FR-VER-03 | View, download, compare, restore | `GET /documents/{id}/preview?version=N`, `GET /documents/{id}/content?version=N`, `GET /documents/{id}/compare?a=N&b=M` (side-by-side `rows` and `redline`), `POST /documents/{id}/versions/{n}/restore` | `services/compare.py` (text, PDF, Word, Excel, PowerPoint, OpenDocument, RTF) |
| FR-VER-04 | Restore creates a new version | `POST /documents/{id}/versions/{n}/restore` | `restore_version` appends a new version |
| FR-VER-05 | Version-retention rules | `PUT /admin/settings` (`version_keep_last`, `version_keep_years`), `PATCH /folders/{id}/retention`, `POST /admin/maintenance` | `services/retention.py` |
| FR-SRC-01 | Full-text search across content, email bodies, attachments, metadata | `GET /search?q=` | SQLite FTS5 index (`services/search.py`, `services/text_extract.py`) |
| FR-SRC-02 | Boolean, wildcards, phrase, fuzzy, date/size/type filters | `GET /search` with `AND OR NOT -x "phrase" a* b?c word~ name:/body:/meta:` and `type`, `date_from`, `date_to`, `size_min`, `size_max`, `tag`, `folder_id`, `author_id` | `services/search.py` |
| FR-SRC-03 | Security-trimmed results | all lists, search, favourites, recent, ZIP exports | `Resolver.visible_documents_clause` |
| FR-SRC-04 | Facets, sorting, highlighting | `facets`, `sort=relevance|date|name|size`, `<mark>` in `snippet` | `services/search.py` |
| FR-MET-01 | System metadata and custom fields per document type or folder | `author`, `created_at`, `updated_at`, `size`, `extension`, `mime_type`, `version` on every document; `POST /admin/metadata/types`, `.../fields` | `services/metadata.py` |
| FR-MET-02 | Controlled vocabularies, tags, mandatory fields at upload | `GET /metadata/schema?folder_id=&document_type_id=`, `POST /admin/metadata/vocabularies`, `.../tags`; `meta_<field id>` and `tags` on upload | required/choice fields validated at upload; `tags_controlled` setting |
| FR-SEC-01 | RBAC with predefined and custom roles | `GET/POST/PATCH/DELETE /admin/roles`, `PATCH /admin/users/{id}` | Admin, Records Manager, Contributor, Reader, Auditor (`security/permissions.py`) |
| FR-SEC-02 | Permissions at repository, folder, document, version level | `/permissions/{repository|folder|document|version}/...` (`grant`, `share`, `inheritance`, `entries`); `GET /directory` to pick users and roles | `AccessEntry` model; creators get full control of what they create |
| FR-SEC-03 | SSO (SAML 2.0, OIDC, Entra ID, Google), LDAP/AD, MFA | `GET /auth/options`, `POST /auth/login`, `POST /auth/mfa`, `/auth/mfa/setup`; `/auth/oidc/{name}`, `/auth/saml/login` redirects | `security/saml.py`, `routes/auth.py` (Authlib OIDC), `security/ldap_auth.py`, TOTP MFA with recovery codes |
| FR-SEC-04 | TLS 1.2+ in transit; AES-256 at rest; customer-managed keys | `GET /admin/status` reports encryption | `DMS_FORCE_HTTPS` + HSTS, TLS 1.2 minimum in `app.py`; AES-256-GCM envelope encryption (`crypto.py`) |
| FR-SEC-05 | IP restriction, session timeout, device-based policies | `PUT /admin/settings` (`ip_allowlist`, `session_idle_minutes`, `session_max_hours`, `device_policy`, `blocked_platforms`), `GET/PATCH /admin/devices` | `security/auth.py` |

`PUT /admin/settings` (or `PATCH`) takes a JSON object with only the settings to change; every other
setting keeps its value. Unknown keys are rejected.

### What needs infrastructure outside this application

* **Desktop sync client and mobile app** are separate client applications. The server side they need
  (token authentication, change feed, upload/download, check-out/check-in) is provided by the REST API.
* **Antivirus engine**: for production configure ClamAV (`CLAMAV_HOST`) or run the service under an
  account that can use Microsoft Defender. Without one, only the built-in signatures (EICAR test file)
  are checked and the Admin Console shows a warning.
* **TLS certificates**, the **identity provider** registrations (Entra ID / Google / SAML IdP) and the
  **directory server** are configured in `.env` (see `.env.example`).
* **Customer-managed keys in a cloud KMS/HSM** plug in through `DMS_KEY_PROVIDER`; a local key file or
  environment keys work out of the box.

## Features

- **Access control**: user accounts, five predefined roles plus custom roles, permissions on the repository,
  folders, documents and individual versions, inheritance that can be stopped at any level, sharing.
- **Sign-in**: local accounts (strong passwords, lockout), LDAP/Active Directory, OpenID Connect (Microsoft Entra
  ID, Google or any OIDC provider), SAML 2.0, TOTP multi-factor authentication, automatic user provisioning with
  directory-group → role mapping.
- **Policies**: IP allowlist, idle timeout and maximum session length, device approval and platform blocking.
- **Documents**: upload (drag and drop, multiple files), metadata and tags at upload, in-browser preview, check-in /
  check-out, rename, move, copy, shortcuts, favourites, recycle bin.
- **Versions**: major/minor numbering, change comments, download any version, compare (side-by-side and redline),
  restore as a new version, retention rules.
- **Search**: full-text search with boolean operators, phrases, wildcards and fuzzy matching, facets, filters and
  highlighting; results are always limited to what the user may see.
- **Security**: AES-256-GCM encryption at rest, TLS/HSTS, malware scanning, CSRF protection, strict CSP, and an
  audit log of every change, sign-in and permission change (with user and IP address).
- **Email archive**: emails archived in Gmail are imported automatically as their original `.eml`, scanned for
  malware and indexed for search (bodies and attachments).
- **Folder download**: any folder, or the whole library, can be downloaded as a ZIP that keeps the folder
  structure (only what the user may download is included).

The app has two separate areas, shown as separate groups in the sidebar:

| Document Library | Email Archive |
|---|---|
| Files you upload. Documents have versions, live in folders and can be moved, renamed, copied, checked out, trashed and exported as a ZIP. | Emails imported from Gmail, stored as their original `.eml`. They are never versioned, edited or included in document ZIPs. Visible to roles with the *Email archive access* capability. |

An email attachment can be copied into the library with **Save to Document Library** (the folder icon
next to the attachment on the email page). This creates an independent document, which can then be
versioned like any other. The archived email and its `.eml` file stay unchanged.

### Users, My Files and sharing

- **Accounts.** The first visit creates the administrator. After that anyone who can reach the sign-in page can
  **Create an account** (full name, work email, user name, password). New accounts get the role chosen under
  **Admin › Settings › Role for self-created accounts** (default *Contributor*; administrator and records-manager
  roles are never given this way). Administrators can turn sign-up off there and create accounts under
  **Admin › Users** instead.
- **My Files.** Every user has a private **My Files** folder. Uploads and new folders go there unless you are
  viewing another folder. Nobody else sees it except administrators. All the *My Files* folders sit inside a
  *Personal files* folder that only administrators see. These folders cannot be renamed, moved or deleted.
- **Shared library.** The top-level folders that all Contributors see. Use it for team content.
- **Ownership.** Whoever creates a folder or uploads a document gets full control of it, even inside the shared
  library. They can rename, delete and share it.
- **Sharing.** **Share** on a folder, document or document row: pick a person and an access level (*Can view*,
  *Can view & download*, *Can edit*, *Full control*). Sharing a folder shares everything in it. Items shared with
  you appear under **Shared with me**. The owner can change or remove someone's access on the same page.
  **Advanced permissions** on that page shows roles, inheritance and individual permissions.
- **Recycle bin.** Deleted items stay restorable for the configured period (default and minimum 30 days), then
  they are deleted permanently. You can **Delete forever** one item or **Empty recycle bin** at any time. This
  only affects items you deleted or are allowed to delete.

### Roles and permissions

| Role | Repository permissions | System capabilities |
|---|---|---|
| Admin | everything | administration, records management, audit, email archive |
| Records Manager | everything | records management (recycle bin, retention, check-out override), audit, email archive |
| Contributor | view, download, edit, share | — |
| Reader | view, download | — |
| Auditor | view | audit log, email archive |

Permissions: **view** (see and preview), **download**, **edit** (upload, new versions, rename, move in, check out,
metadata, create subfolders), **delete** (to the recycle bin), **share** (give others view/download/edit up to your
own access), **manage** (change permissions and inheritance; implies everything). A folder, document or version
inherits from its parent; **Stop inheriting** copies the current grants onto it so they can then be narrowed.
Repository-level grants for each role are edited under **Admin › Roles**; grants for specific users or roles on a
folder/document/version under **More › Permissions**.

### Documents and versions

- **Upload Document** always creates a *new* document (version 1.0). If a file with the same name already
  exists in the folder, the new document gets a suffix such as `Report (1).pdf`; it never becomes a
  version of the existing one. Required metadata fields must be filled in.
- To update a document, use **Upload New Version** (minor: 1.0 → 1.1, or major: 1.1 → 2.0) or check the document
  out, edit it, and **Check in** with the new file. The new file must have the same file type.
- While a document is **checked out**, nobody else can upload versions, rename, move, delete or edit its properties.
- **Version History** lists every version with author and comment. Each one can be downloaded (subject to
  version-level permissions), compared with another version, or restored — restoring creates a new version.
- Moving or renaming a document does not create a version and does not change its history.

### Folder ZIP download

- **Download Folder (ZIP)** on a folder page, or the download icon on any folder card, returns
  `<Folder>.zip` with the current version of every document in that folder and all its subfolders.
  The folder structure is kept and empty folders are included.
- **More → Download ZIP with version history** also adds a `_version_history/<document>/v1.0 - …`
  folder with every stored version of documents that have more than one.
- Documents in the recycle bin, archived emails, the database, credentials, keys and tokens are never included,
  nor are items the user may not download. If a stored file is missing, the ZIP contains `MISSING_FILES.txt`.
- The ZIP is built in a temporary file that is deleted after the download; nothing is kept on disk.

### REST API (desktop sync client, mobile app, integrations)

Create a personal access token with the account API and send it as
`Authorization: Bearer dms_…`. Endpoints (all permission-checked and audited):

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/me` | The token's user and roles |
| GET / POST | `/api/v1/folders` | List visible folders / create a folder (`name`, `parent_id`) |
| GET | `/api/v1/documents?changed_since=ISO8601&folder_id=` | Change feed for sync clients (includes deletions) |
| GET | `/api/v1/documents/<id>` | Document, metadata and versions |
| GET | `/api/v1/documents/<id>/content[?version=n]` | Download (supports HTTP Range) |
| POST | `/api/v1/documents` | Upload (`file`, `folder_id`, `document_type_id`, `meta_<field id>`, `tags`, `idempotency_key`) |
| POST | `/api/v1/documents/<id>/versions` | New version (`file`, `note`, `major=1`) |
| POST | `/api/v1/documents/<id>/checkout`, `/checkin` | Locking for offline editing |
| DELETE | `/api/v1/documents/<id>` | Move to the recycle bin |

#### Angular integration

The versioned API is rooted at `/api/v1`; local development uses
`http://localhost:5000/api/v1`. It reuses the existing services, permission checks, malware
scanning, validation, audit logging, and storage. The Flask server does not serve the Angular app;
run Angular separately and configure its API base URL and CORS origin.

Browser clients can use an HTTP-only cookie session:

1. Send `GET /api/v1/auth/csrf` with browser credentials enabled.
2. Send `POST /api/v1/auth/login` with JSON `{ "username": "...", "password": "..." }` and the
   returned token in the `X-CSRFToken` header.
3. Use the refreshed token returned by login for subsequent `POST`, `PUT`, `PATCH`, and `DELETE`
   requests. If login reports `mfa_required`, complete `POST /api/v1/auth/mfa` first.
4. Fetch a new CSRF token after a reload or a new login. Logout is `POST /api/v1/auth/logout`.

Non-browser clients can continue using personal access tokens; send those in an `Authorization`
Bearer header. Token-authenticated requests do not need cookie CSRF protection. `GET /api/v1/me`
and `GET /api/v1/account` return the authenticated user's profile and roles.

Set `DMS_CORS_ALLOWED_ORIGINS` to a comma-separated list of exact frontend origins (scheme, host,
and optional port; no wildcard or path). Cookies are sent only when the Angular HTTP client uses
credentials. For a production frontend on a different site, use HTTPS and set
`SESSION_COOKIE_SAMESITE=None` and `SESSION_COOKIE_SECURE=true`; same-site localhost development
can use the `Lax` default. Set `DMS_ANGULAR_FRONTEND_URL` so successful SSO or Gmail OAuth callbacks
return to Angular. `GET /api/v1/auth/options` includes SSO login URLs; the provider callback and
Gmail OAuth endpoints remain browser redirect flows, while application data is exchanged via APIs.

Additional API operations include:

| Area | API paths |
|---|---|
| Documents | `GET /documents/<id>/preview`; `PATCH /documents/<id>` for rename, move, or metadata; `POST /documents/<id>/restore`, `/release`, `/versions/<n>/restore`; `DELETE /documents/<id>/purge`; `POST /documents/<id>/links`, `DELETE /documents/links/<link-id>`; `POST /documents/import-email-attachment` |
| Folders | `GET /folders/<id>`, `/folders/<id>/history`, `/folders/<id>/download`, `/folders/download`; `PATCH /folders/<id>` and `/folders/<id>/retention`; `POST /folders/<id>/restore`; `DELETE /folders/<id>` and `/folders/<id>/purge` |
| Search and personal views | `GET /search`, `/recent`, `/versions`, `/dashboard`, `/activity`, `/shared`, `/favourites`, `/recycle-bin`; `PUT`/`DELETE /favourites/<folder-or-document>/<id>`; `DELETE /recycle-bin` |
| Sharing and email | `/permissions/<kind>/<id>` for access entries; `/emails` for listing and detail; `/emails/<id>/body`, `/eml`, `/attachments/<part>`, and `/inline/<part>` for safely retrieved archive content |
| Gmail | `GET /gmail/status`, `/gmail/connect`; `POST /gmail/sync`, `/gmail/retry`, `/gmail/disconnect` |
| Administration | `/admin/users`, `/admin/roles`, `/admin/settings`, `/admin/metadata`, and `/admin/devices` support the corresponding list/create/update operations |

File uploads use `multipart/form-data` with a `file` field and optional metadata fields; downloads
and previews return file responses with content headers and range support where applicable. JSON
validation/authentication errors include `ok: false`, a human-readable `error`, and a stable `code`
where applicable, with standard HTTP status codes. Email archive operations enforce the existing
email capability and block downloading infected attachments.

## Project layout

```
app.py                  Entry point (python app.py); serves TLS 1.2+ when a certificate is configured
dms/
  __init__.py           App factory, security headers, error handling
  config.py             Environment-based configuration
  models.py             SQLAlchemy models
  migrate.py            In-place upgrades of existing databases
  crypto.py             AES-256-GCM encryption at rest, key ring, rotation
  storage.py            File storage (encrypting local backend; swappable)
  validation.py         File types, size limits, content checks, naming conventions
  security/             Sign-in & policies (auth.py), RBAC (permissions.py), MFA, LDAP, SAML, malware scanning
  services/             Business logic: documents, folders, access, metadata, search, preview, compare,
                        retention, settings, emails, activity
  gmail/                OAuth (auth.py), synchronisation (sync.py), background scheduler
  routes/               Blueprints: dashboard, documents, folders, search, permissions, account, admin, auth,
                        api, emails, gmail
  templates/ static/    Web interface
  cli.py                flask commands (init-db, create-user, reindex, maintenance, encrypt-storage, ...)
tests/                  Automated tests (tests/test_brd_requirements.py covers the BRD requirements)
instance/               SQLite database and master.key (created automatically, not in source control)
storage/                Encrypted document versions, renditions and .eml files (not in source control)
emails/                 .eml files saved by the earlier version; imported once, left in place
```

## 1. Install

From the project folder, using the existing virtual environment:

```powershell
venv\Scripts\activate
pip install -r requirements.txt
```

## 2. Configure

```powershell
copy .env.example .env
```

Edit `.env` and set `SECRET_KEY` to a long random value:

```powershell
python -c "import secrets; print(secrets.token_hex(32))"
```

All settings are listed in `.env.example`: encryption keys, HTTPS, malware scanner, LDAP, OIDC and
SAML, plus the Gmail settings. Policies that administrators change at runtime (file types, size limits,
retention, sessions, IP and device rules, MFA requirement) are in **Admin › Settings**.

**Encryption key.** On first start the app creates `instance\master.key`. Back it up securely and
separately from the data — without it the stored files cannot be decrypted. To use your own
(customer-managed) key instead, set `DMS_MASTER_KEY_FILE` or `DMS_MASTER_KEYS` before the first start.

## 3. Google Cloud / Gmail API setup

The app needs **read-only** Gmail access (`gmail.readonly`).

1. In [Google Cloud Console](https://console.cloud.google.com/), select or create a project.
2. Go to **APIs & Services → Library** and enable the **Gmail API**.
3. Go to **APIs & Services → OAuth consent screen**:
   - User type: *External* (or *Internal* for a Workspace organisation).
   - Add the scope `https://www.googleapis.com/auth/gmail.readonly`.
   - While the app is in *Testing*, add every Gmail account that will connect under **Test users**.
4. Go to **APIs & Services → Credentials → Create credentials → OAuth client ID**:
   - **Desktop app** (what this project uses now): localhost redirects work automatically.
   - **Web application** (for a server deployment): add the authorised redirect URI
     `https://<your-host>/gmail/oauth2callback`.
5. Download the JSON file and save it as `credentials.json` in the project folder.

`credentials.json` and `token.json` are excluded from source control by `.gitignore`. They are read
only by the server and never sent to the browser.

## 4. Initialise the database

The schema is created — and an existing database upgraded in place — automatically on startup. To do it
explicitly:

```powershell
flask --app app init-db
```

## 5. Run and create the first administrator

```powershell
python app.py
```

Then open http://127.0.0.1:5000 **on the server itself**. While no user exists, the app shows a setup page
to create the first administrator (or use `flask --app app create-user <name> --role Admin`). Add further
users under **Admin › Users**, or let people sign in with LDAP/SSO (accounts are created on first sign-in with
the default role or the roles mapped from their directory groups).

For production: serve over HTTPS (set `DMS_SSL_CERT_FILE`/`DMS_SSL_KEY_FILE`, or put a TLS-terminating reverse
proxy in front and set `DMS_TRUSTED_PROXIES=1`), then set `DMS_FORCE_HTTPS=true` and `SESSION_COOKIE_SECURE=true`.

### Connect Gmail

As an administrator, open **Gmail Connection** in the sidebar and click **Connect Gmail**, then sign in with a test-user
account. If an existing `token.json` is present (as in this installation), the account is already
connected.

Alternative from the command line (opens a browser on the same machine):

```powershell
flask --app app gmail-connect
```

On the first connection the app records Gmail's current position (its `historyId`). From then on,
**messages you archive are imported**. Messages archived before connecting are not imported unless
you set `GMAIL_INITIAL_BACKFILL` (for example `50`) before connecting.

## 6. How automatic archiving works

Gmail does not notify external applications when you click **Archive**. Archiving removes the
message's `INBOX` label, and Gmail records that change in the mailbox history. The app works from
that history:

1. **Every `GMAIL_SYNC_INTERVAL_SECONDS`** (120 s by default), a background thread calls
   `users.history.list` for changes since the last stored `historyId`. Only changes are fetched, never
   the whole mailbox.
2. Messages whose `INBOX` label was removed are added to a durable import queue. The new `historyId`
   is saved in the same database transaction, so an interruption cannot lose a message.
3. For each queued message, the app fetches `messages.get(format="raw")`, checks the message's
   **current** labels (if it is back in the Inbox, in Trash or in Spam, it is skipped), decodes the
   URL-safe Base64 and writes the bytes straight to `storage/emails/<account>/<messageId>.eml`
   (written to a temporary file, flushed to disk, then renamed into place).
4. The database record is committed only after the file is written. A message counts as archived only
   once both steps succeed. The pair (Gmail account, message ID) is unique, so repeated or overlapping
   runs never create duplicates.
5. Failed imports are retried on later runs (up to `GMAIL_MAX_IMPORT_ATTEMPTS`). After that they are
   shown in the UI with a **Retry failed** button.
6. Changes to messages that are already archived are reconciled. If a message goes back to the Inbox,
   is trashed, is marked as spam or is permanently deleted in Gmail, its status is updated in the DMS.
   The archived copy is always kept.
7. Gmail keeps history for a limited time. If the stored `historyId` has expired (HTTP 404), the app
   rescans archived mail from the last `GMAIL_RESYNC_WINDOW_DAYS` days, imports anything missing
   (duplicates are skipped) and starts again from a fresh baseline.

**Latency:** an archived email appears within one sync interval (about 2 minutes by default) while
the app is running. It is not instantaneous. **Sync now** on the Email Archive or Gmail page triggers
an immediate check.

Messages that skip the Inbox through a Gmail filter, and your own sent messages, were never in the
Inbox and therefore are not treated as archived.

### Continuous synchronisation on a server

The background thread only runs while the web process runs. For a server deployment:

- **Single web process** (for example `waitress-serve` or one gunicorn worker): the built-in
  scheduler is enough. Keep the process running as a service (Windows service / NSSM, systemd, etc.).
- **Several web workers**: a database lease ensures that only one sync runs at a time. For clarity,
  set `GMAIL_SYNC_ENABLED=false` for the web workers and run one dedicated worker:
  ```
  flask --app app gmail-worker
  ```
- **Scheduled task instead of a worker**: run `flask --app app gmail-sync` every few minutes from
  Windows Task Scheduler or cron. It exits non-zero on failure.
- Serve over HTTPS and set `SESSION_COOKIE_SECURE=true`. Use a **Web application** OAuth client with
  the server's redirect URI, and publish the OAuth consent screen (or keep the users as test users).
- Faster detection than polling would require Gmail push notifications (`users.watch` with Google
  Cloud Pub/Sub and a public HTTPS endpoint). This is not part of the current implementation.

## 7. Security notes

- **Authentication** is required for every page and API call. Sessions end after the idle timeout or the
  maximum session length; accounts lock after repeated failed sign-ins; MFA can be required for local and
  LDAP accounts (SSO users get MFA from their identity provider).
- **Access control** is enforced on the server for every page, download, preview, search result, ZIP export and
  API call. Items a user cannot see return "not found".
- **Encryption at rest**: every stored file (document versions, Office renditions, archived `.eml` files) is
  encrypted with its own AES-256-GCM data key, wrapped by the master key. Chunks are authenticated, so tampering
  or truncation is detected. MFA secrets in the database are encrypted with the same key ring.
  - Files stored before encryption was enabled stay readable; convert them with `flask --app app encrypt-storage`.
  - Rotate the master key with `flask --app app rotate-key` (adds a new key, makes it active, re-wraps every file).
- **Encryption in transit**: with `DMS_FORCE_HTTPS=true` all HTTP requests are redirected to HTTPS and HSTS is
  sent; the built-in server accepts only TLS 1.2 or newer. LDAP should use `ldaps://` or `LDAP_START_TLS=true`.
- **Malware scanning**: every upload (web, API, saved email attachments) is scanned before it is stored; infected
  files are rejected and logged. Archived emails are scanned too; an infected email is kept for the record but its
  attachments cannot be downloaded or imported.
- **Audit log** (Admin/Auditor): uploads, versions, moves, deletions, restores, permission and inheritance changes,
  sign-ins and failures, MFA changes, device approvals and settings changes, with user and IP address.
- CSRF protection on all state-changing requests, a strict Content-Security-Policy, `nosniff`, same-origin
  framing and `no-store` caching of pages.
- Uploads are validated by extension, administrator allow/block lists, size limits **and** file signature.
  Files are stored under random internal names outside the code folder and are only served through
  database records. Active content (HTML, SVG) is never displayed inline.
- Email HTML and Office previews are sanitised (nh3) and rendered in sandboxed frames with no scripts.
- Lock-out recovery: `flask --app app reset-security-policy` clears the IP allowlist and device restrictions;
  requests from the server itself (localhost) are always allowed.

## 8. Tests

```powershell
python -m pytest
```

- `tests/test_brd_requirements.py`: one or more acceptance tests per BRD requirement (folders, capture, preview,
  check-out, malware, versions, compare, retention, search, metadata, RBAC, inheritance, share, LDAP, SAML, MFA,
  encryption, IP/session/device policies, REST API, audit trail).
- `tests/test_documents.py`, `test_folders.py`, `test_email_preview.py`, `test_security.py`: core
  workflows, versioning, validation, sanitisation, CSRF.
- `tests/test_document_library.py`: upload workflow, version history scenario, moves that keep
  history, folder ZIP export, failed operations leaving no records or orphaned files, and
  saving email attachments without changing the archived email.
- `tests/test_gmail_sync_mocked.py`: Gmail synchronisation against a **mocked** Gmail API.
- `tests/test_gmail_live.py`: **live**, read-only check against the real account in `token.json`.
  It is skipped by default. To run it:
  ```powershell
  $env:RUN_LIVE_GMAIL="1"; python -m pytest tests/test_gmail_live.py -v -s
  ```

To check a stored email (decrypted through the app): `python verify_eml.py` or
`python verify_eml.py emails/1/<messageId>.eml`.

## 9. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Setup page says it must be completed from the server | Open the app on the server itself (localhost) or set `DMS_ALLOW_REMOTE_SETUP=true` temporarily. |
| "This device is waiting for administrator approval" | The device policy requires approval: an administrator approves it under **Admin › Devices**. |
| "Access from your network address is not permitted" | The IP allowlist in **Admin › Settings** does not include the client address (behind a proxy, set `DMS_TRUSTED_PROXIES`). |
| Uploads rejected: "could not be scanned for malware" | The configured antivirus (ClamAV/Defender) is unreachable and `DMS_MALWARE_FAIL_CLOSED=true`. Start clamd or fix `CLAMAV_HOST`. |
| "Master key '…' is not available" | The key that encrypted a file is missing from the key ring. Restore `instance\master.key` (or the key in `DMS_MASTER_KEYS`). |
| Office preview shows "Simplified preview" | LibreOffice is not installed; install it (or set `LIBREOFFICE_PATH`) for exact page rendering. |
| Search finds nothing for older content | Rebuild the index: **Admin Console › Rebuild search index** or `flask --app app reindex`. |
| `Error 403: access_denied` when connecting | The account is not listed as a **Test user** on the OAuth consent screen. |
| `redirect_uri_mismatch` | Web clients: add `http(s)://<host>/gmail/oauth2callback` exactly. Desktop clients: open the app via `localhost` or `127.0.0.1`. |
| "Gmail access has expired or been revoked" | The refresh token was revoked, the password changed, or the consent screen is in Testing (refresh tokens expire after 7 days). Click **Reconnect**. |
| `Gmail API has not been used in project … or it is disabled` | Enable the Gmail API for the project that owns `credentials.json`. |
| "Stored Gmail authorization lacks required permissions" | `token.json` was created with other scopes. Reconnect. |
| Archived email does not appear | Wait one interval or click **Sync now**. Check that the message is not in Trash or Spam and was in the Inbox before archiving. Check **Gmail** → status and errors. |
| "Session expired" on a form | The CSRF token expired (for example because `SECRET_KEY` is not set and the server restarted). Set `SECRET_KEY` in `.env` and reload. |
| HTTP 413 on upload | The file exceeds `DMS_MAX_UPLOAD_MB`. |
| `database is locked` | Another process holds a write lock. Only one sync runs at a time; retry. For heavy use, switch `DATABASE_URL` to PostgreSQL. |

## Moving storage

All file access goes through `dms/storage.py` (`LocalStorage`), using opaque keys such as
`documents/ab/cd/<uuid>` and `emails/<account>/<id>.eml`. Content is encrypted before it reaches the backend,
so the storage location never sees plaintext. To move to a network share, point `DMS_STORAGE_DIR` at it (and
keep the master key with it). For object storage, implement the same methods (`save_stream`, `save_bytes`,
`open_reader`, `read_bytes`, `exists`, `delete`) in a new backend class. The database schema does not change.
