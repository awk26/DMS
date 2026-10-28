"""SAML 2.0 service provider (FR-SEC-03): SP-initiated login with the HTTP-Redirect binding
for the AuthnRequest and the HTTP-POST binding for the response.

The IdP must sign the Response or the Assertion (RSA/ECDSA with SHA-256 or stronger). Only the
XML returned by the signature verifier is used, which prevents signature-wrapping attacks.
Encrypted assertions are not supported.
"""
import base64
import secrets
import zlib
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from xml.sax.saxutils import escape, quoteattr

from flask import current_app, url_for
from lxml import etree

NS = {
    "samlp": "urn:oasis:names:tc:SAML:2.0:protocol",
    "saml": "urn:oasis:names:tc:SAML:2.0:assertion",
    "ds": "http://www.w3.org/2000/09/xmldsig#",
}
SKEW = timedelta(minutes=3)


class SamlError(Exception):
    pass


def enabled():
    cfg = current_app.config
    return bool(cfg.get("SAML_ENABLED") and cfg.get("SAML_IDP_SSO_URL") and cfg.get("SAML_IDP_CERT_FILE"))


def sp_entity_id():
    return current_app.config.get("SAML_SP_ENTITY_ID") or url_for("auth_callbacks.saml_metadata", _external=True)


def acs_url():
    return url_for("auth_callbacks.saml_acs", _external=True)


def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_time(value):
    value = value.strip().replace("Z", "+00:00")
    if "." in value:  # drop fractional seconds of any precision
        head, _, rest = value.partition(".")
        tz = rest[rest.find("+"):] if "+" in rest else (rest[rest.find("-"):] if "-" in rest else "")
        value = head + tz
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def new_request_id():
    return "_" + secrets.token_hex(16)


def build_login_redirect(relay_state="", request_id=None):
    """Returns (redirect_url, request_id)."""
    request_id = request_id or new_request_id()
    xml = (
        f'<samlp:AuthnRequest xmlns:samlp="{NS["samlp"]}" xmlns:saml="{NS["saml"]}" ID="{request_id}" '
        f'Version="2.0" IssueInstant="{_iso(_now())}" Destination={quoteattr(current_app.config["SAML_IDP_SSO_URL"])} '
        f'ProtocolBinding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST" AssertionConsumerServiceURL={quoteattr(acs_url())}>'
        f'<saml:Issuer>{escape(sp_entity_id())}</saml:Issuer>'
        f'<samlp:NameIDPolicy Format="urn:oasis:names:tc:SAML:1.1:nameid-format:unspecified" AllowCreate="true"/>'
        f'</samlp:AuthnRequest>'
    )
    deflated = zlib.compress(xml.encode())[2:-4]  # raw DEFLATE
    params = {"SAMLRequest": base64.b64encode(deflated).decode()}
    if relay_state:
        params["RelayState"] = relay_state
    sso = current_app.config["SAML_IDP_SSO_URL"]
    return f"{sso}{'&' if '?' in sso else '?'}{urlencode(params)}", request_id


def sp_metadata():
    return (
        f'<?xml version="1.0"?><md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata" '
        f'entityID={quoteattr(sp_entity_id())}><md:SPSSODescriptor AuthnRequestsSigned="false" '
        f'WantAssertionsSigned="true" protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">'
        f'<md:NameIDFormat>urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress</md:NameIDFormat>'
        f'<md:AssertionConsumerService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST" '
        f'Location={quoteattr(acs_url())} index="0" isDefault="true"/></md:SPSSODescriptor></md:EntityDescriptor>'
    )


def _parser():
    return etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False, remove_comments=True)


def parse_response(saml_response_b64, expected_request_id):
    """Validate a POSTed SAMLResponse. Returns {'name_id','email','name','groups'}."""
    from signxml import XMLVerifier
    from signxml.exceptions import InvalidSignature

    cfg = current_app.config
    try:
        raw = base64.b64decode(saml_response_b64 or "", validate=False)
        doc = etree.fromstring(raw, parser=_parser())
    except (ValueError, etree.XMLSyntaxError) as exc:
        raise SamlError("The SAML response could not be read.") from exc
    if doc.tag != f"{{{NS['samlp']}}}Response":
        raise SamlError("Not a SAML response.")
    status = doc.find("samlp:Status/samlp:StatusCode", NS)
    if status is None or status.get("Value") != "urn:oasis:names:tc:SAML:2.0:status:Success":
        raise SamlError("The identity provider did not authenticate the user.")

    cert = cfg["SAML_IDP_CERT_FILE"].read_text()
    try:
        verified = XMLVerifier().verify(raw, x509_cert=cert, parser=_parser())
    except InvalidSignature as exc:
        raise SamlError("The SAML signature is invalid.") from exc
    except Exception as exc:
        raise SamlError("The SAML response is not signed correctly.") from exc
    signed = verified.signed_xml if not isinstance(verified, list) else verified[0].signed_xml
    if signed.tag == f"{{{NS['saml']}}}Assertion":
        assertion = signed
    else:
        found = signed.findall("saml:Assertion", NS)
        if len(found) != 1:
            raise SamlError("Expected exactly one signed assertion.")
        assertion = found[0]
        if signed.get("InResponseTo") not in (None, expected_request_id):
            raise SamlError("The SAML response does not match the login request.")
        if signed.get("Destination") not in (None, acs_url()):
            raise SamlError("The SAML response was sent to a different service.")

    now = _now()
    issuer = (assertion.findtext("saml:Issuer", namespaces=NS) or "").strip()
    if cfg.get("SAML_IDP_ENTITY_ID") and issuer != cfg["SAML_IDP_ENTITY_ID"]:
        raise SamlError("The assertion was issued by an unexpected identity provider.")
    conditions = assertion.find("saml:Conditions", NS)
    if conditions is not None:
        nb, noa = conditions.get("NotBefore"), conditions.get("NotOnOrAfter")
        if nb and now + SKEW < _parse_time(nb):
            raise SamlError("The assertion is not yet valid.")
        if noa and now - SKEW >= _parse_time(noa):
            raise SamlError("The assertion has expired.")
        audiences = [a.text.strip() for a in conditions.findall("saml:AudienceRestriction/saml:Audience", NS) if a.text]
        if audiences and sp_entity_id() not in audiences:
            raise SamlError("The assertion is intended for a different service.")
    confirmed = False
    for sc in assertion.findall("saml:Subject/saml:SubjectConfirmation", NS):
        if sc.get("Method") != "urn:oasis:names:tc:SAML:2.0:cm:bearer":
            continue
        data = sc.find("saml:SubjectConfirmationData", NS)
        if data is None:
            continue
        if data.get("Recipient") and data.get("Recipient") != acs_url():
            continue
        if data.get("NotOnOrAfter") and now - SKEW >= _parse_time(data.get("NotOnOrAfter")):
            continue
        if not expected_request_id or data.get("InResponseTo") != expected_request_id:
            continue  # only SP-initiated logins started from this browser are accepted
        confirmed = True
    if not confirmed:
        raise SamlError("The assertion's subject confirmation is not valid for this login.")
    if not expected_request_id:
        raise SamlError("No SAML login was started from this browser.")

    name_id = (assertion.findtext("saml:Subject/saml:NameID", namespaces=NS) or "").strip()
    attrs = {}
    for attr in assertion.findall("saml:AttributeStatement/saml:Attribute", NS):
        values = [v.text.strip() for v in attr.findall("saml:AttributeValue", NS) if v.text]
        for key in (attr.get("Name"), attr.get("FriendlyName")):
            if key:
                attrs[key] = values

    def first(name):
        vals = attrs.get(name) or []
        return vals[0] if vals else ""

    email = first(cfg["SAML_ATTR_EMAIL"]) or (name_id if "@" in name_id else "")
    if not (name_id or email):
        raise SamlError("The assertion does not identify the user.")
    return {"name_id": name_id or email, "email": email, "name": first(cfg["SAML_ATTR_NAME"]),
            "groups": attrs.get(cfg["SAML_ATTR_GROUPS"], [])}
