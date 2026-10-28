"""LDAP / Active Directory authentication (FR-SEC-03)."""
import logging
import ssl

from flask import current_app

log = logging.getLogger(__name__)


class LdapUnavailable(Exception):
    pass


def enabled():
    return bool(current_app.config.get("LDAP_URL"))


def _server():
    from ldap3 import Server, Tls
    cfg = current_app.config
    url = cfg["LDAP_URL"]
    tls = Tls(validate=ssl.CERT_REQUIRED, version=ssl.PROTOCOL_TLS_CLIENT,
              ca_certs_file=cfg.get("LDAP_CA_FILE") or None)
    return Server(url, use_ssl=url.lower().startswith("ldaps://"), tls=tls, get_info=None, connect_timeout=10)


def _first(entry, attr):
    if not attr or attr not in entry:
        return ""
    value = entry[attr].value
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return str(value or "")


def _groups(entry, attr):
    if not attr or attr not in entry:
        return []
    values = entry[attr].values or []
    out = []
    for v in values:
        v = str(v)
        out.append(v)
        if v.upper().startswith("CN="):  # also expose the plain group name of a DN
            out.append(v.split(",", 1)[0][3:])
    return out


def authenticate(username, password):
    """Return {'dn','email','name','groups'} on success, None on bad credentials.

    Raises LdapUnavailable when the directory cannot be reached.
    """
    from ldap3 import Connection, SUBTREE
    from ldap3.core.exceptions import LDAPBindError, LDAPException
    from ldap3.utils.conv import escape_filter_chars

    cfg = current_app.config
    if not username or not password:
        return None  # an empty password would be an anonymous (unauthenticated) bind
    if any(c in username for c in ',=+<>#;\\"*()\x00'):
        return None  # never let a user name alter a bind DN or search filter
    attrs = [a for a in (cfg["LDAP_ATTR_EMAIL"], cfg["LDAP_ATTR_NAME"], cfg["LDAP_ATTR_GROUPS"]) if a]
    search_filter = cfg["LDAP_USER_FILTER"].format(username=escape_filter_chars(username))
    try:
        server = _server()
        if cfg.get("LDAP_USER_DN_TEMPLATE"):
            user_dn = cfg["LDAP_USER_DN_TEMPLATE"].format(username=username)
            conn = Connection(server, user=user_dn, password=password, raise_exceptions=True)
            if cfg.get("LDAP_START_TLS"):
                conn.open()
                conn.start_tls()
            conn.bind()
            entry = None
            if cfg.get("LDAP_BASE_DN") and conn.search(cfg["LDAP_BASE_DN"], search_filter, SUBTREE,
                                                       attributes=attrs, size_limit=2) and conn.entries:
                entry = conn.entries[0]
            conn.unbind()
            dn = entry.entry_dn if entry is not None else user_dn
        else:
            svc = Connection(server, user=cfg["LDAP_BIND_DN"] or None, password=cfg["LDAP_BIND_PASSWORD"] or None,
                             raise_exceptions=True)
            if cfg.get("LDAP_START_TLS"):
                svc.open()
                svc.start_tls()
            svc.bind()
            svc.search(cfg["LDAP_BASE_DN"], search_filter, SUBTREE, attributes=attrs, size_limit=2)
            entries = list(svc.entries)
            svc.unbind()
            if len(entries) != 1:
                return None
            entry = entries[0]
            dn = entry.entry_dn
            conn = Connection(server, user=dn, password=password, raise_exceptions=True)
            if cfg.get("LDAP_START_TLS"):
                conn.open()
                conn.start_tls()
            conn.bind()
            conn.unbind()
    except LDAPBindError:
        return None
    except LDAPException as exc:
        log.error("LDAP error: %s", exc)
        raise LdapUnavailable("The directory server could not be reached.") from exc
    except OSError as exc:
        raise LdapUnavailable("The directory server could not be reached.") from exc
    if entry is None:
        return {"dn": dn, "email": username if "@" in username else "", "name": username, "groups": []}
    return {
        "dn": dn,
        "email": _first(entry, cfg["LDAP_ATTR_EMAIL"]),
        "name": _first(entry, cfg["LDAP_ATTR_NAME"]) or username,
        "groups": _groups(entry, cfg["LDAP_ATTR_GROUPS"]),
    }
