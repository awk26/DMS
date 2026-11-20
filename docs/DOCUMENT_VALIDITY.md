# Document Validity Management & Expiry Monitoring

An additive module (`dms/validity/`, frontend `src/components/validity/`). It reads documents, folders,
versions and permissions but never changes how they work. All of its data lives in nine new tables.

## 1. How it works

```
upload / new version / restore (existing code, unchanged)
   │  commit
   ▼
session hook (after commit, own connection, errors swallowed) ── folder monitored? ──► document_validity_jobs (1 per version)
                                                                                         │ worker claims (atomic UPDATE + lease)
                                                                                         ▼
                                                     Gemini (server side) ─► validate JSON ─► document_validity_analyses (history)
                                                                                         │
                                                                     document_validity: requires_confirmation / needs_review
                                                                                         │ user (edit access) confirms/corrects
                                                                                         ▼
                                                                               verified ─► daily run ─► notifications (+ email digest)
```

* **Folder setting.** "Enable Document Validity Monitoring" on *New folder*, or *More › Validity monitoring…* on a
  folder. Modes: *enabled*, *disabled*, or *inherit*. A folder with no setting of its own inherits; the nearest
  explicit setting above it wins, and the top level defaults to off. A subfolder can opt out (or in) at any depth.
  Documents at the library root are never monitored. This is independent of permission inheritance.
* **Who can do what.** Every user, administrators included, is evaluated as an ordinary user
  (`Resolver(honour_admin=False)`). The admin role gives no extra validity rights and no access to other people's
  documents through this module.

  | Action | Needs |
  |---|---|
  | See validity data, follow alerts | `view` on the document |
  | Confirm or correct dates, pause/disable/resume, record/withdraw exceptions, re-run AI | `edit` (owners have it) |
  | Download exception evidence | `download` |
  | Change a folder's setting | `edit` on the folder |

* **AI analysis.**
  * PDFs and images (PNG/JPEG/WebP/HEIC; GIF/BMP/TIFF are converted to PNG) are sent inline.
  * Office, OpenDocument and text files are sent as text extracted by the existing `text_extract`.
  * Multi-page PDFs are sent whole. PDFs over `GEMINI_MAX_INLINE_MB` are sent as extracted text.
  * Damaged or password-protected files are marked *Cannot be analysed* without calling the AI.
  * The model must answer with JSON matching a strict schema. `rules.validate_extraction` then validates it:
    * Types are checked.
    * An unreadable date becomes `null` and is never guessed.
    * The result needs review when an issue date is not before the expiry date, the expiry is ambiguous, the answer
      contradicts itself, or confidence is below `VALIDITY_CONFIDENCE_THRESHOLD`.
  * The AI never sets *verified*.
* **Versions.** A new version gets its own job. Until its dates are confirmed the document shows *New version:
  re-check* and **sends no alerts**, so a renewed certificate never inherits the old expiry date. Identical content
  (e.g. a restored version) reuses the earlier analysis of the same document, with no new AI call. *Analyse with AI*
  always calls the AI again.
* **Daily run.** Once per business day at `VALIDITY_SCHEDULER_TIME` in `VALIDITY_TIMEZONE`. A document gets a notice
  only if all of these hold:
  1. its folder's effective setting is on;
  2. its own monitoring is active (pauses that ended are resumed first and audited);
  3. it is not deleted;
  4. the validity data belongs to its current version;
  5. the data is verified and has an expiry date;
  6. no acceptance exception applies (checked once expired);
  7. today is within `VALIDITY_NOTIFICATION_START_DAYS` of expiry.

  Notices: "expires in 30 days" … "expires in 1 day", "expires today", then "expired N days ago" daily. Only today's
  notice is created: days the server was down, or a pause, never produce a backlog.
* **Recipients.** The owner (unless they untick *Follow alerts*) plus anyone with view access who ticked it.
  Access is re-checked every day and when notifications are listed or emailed.
* **Acceptance exceptions** ("This expired document is still accepted by the port/authority").
  * Fields: scope (port or all ports), port, authority, reference, reason, start date, end/review date (or a written
    justification for no end date) and optional evidence. Evidence is malware-scanned and stored encrypted like
    documents.
  * The expiry date and the *expired* status never change; the UI shows *Expired · accepted*.
  * Only *expired* notices are suppressed. The countdown and *expires today* still appear.
  * Alerts resume automatically after the end date or on withdrawal. Withdrawn exceptions are kept.
* **Audit.** Every change is written to the existing append-only `activity_log`, with `validity_*` actions and old/new
  values in `details.changes`. The document's *Validity history* shows it to anyone who may view the document.

## 2. Reliability

| Concern | Mechanism |
|---|---|
| Upload must never fail because of AI | Jobs are queued **after** the upload commits, in a separate transaction inside `try/except`; a reconcile sweep re-queues anything missed |
| Duplicate analysis | `UNIQUE(version_id)` on jobs; `INSERT IGNORE` |
| Several workers / Gunicorn processes | Jobs are claimed by a conditional `UPDATE … WHERE status='queued' …` (one winner); results are written only while the worker still holds the job (`locked_by` fencing) |
| Worker crash | Job lease (`VALIDITY_JOB_LEASE_SECONDS`); an expired lease is taken over by any worker |
| Gemini timeout / 429 / 5xx / network / invalid JSON | Retried with exponential backoff (`VALIDITY_RETRY_BASE_SECONDS` × 2ⁿ, honours `Retry-After`), up to `VALIDITY_JOB_MAX_ATTEMPTS`, then *Analysis failed* (user can retry) |
| Unsupported / corrupted file, blocked content | Failed at once, no retry |
| Daily run executed twice / concurrently | `UNIQUE(run_date)` claim with lease; crashed runs are taken over; plus `UNIQUE(user_id, dedupe_key)` on notifications, so even a forced second run creates nothing |
| Email failure | Per-notification status, retried with backoff (max 5); one digest per user per run |

## 3. Configuration (`.env`)

| Variable | Default | Purpose |
|---|---|---|
| `VALIDITY_MONITORING_ENABLED` | `true` | Master switch (`false`: no new jobs, no daily run; data kept) |
| `GEMINI_API_KEY` | *(empty)* | Google AI Studio / Gemini API key. Empty = no AI; users enter dates by hand |
| `GEMINI_MODEL` | `gemini-3.8-flash` | Any Gemini model that supports `responseSchema` |
| `GEMINI_TIMEOUT_SECONDS` | `90` | Per request |
| `GEMINI_MAX_INLINE_MB` | `18` | Larger PDFs go as extracted text; larger images are not analysed |
| `VALIDITY_CONFIDENCE_THRESHOLD` | `0.75` | Below: *needs review* |
| `VALIDITY_TIMEZONE` | `Asia/Kolkata` | Business time zone (days left are counted in it) |
| `VALIDITY_SCHEDULER_TIME` | `09:00` | Daily run time (HH:MM, business time) |
| `VALIDITY_NOTIFICATION_START_DAYS` | `30` | First notice this many days before expiry |
| `VALIDITY_WORKER_ENABLED` | `true` | In-process worker thread in the web app |
| `VALIDITY_WORKER_INTERVAL_SECONDS` | `30` | Worker cycle |
| `VALIDITY_JOB_MAX_ATTEMPTS` / `VALIDITY_RETRY_BASE_SECONDS` | `5` / `60` | Retry policy |
| `VALIDITY_JOB_LEASE_SECONDS` / `VALIDITY_RUN_LEASE_SECONDS` | `600` / `1800` | Crash takeover |
| `VALIDITY_EMAIL_DEFAULT_MODE` | `milestones` | `off` / `milestones` / `daily` for users who have not chosen |
| `VALIDITY_APP_URL` | `DMS_ANGULAR_FRONTEND_URL` | Absolute URL for links in emails |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_STARTTLS`, `SMTP_SSL` | *(empty)* | Outgoing email; empty host = in-app only |

Install `tzdata` (now in `requirements.txt`) on Windows. Linux has the zone database already. Without it, a fixed
offset is used for a few common zones and a warning is logged.

The Gemini key stays on the server: it is sent in a request header, never logged, and never returned by any endpoint.
Document content and extracted values are not written to logs.

## 4. Deployment

### Database
Tables are created automatically on start-up (`db.create_all()`), exactly like every other DMS table. Where the
application account may not create tables, a DBA runs this before starting the new release:

```bash
mysql -u <dba> -p DMS < database/migrations/V2026_11_19_01__document_validity.sql
```

It is idempotent and only creates the nine new tables (plus their indexes and the deferred foreign key).

### Processes
* **Single process** (`python app.py`, waitress, one Gunicorn worker): nothing to do. The worker thread starts with
  the app.
* **Several web processes** (Gunicorn `-w N`, several hosts): set `VALIDITY_WORKER_ENABLED=false` for the web
  processes and run **one** worker service. Several workers are safe but unnecessary.

```ini
# /etc/systemd/system/dms-validity-worker.service
[Unit]
Description=DMS validity worker (AI analysis, daily expiry run, email)
After=network-online.target mysql.service

[Service]
User=dms
WorkingDirectory=/opt/dms/DMS-BE
EnvironmentFile=/opt/dms/DMS-BE/.env
Environment=VALIDITY_WORKER_ENABLED=false
ExecStart=/opt/dms/DMS-BE/venv/bin/flask --app app validity-worker
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

The `Environment=VALIDITY_WORKER_ENABLED=false` line only stops that process from also starting the in-process
thread; the command runs the worker loop itself.

Cron alternative for the daily run only (jobs then still need a worker): `5 9 * * * cd /opt/dms/DMS-BE && venv/bin/flask --app app validity-run-daily`.

### Commands
| Command | Purpose |
|---|---|
| `flask --app app validity-worker` | Dedicated worker (foreground) |
| `flask --app app validity-run-daily [--date YYYY-MM-DD]` | Run the daily check now (`--date` for UAT only) |
| `flask --app app validity-process-jobs [--limit N]` | Process due analysis jobs now |
| `flask --app app validity-reconcile` | Queue new versions in monitored folders that have no job |
| `flask --app app validity-status` | Queue, email and last-run state (JSON) |

`GET /api/v1/validity/status` returns the same for administrators. It contains no document content.

### Logging and monitoring
Logger names: `dms.validity.jobs`, `dms.validity.scheduler`, `dms.validity.notifications`, `dms.validity.gemini`.
Alert on:
* *Daily validity run … failed*;
* `validity-status` showing many `failed` jobs, or a growing `queued` count while `ai_configured` is true;
* Gemini HTTP 401/403/404 errors (wrong key or model);
* email `failed` counts.

## 5. UAT checklist

1. Set `GEMINI_API_KEY` (or leave it empty to test manual entry). Restart. Check that `flask --app app validity-status`
   shows `"ai_configured": true`.
2. As an ordinary user: *New folder* with **Enable Document Validity Monitoring** ticked. The folder header shows
   *Validity monitoring*.
3. Upload a licence/certificate PDF. Within about a minute the document page's **Validity** card shows *Confirm dates*
   with the extracted values. Upload a document with no expiry, and a scanned image.
4. Click **Review & confirm**, correct a value, confirm. Check *Validity history*.
5. Set the expiry date to *today + 30*, then run `flask --app app validity-run-daily`. The bell shows "… expires in 30
   days." Run with `--date` *today + 1* to see 29. Use a document per scenario: each business date runs once.
6. **Pause** until tomorrow + 1. Run for tomorrow: no notice. Run for the day after the pause: exactly one notice, no
   backlog.
7. **Disable**, run: nothing. **Resume**, run: notice.
8. Set an expiry in the past, then record *This expired document is still accepted…* with an end date. Run: no
   notice, badge *Expired · accepted*. Run for a date after the end date: *expired N days ago* again. Withdraw:
   resumes.
9. Upload a new version: badge *New version: re-check*, no alerts until confirmed.
10. Second user without access: the document's validity endpoints answer 404, and they get no notifications. Share
    *view*: they can see the data and tick *Follow alerts*, but cannot edit. Administrator without a share: 404 on
    validity data.
11. Configure SMTP, choose *Notifications › Email: Daily*, run: one digest email.
12. Folder *More › Validity monitoring… › Disabled*: existing data stays, no new analyses or notices.
13. Regression: run the full `pytest` suite (see §6).

## 6. Tests

* `tests/test_validity_rules.py`: pure rules, AI-response validation and Gemini error handling. No database needed:
  `python -m pytest --noconftest -p no:cacheprovider tests/test_validity_rules.py`.
* `tests/test_validity.py`: end-to-end against the MySQL test database (same fixtures as the rest of the suite).
  Gemini and SMTP are always faked, so no quota is used. It covers all 24 required scenarios, plus email digests,
  the dashboard and the admin status endpoint.
* The whole suite: `python -m pytest` (needs `TEST_DATABASE_URL` or a DB user that may create `<DB_NAME>_test`).

## 7. Rollback

1. **Application rollback (recommended, non-destructive).** Deploy the previous release, stop any
   `dms-validity-worker` service and restore the previous `.env` (the new variables are simply ignored). Older
   releases never read the new tables, so all existing features behave as before and the validity data is kept for a
   later re-deploy.
2. **Full removal (destructive, optional).** After a backup, run
   `database/migrations/V2026_11_19_01__document_validity.rollback.sql`. It drops only the nine module tables.
   Evidence files stay in the storage folder.

## 8. Changes to existing files (all additive)

| File | Change |
|---|---|
| `dms/__init__.py` | Register the module's models, blueprint (CSRF-exempt like `api.bp`; `api_auth` does its own CSRF check), CLI, session hook and `ensure_validity_worker()` |
| `app.py` | Start the validity worker in `__main__`, like the Gmail and maintenance threads |
| `dms/config.py` | New settings; `TestConfig` disables the worker, Gemini and SMTP |
| `dms/routes/api.py` | `POST /folders` accepts an optional `validity_monitoring` field (absent = unchanged behaviour) |
| `requirements.txt` | `tzdata` |
| `.env.example` | New variables (appended) |
| Frontend | `FolderModal` checkbox, folder page badge and menu item, document page **Validity** card, topbar bell, sidebar *Document Validity*, routes `/validity` and `/notifications`, two icons, CSS appended |

## 9. Known limitations and decisions to confirm

* **Port-specific exceptions** suppress the document's expired alerts for all recipients. Alerts are per document,
  not per port. Please confirm this is acceptable, or whether port-specific exceptions should only label the
  document.
* **Exceptions suppress expired notices only** (after the expiry date). The countdown and *expires today* still
  appear, by design.
* **Recipients** are the owner plus users who opt in (*Follow alerts*). Members of a role with access are not
  notified automatically, to avoid alert floods.
* **Enabling an existing folder** does not analyse documents already in it unless *Also analyse documents already in
  this folder* is ticked, to avoid unexpected AI cost.
* **Very large images** (above `GEMINI_MAX_INLINE_MB`) are not analysed; enter their dates by hand. The Gemini File
  API is not used.
* **Audit visibility.** Administrators see validity audit entries in the global audit log, as they do for every other
  metadata change (existing Permissions Matrix v2 behaviour).
