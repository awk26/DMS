"""Flask CLI commands: `flask --app app <command>`."""
import json
import time

import click

from .extensions import db


def register_cli(app):
    @app.cli.command("init-db")
    def init_db():
        """Create database tables (safe to run repeatedly)."""
        db.create_all()
        click.echo(f"Database ready: {app.config['SQLALCHEMY_DATABASE_URI']}")

    @app.cli.command("gmail-connect")
    def gmail_connect():
        """Authorize Gmail from this machine using a local browser window."""
        from .gmail.auth import console_authorize
        from .gmail.sync import run_sync
        console_authorize()
        result = run_sync(trigger="connect")
        click.echo(f"Connected: {result.account} ({result.status}) {result.message}")

    @app.cli.command("gmail-sync")
    def gmail_sync():
        """Run one Gmail synchronization (suitable for cron / Task Scheduler)."""
        from .gmail.sync import run_sync
        result = run_sync(trigger="cli")
        click.echo(json.dumps(result.as_dict(), indent=2, default=str))
        if result.status not in ("ok", "partial", "busy"):
            raise SystemExit(1)

    @app.cli.command("gmail-worker")
    @click.option("--interval", type=int, default=None, help="Seconds between runs.")
    def gmail_worker(interval):
        """Run Gmail synchronization continuously in the foreground (separate worker process)."""
        from .gmail.sync import run_sync
        interval = interval or app.config["GMAIL_SYNC_INTERVAL_SECONDS"]
        click.echo(f"Gmail sync worker running every {interval}s. Press Ctrl+C to stop.")
        while True:
            result = run_sync(trigger="worker")
            click.echo(f"[{time.strftime('%H:%M:%S')}] {result.status}: {result.message}")
            db.session.remove()
            time.sleep(interval)

    @app.cli.command("create-user")
    @click.argument("username")
    @click.option("--role", "roles", multiple=True, default=["Admin"], help="Role name (repeatable).")
    @click.option("--email", default=None)
    @click.password_option()
    def create_user(username, roles, email, password):
        """Create a local user (for example the first administrator)."""
        from .models import User
        from .security.auth import check_password_policy, hash_password, role_by_name
        check_password_policy(password)
        role_objs = [role_by_name(r) for r in roles]
        if not all(role_objs):
            raise click.ClickException(f"Unknown role in {roles}")
        user = User(username=username.strip().lower(), email=email, password_hash=hash_password(password),
                    roles=role_objs)
        db.session.add(user)
        db.session.commit()
        click.echo(f"User '{user.username}' created with roles: {', '.join(roles)}")

    @app.cli.command("reindex")
    def reindex():
        """Rebuild the full-text search index."""
        from .services.search import reindex_all
        click.echo(f"Indexed {reindex_all()} item(s).")

    @app.cli.command("maintenance")
    def maintenance():
        """Purge expired recycle-bin items and apply version-retention rules now."""
        from .services.retention import run_maintenance
        click.echo(json.dumps(run_maintenance()))

    @app.cli.command("encrypt-storage")
    def encrypt_storage():
        """Encrypt files stored before encryption at rest was enabled (safe to re-run)."""
        from .storage import get_storage
        storage = get_storage()
        if not storage.encrypted:
            raise click.ClickException("Encryption at rest is disabled (DMS_ENCRYPTION_AT_REST=false).")
        converted = sum(1 for key in list(storage.iter_keys()) if storage.encrypt_existing(key))
        click.echo(f"Encrypted {converted} file(s).")

    @app.cli.command("rotate-key")
    @click.option("--rewrap-only", is_flag=True, help="Only re-wrap files with the current active key.")
    def rotate_key(rewrap_only):
        """Add a new master key to the key file, make it active and re-wrap every file's data key."""
        from pathlib import Path
        from .crypto import add_key_to_file, load_key_ring
        from .storage import get_storage
        storage = get_storage()
        if not storage.encrypted:
            raise click.ClickException("Encryption at rest is disabled.")
        if not rewrap_only:
            path = Path(app.config.get("DMS_MASTER_KEY_FILE") or Path(app.config["INSTANCE_DIR"]) / "master.key")
            if not path.exists():
                raise click.ClickException("Keys come from DMS_MASTER_KEYS or a key provider: add the new key "
                                           "there, set DMS_ACTIVE_KEY_ID, then run with --rewrap-only.")
            kid = add_key_to_file(path)
            storage.ring = load_key_ring(app.config)
            click.echo(f"New active master key: {kid}")
        n = sum(1 for key in list(storage.iter_keys()) if storage.rewrap(key))
        click.echo(f"Re-wrapped {n} file(s) with key '{storage.ring.active_key_id}'.")

    @app.cli.command("reset-security-policy")
    def reset_security_policy():
        """Clear the IP allowlist and device restrictions (recovery if administrators are locked out)."""
        from .services import settings
        settings.set_value("ip_allowlist", [])
        settings.set_value("device_policy", "any")
        settings.set_value("blocked_platforms", [])
        db.session.commit()
        click.echo("IP allowlist and device policy reset.")

    @app.cli.command("gmail-retry")
    def gmail_retry():
        """Re-queue imports that failed permanently."""
        from .gmail.sync import retry_failed
        click.echo(f"{retry_failed()} item(s) re-queued.")
