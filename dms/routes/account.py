"""The signed-in user's own account: profile, password, MFA, API tokens and devices."""
from flask import Blueprint, current_app, flash, g, redirect, render_template, request, session, url_for
from sqlalchemy import select
from werkzeug.security import check_password_hash

from ..extensions import db
from ..models import ApiToken, UserDevice, utcnow
from ..security import mfa
from ..security.auth import check_password_policy, hash_password, new_api_token
from ..services.activity import log_activity
from ..validation import ValidationError
from ._helpers import handle_errors, ok

bp = Blueprint("account", __name__, url_prefix="/account")


def _me():
    return g.user


@bp.get("/")
def profile():
    user = _me()
    tokens = db.session.execute(select(ApiToken).where(ApiToken.user_id == user.id, ApiToken.revoked_at.is_(None))
                                .order_by(ApiToken.created_at.desc())).scalars().all()
    devices = db.session.execute(select(UserDevice).where(UserDevice.user_id == user.id)
                                 .order_by(UserDevice.last_seen.desc())).scalars().all()
    return render_template("account/profile.html", user=user, tokens=tokens, devices=devices,
                           new_token=session.pop("new_api_token", None),
                           recovery_left=mfa.remaining_recovery_codes(user), current_device=session.get("device_id"))


@bp.post("/password")
@handle_errors(lambda **_: url_for("account.profile"))
def change_password():
    user = _me()
    if user.auth_source != "local":
        raise ValidationError("Your password is managed by your organisation's identity provider.")
    if not check_password_hash(user.password_hash or "", request.form.get("current", "")):
        raise ValidationError("The current password is incorrect.")
    if request.form.get("new") != request.form.get("new2"):
        raise ValidationError("The new passwords do not match.")
    check_password_policy(request.form.get("new"))
    user.password_hash = hash_password(request.form.get("new"))
    log_activity("security", "password_changed", f"'{user.username}' changed their password", user.id)
    db.session.commit()
    return ok("Password changed.", url_for("account.profile"))


@bp.get("/mfa")
def mfa_setup():
    user = _me()
    secret = session.get("mfa_pending_secret")
    if not secret:
        secret = mfa.new_secret()
        session["mfa_pending_secret"] = secret
    uri = mfa.provisioning_uri(secret, user.email or user.username, current_app.config["APP_NAME"])
    return render_template("account/mfa.html", user=user, secret=secret, qr=mfa.qr_svg_data_uri(uri),
                           recovery_codes=session.pop("mfa_recovery_codes", None))


@bp.post("/mfa/enable")
@handle_errors(lambda **_: url_for("account.mfa_setup"))
def mfa_enable():
    user = _me()
    secret = session.get("mfa_pending_secret")
    if not secret or not mfa.verify_totp(secret, request.form.get("code")):
        raise ValidationError("That code is not correct. Check the time on your phone and try again.")
    user.mfa_secret = mfa.seal(secret)
    user.mfa_enabled = True
    codes, hashed = mfa.new_recovery_codes()
    user.mfa_recovery_json = hashed
    session.pop("mfa_pending_secret", None)
    session["mfa_ok"] = True
    session["mfa_recovery_codes"] = codes
    log_activity("security", "mfa_enabled", f"'{user.username}' enabled multi-factor authentication", user.id)
    db.session.commit()
    flash("Multi-factor authentication is on. Save your recovery codes now.", "success")
    return redirect(url_for("account.mfa_setup"))


@bp.post("/mfa/disable")
@handle_errors(lambda **_: url_for("account.profile"))
def mfa_disable():
    from ..services import settings
    user = _me()
    if settings.get("mfa_required") and user.auth_source in ("local", "ldap"):
        raise ValidationError("Your organisation requires multi-factor authentication.")
    if not mfa.verify_totp(mfa.unseal(user.mfa_secret), request.form.get("code")):
        raise ValidationError("Enter a current code from your authenticator app to turn MFA off.")
    user.mfa_enabled = False
    user.mfa_secret = None
    user.mfa_recovery_json = "[]"
    log_activity("security", "mfa_disabled", f"'{user.username}' disabled multi-factor authentication", user.id)
    db.session.commit()
    return ok("Multi-factor authentication turned off.", url_for("account.profile"))


@bp.post("/tokens")
@handle_errors(lambda **_: url_for("account.profile"))
def create_token():
    name = (request.form.get("name") or "").strip()
    if not name:
        raise ValidationError("Give the token a name, such as 'Laptop sync client'.")
    days = request.form.get("days", type=int) or None
    if days is not None and not 1 <= days <= 3650:
        raise ValidationError("Expiry must be between 1 and 3650 days.")
    raw, _ = new_api_token(_me(), name, days)
    session["new_api_token"] = raw
    return redirect(url_for("account.profile") + "#tokens")


@bp.post("/tokens/<int:token_id>/revoke")
@handle_errors(lambda **_: url_for("account.profile"))
def revoke_token(token_id):
    token = db.session.get(ApiToken, token_id)
    if token is None or token.user_id != _me().id:
        raise ValidationError("Token not found.")
    token.revoked_at = utcnow()
    log_activity("security", "token_revoked", f"API token '{token.name}' revoked", _me().id)
    db.session.commit()
    return ok("Token revoked.", url_for("account.profile") + "#tokens")


@bp.post("/devices/<int:device_id>/remove")
@handle_errors(lambda **_: url_for("account.profile"))
def remove_device(device_id):
    device = db.session.get(UserDevice, device_id)
    if device is None or device.user_id != _me().id:
        raise ValidationError("Device not found.")
    if device.id == session.get("device_id"):
        raise ValidationError("You cannot remove the device you are using.")
    db.session.delete(device)
    db.session.commit()
    return ok("Device removed. It will need to sign in again.", url_for("account.profile") + "#devices")
