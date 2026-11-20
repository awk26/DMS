"""Document Validity Management & Expiry Monitoring.

A separate, additive module: it never changes how documents, folders, versions, permissions or the
recycle bin work. It reads them, and keeps its own data in its own tables:

* ``models.py``         tables (created by ``db.create_all()`` on start-up, like every other table)
* ``rules.py``          pure business rules: countdown/notice decisions, AI response validation
* ``clock.py``          business time zone and "today"
* ``access.py``         permission checks (administrators are evaluated as ordinary users)
* ``service.py``        folder settings, document records, monitoring controls, exceptions
* ``gemini.py``         Google Gemini client (server side only; the key never reaches the browser)
* ``jobs.py``           durable analysis queue: enqueue on upload, atomic claim, retry with backoff
* ``notifications.py``  in-app notification centre and email digests (reusable by other modules)
* ``scheduler.py``      daily expiry run and the background worker thread
* ``routes.py``         ``/api/v1`` endpoints
* ``cli.py``            ``flask validity-*`` commands (dedicated worker, daily run for cron)
"""
