# Archive DMS — Document Management System with Gmail Email Archiving

A Flask API backend for a shared, access-controlled document repository. It
stores documents with full major/minor version history, encrypts all content at rest, scans every
upload for malware, provides full-text search across documents and archived email, and archives
emails from Gmail as complete, unmodified `.eml` files.

The Flask app is a JSON API only. The user interface is the React app in `DMS-FE`, which calls
`/api/v1`; the backend serves no HTML pages, templates or static files. OIDC, SAML, and Gmail provider
callback endpoints remain available for OAuth redirects.

## BRD Phase 1 traceability

All paths are under `/api/v1`. Acceptance tests are in `tests/test_api_brd.py`, one test per requirement
group.

| ID | Requirement | API endpoints | Implementation |
|---|---|---|---|
| FR-FLD-01 | Create, rename, move, copy, delete folders, unlimited depth | `POST /folders`, `PATCH /folders/{id}` (`name`, `parent_id`), `POST /folders/{id}/copy`, `DELETE /folders/{id}` | `services/folders.py` |
| FR-FLD-02 | Permission inheritance with override at any level | `PATCH /permissions/{kind}/{id}/inheritance`, `POST .../grant`, `GET /permissions/{kind}/{id}` | `security/permissions.py`, `services/access.py` |
| FR-FLD-03 | Naming conventions, invalid characters, duplicates | enforced on every create/rename/move/upload (a same-named upload with changed content becomes a new version; with identical content it is answered with HTTP 409 `duplicate_document` until the user chooses Replace) | `validation.validate_name`; `folder_name_pattern`, `document_name_pattern`, `name_max_length` settings |
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
| FR-SRC-01 | Full-text search across content, email bodies, attachments, metadata | `GET /search?q=` | MySQL `search_entries` table (`services/search.py`); text from PDF and Office/ODF files, email bodies and attachments, plus OCR of images and scanned PDF pages when Tesseract is installed (`services/text_extract.py`) |
| FR-SRC-02 | Boolean, wildcards, phrase, fuzzy, date/size/type filters | `GET /search` with `AND OR NOT -x "phrase" a* b?c word~ name:/body:/meta:` and `type`, `date_from`, `date_to`, `size_min`, `size_max`, `tag`, `folder_id`, `author_id` | `services/search.py` |
| FR-SRC-03 | Security-trimmed results | all lists, search, favourites, recent, ZIP exports | `Resolver.visible_documents_clause` |
| FR-SRC-04 | Facets, sorting, highlighting | `facets`, `sort=relevance|date|name|size`, `<mark>` in `snippet` and `title_html` | `services/search.py` |
| FR-MET-01 | System metadata and custom fields per document type or folder | `author`, `created_at`, `updated_at`, `size`, `extension`, `mime_type`, `version` on every document; `POST /admin/metadata/types`, `.../fields` | `services/metadata.py` |
| FR-MET-02 | Controlled vocabularies, tags, mandatory fields at upload | `GET /metadata/schema?folder_id=&document_type_id=`, `POST /admin/metadata/vocabularies`, `.../tags`; `meta_<field id>` and `tags` on upload | required/choice fields validated at upload; `tags_controlled` setting |
| FR-SEC-01 | RBAC with predefined and custom roles | `GET/POST/PATCH/DELETE /admin/roles`, `PATCH /admin/users/{id}` | Admin, Records Manager, Contributor, Reader, Auditor (`security/permissions.py`) |
| FR-SEC-02 | Permissions at repository, folder, document, version level | `/permissions/{repository|folder|document|version}/...` (`grant`, `share`, `inheritance`, `entries`); `GET /directory` to pick users and roles | `AccessEntry` model; creators get full control of what they create |
| FR-SEC-03 | SSO (SAML 2.0, OIDC, Entra ID, Google), LDAP/AD, MFA | `GET /auth/options`, `POST /auth/login`, `POST /auth/mfa`, `/auth/mfa/setup`; `/auth/oidc/{name}`, `/auth/saml/login` redirects | `security/saml.py`, `routes/auth.py` (Authlib OIDC), `security/ldap_auth.py`, TOTP MFA with recovery codes |
| FR-SEC-04 | TLS 1.2+ in transit; AES-256 at rest; customer-managed keys | `GET /admin/status` reports encryption | `DMS_FORCE_HTTPS` + HSTS, TLS 1.2 minimum in `app.py`; AES-256-GCM envelope encryption (`crypto.py`) |
| FR-SEC-05 | IP restriction, session timeout, device-based policies | `PUT /admin/settings` (`ip_allowlist`, `session_idle_minutes`, `session_max_hours`, `device_policy`, `blocked_platforms`), `GET/PATCH /admin/devices` | `security/auth.py` |

### Audit log

Every important action is recorded in `activity_log` (`services/activity.py`): uploads, new versions,
check-out/in, renames, moves, copies, shortcuts, property/metadata edits, recycle bin and permanent deletion of
documents and folders, sharing, permission grants/removals, inheritance changes, role/user/settings changes,
sign-ins and Gmail events, plus opening (`viewed`) and downloading (`downloaded`) a document and refused attempts
(`access_denied`). Each entry stores **who** (user id, name, IP), **what** (category, action, message),
**which** item (`entity_type`, `entity_id`, `entity_name` snapshot), **when** (UTC) and the **outcome**
(`status`: `success`, `denied` = the user lacked the permission, `failed` = e.g. a rejected sign-in or blocked
malware). Where applicable, `details.changes` holds `{field: {old, new}}` (e.g. old/new name, folder, tags,
metadata, permission sets). A denied attempt is written in its own transaction, so it is kept although the request
fails; when the user could not even see the item, its name is not stored.

* **Read access**: `GET /api/v1/activity` is personal: every signed-in user, administrators included, sees only
  the entries for actions they performed themselves. Filters: `category`, `action`, `entity_type`, `entity_id`,
  `status`, `q`, `date_from`, `date_to`.
* **Tamper protection**: entries are append-only. The triggers `trg_activity_log_no_update` /
  `trg_activity_log_no_delete` reject any UPDATE or DELETE, the ORM refuses to flush changes to existing
  entries, and no API changes or deletes them. The `sp_audit_log_add` procedure and the triggers are installed on
  start-up if missing (needs CREATE ROUTINE and TRIGGER privileges; otherwise run `flask install-procedures`).
  Restrict the production database account so it cannot `DROP TRIGGER` or `TRUNCATE` (grant those only to a
  separate DBA account) for the protection to hold against direct database access.

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
| Files you upload. Documents have versions, live in folders and can be moved, renamed, copied, checked out, trashed and exported as a ZIP. | Emails imported from Gmail, stored as their original `.eml`. They are never versioned, edited or included in document ZIPs. Visible only to the user whose Gmail mailbox they came from. |

**Email preview.** The email page shows the complete message body: text, formatting (inline styles and the
message's own `<style>` rules, without external stylesheets), every body part, and the images embedded in it
(`cid:` images such as logos and signatures, and `data:` images). An image counts as inline only when the body
actually shows it; every other part, including attached images and forwarded emails, is an attachment.
Attachments are listed with their name, file type and size only; they are not rendered in the body and cannot
be opened or downloaded from the preview (`GET /emails/{id}/inline/{n}` serves only images the body shows).
Scripts and remote content stay blocked.

**Linked images (newsletters).** Many emails (e.g. Mailchimp newsletters) do not contain their logos and
pictures; they link to them on the sender's servers, so the `.eml` holds only the addresses. The server
downloads each linked image once (when the email is archived, or the first time it is opened), stores it
encrypted with the email (`email_images` table, `storage/emailimg/`), and the preview shows that copy; the
browser never loads anything from the internet. Downloads are limited to public http(s) addresses (private and
internal addresses are refused, redirects re-checked), real PNG/JPEG/GIF/WebP/BMP data, `DMS_EMAIL_IMAGE_MAX_MB`
(5) per image, `DMS_EMAIL_IMAGE_MAX_COUNT` (60) per email and `DMS_EMAIL_IMAGE_TIMEOUT_SECONDS` (10). Turn it off
with `DMS_EMAIL_REMOTE_IMAGES=0`. `flask --app app email-images [--retry]` downloads them for all emails at once.
The archived `.eml` is never changed: **Download original (.eml)** returns it byte for byte, and **Download with
images (.eml)** (`GET /emails/{id}/eml?with_images=1`) returns a copy with the stored images embedded, which
shows them in any mail program, even offline.

An email attachment can be copied into the library with **Save to Document Library** (the folder icon
next to the attachment on the email page). This creates an independent document, which can then be
versioned like any other. The archived email and its `.eml` file stay unchanged.

### Users, My Files and sharing

- **Accounts.** The first visit creates the administrator. After that anyone who can reach the sign-in page can
  **Create an account** (full name, work email, user name, password). Self-created accounts get **no role**: they
  have their own My Files and whatever others share with them, and an administrator adds them to roles
  (departments) under **Admin › Users** or **Admin › Roles › Members**. Administrators can turn sign-up off under
  **Admin › Settings** and create accounts under **Admin › Users** instead.
- **My Files.** Every user has a private **My Files** folder. Uploads and new folders go there unless you are
  viewing another folder. Nobody else sees it, administrators included, unless you share it. All the *My Files*
  folders sit inside a hidden *Personal files* folder. These folders cannot be renamed, moved or deleted.
- **Shared library (restricted by default).** Nobody but Admin and Records Manager has repository-wide access.
  An administrator creates a top-level folder per department and gives a role access to it (**Share** › pick the
  role, or **Advanced permissions**). Members of that role see the folder; everyone else does not even see that it
  exists. Departments and teams are simply roles (custom roles under **Admin › Roles**, members under
  **Admin › Roles › Members**).
- **Privacy from administrators.** Administrators run the system (users, roles, settings, top-level folders)
  but cannot see, preview, download, edit or delete any other user's folders or documents, in My Files or in
  the Shared library, unless that user shares them. They see only what they created or what was shared with
  them, at the shared level.
- **Ownership.** Whoever creates a folder or uploads a document gets full control of it, even inside the shared
  library. They can rename, delete and share it.
- **Sharing.** **Share** on a folder, document or document row: pick a person or a group (role) and an access level
  (*Can view*, *Can view & download*, *Can add files*, *Can edit*, *Full control*), optionally with an **end date**
  (`expires_at`, `YYYY-MM-DD` = until the end of that day UTC). After the end date the grant is ignored, as if it had
  been removed; the person keeps any other access they have. Sharing a folder shares everything in it. Items shared
  with you appear under **Shared with me**. The owner can change or remove someone's access on the same page.
  **Advanced permissions** on that page shows roles, inheritance and individual permissions.
- **Recycle bin.** Deleted items stay restorable for the configured period (default and minimum 30 days), then
  they are deleted permanently. You can **Delete forever** one item or **Empty recycle bin** at any time. This
  only affects items you deleted or are allowed to delete.

### Roles and permissions

| Role | Repository permissions | System capabilities |
|---|---|---|
| Admin | everything | administration, records management, audit, email archive |
| Records Manager | everything | records management (recycle bin, retention, check-out override), audit, email archive |
| Contributor | — (per department folder) | — |
| Reader | — (per department folder) | — |
| Auditor | — (per department folder) | own audit log |

Contributor, Reader and Auditor work only in the folders they (or a role they hold) are given. On upgrade, the
repository-wide access these roles used to have is copied once onto every existing top-level Shared library
folder and root document, so nobody loses access to what they could open before; new top-level folders are
private until someone grants access.

Permissions: **view** (see and preview), **upload** (add new files and subfolders without changing existing ones;
what you add is yours), **download**, **edit** (upload, new versions, rename, move in, check out, metadata, create
subfolders; implies upload), **delete** (to the recycle bin), **share** (give others view/upload/download/edit up to
your own access), **manage** (change permissions and inheritance; implies everything). Uploading a file whose name
already exists in the folder makes it a new version of that document, so it needs **edit** on that document. A folder, document or version
inherits from its parent; **Stop inheriting** copies the current grants onto it so they can then be narrowed.
Repository-level grants for each role are edited under **Admin › Roles**; grants for specific users or roles on a
folder/document/version under **More › Permissions**.

### Documents and versions

- **Upload Document** creates a *new* document (version 1.0). If a document with the same name already exists in
  the folder:
  - **different content**: the file is saved straight away as that document's next version (1.0 → 1.1 …); every
    earlier version and its history are kept.
  - **identical content** (same name and exactly the same bytes as one of its versions): the user is asked.
    **Replace** deletes the old identical version (record and stored file) and stores the upload in its place as the
    current version, so a document never holds two identical versions; other versions are kept. **Cancel** changes
    nothing and nothing is stored.
  Required metadata fields must be filled in.
  API: `POST /documents` (and `POST /documents/import-email-attachment`): an identical file gets HTTP 409
  `{"code": "duplicate_document", "duplicate": {...}, "duplicates": [...]}` (existing name, version, size,
  `identical_version`, `can_replace`, `reason`); resend it with `on_duplicate=replace&allow_identical=1` to replace.
  In a multi-file upload the other files are still uploaded and the identical ones are listed under `duplicates`.
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
  routes/               api.py (the /api/v1 JSON API), auth.py (OIDC/SAML callbacks),
                        gmail.py (Gmail OAuth callback), _helpers.py (file streaming, ZIP export)
  cli.py                flask commands (init-db, create-user, reindex, maintenance, encrypt-storage, ...)
tests/                  Automated tests against a MySQL test database (tests/test_api_brd.py covers the BRD)
database/               MySQL stored procedures (procedures.sql; install with `flask install-procedures`)
instance/               master.key (created automatically, not in source control)
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

**Every user connects their own mailbox.** Any signed-in user (not only administrators) opens
**Gmail Connection** and clicks **Connect Gmail**, then signs in to Google with their own account. The
OAuth token is stored encrypted (master key) in `gmail_accounts.token_enc`; there is no `token.json`.

Privacy: archived emails belong to the user whose mailbox they came from. Only that user can list,
open, search, download or import them; administrators, records managers and auditors cannot see other
users' emails. Administrators see only each mailbox's sync status (`GET /api/v1/admin/gmail/accounts`),
and the activity log never contains email subjects or content. A Gmail address can be connected by one
DMS user only. Set `GMAIL_ALLOWED_DOMAINS=jmbaxi.com` to allow company mailboxes only.

Alternative from the command line (opens a browser on the same machine):

```powershell
flask --app app gmail-connect <dms-username>
```

On the first connection the app records Gmail's current position (its `historyId`). From then on,
**messages you archive are imported**. Messages archived before connecting are not imported unless
you set `GMAIL_INITIAL_BACKFILL` (for example `50`) before connecting.

## 6. How automatic archiving works

Gmail does not notify external applications when you click **Archive**. Archiving removes the
message's `INBOX` label, and Gmail records that change in the mailbox history. The app works from
that history:

1. **Every `GMAIL_SYNC_INTERVAL_SECONDS`** (120 s by default), a background thread goes through every
   connected mailbox (`GMAIL_SYNC_WORKERS` in parallel) and calls `users.history.list` for changes since
   that mailbox's last stored `historyId`. Only changes are fetched, never the whole mailbox.
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
- **Several web workers**: a per-mailbox database lease ensures each mailbox is synced by one process at a time. For clarity,
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

The tests run against a separate MySQL **test** database, never the application database:

- `TEST_DATABASE_URL` selects it. When it is not set, the server and credentials from `DATABASE_URL` are
  used with `_test` added to the database name (`DMS` -> `DMS_test`).
- Its name must end in `_test` and differ from the application database: every table in it is emptied
  after each test. The tests refuse to start otherwise.
- It is created if missing (the MySQL account needs `CREATE` permission), and the tables and stored
  procedures are (re)created at the start of every run.

```powershell
python -m pytest
```

- `tests/test_api_brd.py`: one or more acceptance tests per BRD requirement through `/api/v1` (folders,
  capture, preview, check-out, malware, versions, compare, retention, search, metadata, RBAC, inheritance,
  sharing, SSO/MFA, encryption, IP/session policies).
- `tests/test_api.py`: cookie and token authentication, CSRF, CORS, the API-only app, emails, admin.
- `tests/test_sharing.py`: share levels and the "Shared with me" / "Shared by me" lists.
- `tests/test_email_preview.py`: sanitised email bodies, inline images, email search and privacy.
- `tests/test_security.py`: security headers, error bodies, storage path traversal and tamper detection.
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
