from sqlalchemy import func, select

from ..extensions import db
from ..models import Role, User
from ..security.auth import check_password_policy, hash_password
from ..validation import ValidationError


def apply_user_data(user, data, creating=False):
    username = (data.get("username") or "").strip().lower()
    if creating:
        if not username or len(username) > 150:
            raise ValidationError("A user name is required.")
        if db.session.execute(select(User).where(func.lower(User.username) == username)).first():
            raise ValidationError(f"The user name '{username}' is already taken.")
        user.username = username
    user.display_name = (data.get("display_name") or "").strip()[:200]
    user.email = (data.get("email") or "").strip().lower() or None
    source = data.get("auth_source", "local")
    if source not in ("local", "ldap", "oidc", "saml"):
        raise ValidationError("Invalid sign-in method.")
    user.auth_source = source
    user.is_active = data.get("is_active") == "1" if not creating else True
    role_ids = {int(value) for value in data.getlist("roles") if str(value).isdigit()}
    user.roles = db.session.execute(select(Role).where(Role.id.in_(role_ids)).order_by(Role.name)
                                    ).scalars().all() if role_ids else []
    password = data.get("password") or ""
    if source == "local" and (password or creating):
        check_password_policy(password)
        user.password_hash = hash_password(password)
        user.failed_logins, user.locked_until = 0, None
    if source != "local":
        user.password_hash = None
