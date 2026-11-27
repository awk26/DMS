"""Document Validity Management & Expiry Monitoring: end-to-end behaviour against the MySQL test database.

Gemini is always replaced by ``FakeAI`` and email by a fake mailer: no test calls Google or an SMTP
server. Business dates are driven explicitly (``run_daily(today=...)``), so the countdown can be checked
day by day.
"""
import io
import json
import threading
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import update

from dms.extensions import db
from dms.models import ActivityLog, Document, Folder, utcnow
from dms.validity import clock
from dms.validity import jobs as vjobs
from dms.validity.gemini import TransientAIError
from dms.validity.models import (
    DocumentValidity, Notification, ValidityAnalysis, ValidityException, ValidityJob, ValiditySchedulerRun,
)
from dms.validity.scheduler import check_documents, run_daily

from .conftest import PDF, Api, make_user

TEXT = b"VESSEL OPERATING LICENSE\nLicence No. VL-12345\nVessel: MV Example\nValid until 2027-01-01\n"


# --------------------------------------------------------------------------- fakes and helpers

def answer(**over):
    data = {"document_type": "Vessel Operating License", "document_title": "Operating License",
            "document_number": "VL-12345", "vessel_name": "MV Example", "issuing_authority": "Port Authority",
            "issue_date": "2026-01-01", "expiry_date": "2027-01-01", "has_expiry": True,
            "expiry_date_ambiguous": False, "confidence": 0.97}
    data.update(over)
    return json.dumps(data)


class FakeAI:
    model_name = "fake-gemini"

    def __init__(self):
        self.responses, self.calls = [], []

    def analyse(self, **payload):
        self.calls.append(payload)
        item = self.responses.pop(0) if self.responses else answer()
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def ai(app):
    fake = FakeAI()
    app.extensions["validity_ai_client"] = fake
    yield fake
    app.extensions.pop("validity_ai_client", None)
    vjobs._prechecked.clear()  # pre-check answers are kept per process; never share them between tests


@pytest.fixture
def alice(app, admin):
    return Api(app, make_user("alice", ("Contributor",), email="alice@example.com", display_name="Alice"))


@pytest.fixture
def bob(app, admin):
    return Api(app, make_user("bob", ("Contributor",), email="bob@example.com", display_name="Bob"))


def T(days=0):
    return clock.today() + timedelta(days=days)


def home(client):
    from dms.services.folders import ensure_home
    folder = ensure_home(client.user)
    db.session.commit()
    return folder.id


def monitored_folder(client, name="Certificates", parent=None, mode="enabled"):
    r = client.post("/folders", json={"name": name, "parent_id": parent or home(client), "validity_monitoring": mode})
    assert r.status_code == 201, r.json
    return r.json["folder"]["id"]


def confirm(client, doc, expiry, **over):
    body = {"document_type": "Vessel Operating License", "vessel_name": "MV Example", "document_number": "VL-1",
            "issue_date": (T(-300)).isoformat(), "expiry_date": expiry.isoformat() if expiry else "",
            "has_expiry": expiry is not None, **over}
    return client.put(f"/documents/{doc}/validity", json=body)


def tracked(client, expiry, name="licence.txt", folder=None):
    """A document in a monitored folder whose expiry date has been confirmed."""
    folder = folder or monitored_folder(client)
    doc = client.doc(name, TEXT, folder_id=folder)
    r = confirm(client, doc, expiry)
    assert r.status_code == 200, r.json
    return doc


def daily(day, owner="test-worker"):
    db.session.commit()
    return run_daily(today=day, owner=owner)


def process(limit=10):
    db.session.commit()
    return vjobs.run_due_jobs(owner="test-worker", limit=limit)


def make_due():
    db.session.execute(update(ValidityJob).values(next_attempt_at=utcnow() - timedelta(minutes=1)))
    db.session.commit()


def notes(user_id):
    db.session.rollback()
    return db.session.query(Notification).filter_by(user_id=user_id).order_by(Notification.id).all()


def jobs_for(doc):
    db.session.rollback()
    return db.session.query(ValidityJob).filter_by(document_id=doc).order_by(ValidityJob.id).all()


def record(doc):
    db.session.rollback()
    return db.session.query(DocumentValidity).filter_by(document_id=doc).one_or_none()


def validity(client, doc):
    r = client.get(f"/documents/{doc}/validity")
    assert r.status_code == 200, r.json
    return r.json


def valid_pdf():
    from pypdf import PdfWriter
    writer, out = PdfWriter(), io.BytesIO()
    writer.add_blank_page(width=200, height=200)
    writer.write(out)
    return out.getvalue()


# =========================================================================== 1-4 folder and document settings

def test_folder_creation_checkbox_and_regression(alice):
    on = monitored_folder(alice, "Licences")
    s = alice.get(f"/folders/{on}/validity").json
    assert (s["mode"], s["effective"], s["can_edit"]) == ("enabled", True, True)
    # Without the new field folder creation is unchanged and monitoring is off.
    r = alice.post("/folders", json={"name": "Plain", "parent_id": home(alice)})
    assert r.status_code == 201 and set(r.json) == {"ok", "folder"}
    s = alice.get(f"/folders/{r.json['folder']['id']}/validity").json
    assert (s["mode"], s["effective"]) == ("inherit", False)
    # An invalid value is refused before anything is created.
    bad = alice.post("/folders", json={"name": "Bad", "parent_id": home(alice), "validity_monitoring": "maybe"})
    assert bad.status_code == 400
    assert db.session.query(Folder).filter_by(name="Bad").count() == 0


def test_disabled_folder_does_not_queue_analysis(alice, ai):
    folder = alice.folder("Plain", home(alice))
    doc = alice.doc("licence.txt", TEXT, folder_id=folder)
    assert jobs_for(doc) == [] and record(doc) is None
    assert process() == 0 and ai.calls == []


def test_nested_folder_inheritance_and_explicit_overrides(alice):
    parent = monitored_folder(alice, "Fleet")
    child = alice.folder("MV Example", parent)
    s = alice.get(f"/folders/{child}/validity").json
    assert (s["mode"], s["effective"], s["inherited_from"]["id"]) == ("inherit", True, parent)
    grandchild = alice.folder("Archive", child)
    assert alice.put(f"/folders/{grandchild}/validity", json={"mode": "disabled"}).json["effective"] is False
    below = alice.folder("Old", grandchild)
    assert alice.get(f"/folders/{below}/validity").json["effective"] is False   # inherits the opt-out

    in_child = alice.doc("a.txt", TEXT, folder_id=child)
    in_grandchild = alice.doc("b.txt", TEXT, folder_id=grandchild)
    assert len(jobs_for(in_child)) == 1 and jobs_for(in_grandchild) == []
    # Back to inherit: the grandchild follows the parent again.
    assert alice.put(f"/folders/{grandchild}/validity", json={"mode": "inherit"}).json["effective"] is True


def test_folder_disable_keeps_existing_data_and_stops_notices(alice):
    folder = monitored_folder(alice)
    doc = tracked(alice, T(5), folder=folder)
    assert alice.put(f"/folders/{folder}/validity", json={"mode": "disabled"}).status_code == 200
    assert daily(T()) is not None and notes(alice.user.id) == []
    assert record(doc).expiry_date == T(5)                   # nothing deleted
    later = alice.doc("new.txt", TEXT, folder_id=folder)
    assert jobs_for(later) == []                              # no new analyses
    alice.put(f"/folders/{folder}/validity", json={"mode": "enabled"})
    daily(T(1))
    assert len(notes(alice.user.id)) == 1


def test_enable_folder_with_analyse_existing(alice):
    folder = alice.folder("Existing", home(alice))
    docs = [alice.doc(f"{i}.txt", TEXT + bytes([65 + i]), folder_id=folder) for i in range(2)]
    r = alice.put(f"/folders/{folder}/validity", json={"mode": "enabled", "analyse_existing": True})
    assert r.status_code == 200 and r.json["queued"] == 2
    assert all(len(jobs_for(d)) == 1 for d in docs)


def test_only_editors_change_folder_settings(alice, bob):
    folder = monitored_folder(alice)
    assert bob.get(f"/folders/{folder}/validity").status_code == 404
    alice.post(f"/permissions/folder/{folder}/share",
               json={"principal_type": "user", "principal_id": bob.user.id, "level": "view"})
    assert bob.get(f"/folders/{folder}/validity").json["can_edit"] is False
    assert bob.put(f"/folders/{folder}/validity", json={"mode": "disabled"}).status_code == 403


# =========================================================================== 5-9 AI analysis

def test_gemini_successful_extraction(alice, ai):
    doc = alice.doc("licence.txt", TEXT, folder_id=monitored_folder(alice))
    assert process() == 1
    assert "VL-12345" in ai.calls[0]["text"]                  # text formats go as extracted text
    v = validity(alice, doc)
    assert v["record"]["verification_status"] == "requires_confirmation"
    assert (v["record"]["expiry_date"], v["record"]["vessel_name"]) == ("2027-01-01", "MV Example")
    assert v["analyses"][0]["status"] == "succeeded" and v["job"]["status"] == "succeeded"
    assert v["summary"]["alerts_active"] is False             # not verified yet: no notices


def test_pdf_is_sent_to_the_ai_as_a_document(alice, ai):
    alice.doc("cert.pdf", valid_pdf(), folder_id=monitored_folder(alice))
    process()
    assert ai.calls[0]["mime_type"] == "application/pdf" and ai.calls[0]["data"].startswith(b"%PDF")


def test_corrupted_pdf_is_marked_unsupported_without_calling_the_ai(alice, ai):
    doc = alice.doc("broken.pdf", PDF, folder_id=monitored_folder(alice))
    process()
    assert ai.calls == [] and record(doc).verification_status == "unsupported"
    assert jobs_for(doc)[0].status == "failed"


def test_invalid_ai_response_is_retried_then_fails(app, alice, ai):
    app.config["VALIDITY_JOB_MAX_ATTEMPTS"] = 2
    ai.responses = ["this is not json", '{"has_expiry": "maybe", "confidence": 1}']
    doc = alice.doc("licence.txt", TEXT, folder_id=monitored_folder(alice))
    process()
    job = jobs_for(doc)[0]
    assert (job.status, job.attempts) == ("queued", 1) and job.next_attempt_at > utcnow()
    assert process() == 0                                     # backoff: not due yet
    make_due()
    process()
    job = jobs_for(doc)[0]
    assert (job.status, job.attempts) == ("failed", 2)
    assert record(doc).verification_status == "analysis_failed"
    statuses = [a.status for a in db.session.query(ValidityAnalysis).filter_by(document_id=doc)
                .order_by(ValidityAnalysis.id)]
    assert statuses == ["invalid_response", "invalid_response"]


def test_ai_failure_is_retried_with_backoff_then_succeeds(alice, ai):
    ai.responses = [TransientAIError("timeout"), TransientAIError("rate limit", retry_after=5), answer()]
    doc = alice.doc("licence.txt", TEXT, folder_id=monitored_folder(alice))
    for _ in range(3):
        process()
        make_due()
    job = jobs_for(doc)[0]
    assert (job.status, job.attempts) == ("succeeded", 3)
    assert record(doc).verification_status == "requires_confirmation"


def test_upload_never_fails_when_analysis_queueing_breaks(alice, monkeypatch):
    folder = monitored_folder(alice)

    def boom(*a, **k):
        raise RuntimeError("queue down")

    monkeypatch.setattr(vjobs, "enqueue_versions", boom)
    assert alice.upload("licence.txt", TEXT, folder_id=folder).status_code == 201
    monkeypatch.undo()
    doc = db.session.query(Document).filter_by(name="licence.txt").one().id
    assert jobs_for(doc) == []
    assert vjobs.reconcile() == 1 and len(jobs_for(doc)) == 1     # the sweep catches up


def test_without_ai_configuration_jobs_wait_and_manual_entry_works(alice):
    doc = alice.doc("licence.txt", TEXT, folder_id=monitored_folder(alice))
    assert process() == 0
    job = jobs_for(doc)[0]
    assert (job.status, job.attempts) == ("queued", 0)
    assert validity(alice, doc)["ai_configured"] is False
    assert confirm(alice, doc, T(100)).json["record"]["verification_status"] == "verified"


def test_manual_correction_is_validated_and_audited(alice, ai):
    doc = alice.doc("licence.txt", TEXT, folder_id=monitored_folder(alice))
    process()
    assert confirm(alice, doc, T(10), issue_date=T(20).isoformat()).status_code == 400   # expiry before issue
    assert alice.put(f"/documents/{doc}/validity", json={"has_expiry": True, "expiry_date": ""}).status_code == 400
    r = confirm(alice, doc, T(45))
    rec = r.json["record"]
    assert (rec["verification_status"], rec["source"], rec["expiry_date"]) == ("verified", "manual", T(45).isoformat())
    events = alice.get(f"/documents/{doc}/validity/history").json["events"]
    corrected = next(e for e in events if e["action"] == "validity_corrected")
    assert corrected["changes"]["expiry_date"] == {"old": "2027-01-01", "new": T(45).isoformat()}


def test_confirming_ai_values_unchanged_is_recorded_as_ai(alice, ai):
    ai.responses = [answer(issue_date=T(-300).isoformat(), expiry_date=T(200).isoformat())]
    doc = alice.doc("licence.txt", TEXT, folder_id=monitored_folder(alice))
    process()
    r = alice.put(f"/documents/{doc}/validity", json={})       # confirm as extracted
    assert (r.json["record"]["verification_status"], r.json["record"]["source"]) == ("verified", "ai")


def test_document_without_expiry_never_notifies(alice, ai):
    ai.responses = [answer(has_expiry=False, expiry_date=None)]
    doc = alice.doc("manual.txt", TEXT, folder_id=monitored_folder(alice))
    process()
    r = alice.put(f"/documents/{doc}/validity", json={})
    assert r.json["summary"]["status"] == "no_expiry"
    daily(T())
    assert notes(alice.user.id) == []


# =========================================================================== expiry milestone alerts

def test_thirty_day_notification_skips_non_milestone_day(alice):
    doc = tracked(alice, T(30))
    daily(T())
    daily(T(1))
    titles = [n.title for n in notes(alice.user.id)]
    assert titles == ["Vessel Operating License expires in 30 days."]
    first = notes(alice.user.id)[0]
    assert first.link == f"/documents/{doc}#validity" and first.details["vessel_name"] == "MV Example"
    assert first.details["expiry_date"] == T(30).isoformat()


def test_no_notice_before_the_thirty_day_window(alice):
    tracked(alice, T(31))
    daily(T())
    assert notes(alice.user.id) == []


def test_milestone_countdown_expiry_day_and_stop_after_expiry(alice):
    tracked(alice, T(3))
    for i in range(6):
        daily(T(i))
    assert [n.title.replace("Vessel Operating License ", "") for n in notes(alice.user.id)] == [
        "expires in 3 days.", "expires in 2 days.", "expires in 1 day.", "expires today."]
    assert [n.kind for n in notes(alice.user.id)][3:] == ["expires_today"]


def test_duplicate_scheduler_execution_creates_no_duplicates(alice):
    tracked(alice, T(5))
    assert daily(T()) is not None
    assert daily(T()) is None                                  # day already completed
    db.session.commit()
    assert check_documents(T())["notifications_created"] == 0  # even bypassing the claim: DB constraint
    assert len(notes(alice.user.id)) == 1
    assert db.session.query(ValiditySchedulerRun).filter_by(run_date=T(), status="completed").count() == 1


def test_concurrent_scheduler_instances(app, alice):
    tracked(alice, T(5))
    db.session.commit()
    barrier, results = threading.Barrier(4), []

    def worker(i):
        with app.app_context():
            try:
                barrier.wait()
                results.append(run_daily(today=T(), owner=f"instance-{i}"))
            finally:
                db.session.remove()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(1 for r in results if r is not None) == 1
    assert len(notes(alice.user.id)) == 1


def test_crashed_daily_run_is_taken_over(alice):
    tracked(alice, T(5))
    db.session.add(ValiditySchedulerRun(run_date=T(), status="running", worker_id="dead",
                                        lease_until=utcnow() - timedelta(minutes=1)))
    db.session.commit()
    assert daily(T(), owner="survivor") is not None
    assert len(notes(alice.user.id)) == 1


# =========================================================================== 16-17 document controls

def test_temporary_pause_and_automatic_resume_without_backlog(alice):
    doc = tracked(alice, T(10))
    assert alice.post(f"/documents/{doc}/validity/monitoring",
                      json={"state": "paused", "paused_until": T(-1).isoformat()}).status_code == 400
    r = alice.post(f"/documents/{doc}/validity/monitoring",
                   json={"state": "paused", "paused_until": T(2).isoformat(), "reason": "Renewal in progress"})
    assert r.json["record"]["monitoring_state"] == "paused"
    for i in range(3):
        daily(T(i))
    assert notes(alice.user.id) == []
    daily(T(5))                                                # days 3 and 4 never ran: no backlog either
    assert [n.title for n in notes(alice.user.id)] == ["Vessel Operating License expires in 5 days."]
    assert record(doc).monitoring_state == "active"
    assert db.session.query(ActivityLog).filter_by(action="validity_monitoring_resumed", entity_id=doc).count() == 1


def test_permanent_disable_and_re_enable(alice):
    doc = tracked(alice, T(5))
    alice.post(f"/documents/{doc}/validity/monitoring", json={"state": "disabled"})
    daily(T())
    assert notes(alice.user.id) == []
    assert record(doc).expiry_date == T(5)                     # data kept
    alice.post(f"/documents/{doc}/validity/monitoring", json={"state": "active"})
    daily(T(1))
    assert [n.title for n in notes(alice.user.id)] == ["Vessel Operating License expires in 4 days."]


def test_disabled_document_does_not_analyse_new_versions(alice, ai):
    doc = alice.doc("licence.txt", TEXT, folder_id=monitored_folder(alice))
    process()
    alice.post(f"/documents/{doc}/validity/monitoring", json={"state": "disabled"})
    assert alice.version(doc, "licence.txt", TEXT + b"v2").status_code == 201
    assert len(jobs_for(doc)) == 1


# =========================================================================== 18-19 acceptance exceptions

def exception(client, doc, **over):
    body = {"scope": "port", "port_name": "Mumbai", "reason": "Port authority accepts the current licence",
            "reference_number": "MbPT/2026/77", "start_date": T(-1).isoformat(), "end_date": T(3).isoformat(), **over}
    return client.post(f"/documents/{doc}/validity/exceptions", json=body)


def test_expiry_alerts_stop_after_expiry_even_if_accepted(alice):
    doc = tracked(alice, T(-2))
    assert exception(alice, doc, end_date="", indefinite_justification="").status_code == 400  # unjustified
    r = exception(alice, doc)
    assert r.status_code == 201, r.json
    daily(T())
    assert notes(alice.user.id) == []
    summary = validity(alice, doc)["summary"]
    assert (summary["status"], summary["accepted"], summary["expiry_date"]) == ("expired", True, T(-2).isoformat())
    assert record(doc).expiry_date == T(-2)


def test_exception_end_and_withdrawal_do_not_resume_post_expiry_alerts(alice):
    doc = tracked(alice, T(-2))
    exception(alice, doc, end_date=T(1).isoformat())
    daily(T(1))
    assert notes(alice.user.id) == []
    daily(T(2))
    assert notes(alice.user.id) == []

    other = tracked(alice, T(-1), name="permit.txt", folder=monitored_folder(alice, "Permits"))
    exc = exception(alice, other, end_date="", indefinite_justification="Accepted by all ports until further notice",
                    scope="all", port_name="").json["exception"]
    daily(T(3))
    assert all(n.entity_id != other for n in notes(alice.user.id))
    r = alice.post(f"/documents/{other}/validity/exceptions/{exc['id']}/withdraw", json={"reason": "Port changed rule"})
    assert r.json["exceptions"][0]["status"] == "withdrawn" and r.json["exceptions"][0]["withdrawn_by"] == "Alice"
    daily(T(4))
    assert notes(alice.user.id) == []
    assert db.session.query(ValidityException).count() == 2    # history kept


def test_exception_evidence_follows_document_permissions(alice, bob):
    doc = tracked(alice, T(-2))
    r = alice.post(f"/documents/{doc}/validity/exceptions", data={
        "scope": "port", "port_name": "Kandla", "reason": "Accepted by harbour master",
        "start_date": T().isoformat(), "end_date": T(5).isoformat(),
        "evidence": (io.BytesIO(b"Letter from the harbour master"), "letter.txt")}, content_type="multipart/form-data")
    assert r.status_code == 201, r.json
    eid = r.json["exception"]["id"]
    assert alice.get(f"/documents/{doc}/validity/exceptions/{eid}/evidence").data == b"Letter from the harbour master"
    assert bob.get(f"/documents/{doc}/validity/exceptions/{eid}/evidence").status_code == 404
    alice.post(f"/permissions/document/{doc}/share",
               json={"principal_type": "user", "principal_id": bob.user.id, "level": "view"})
    assert bob.get(f"/documents/{doc}/validity/exceptions/{eid}/evidence").status_code == 403   # view only
    assert bob.post(f"/documents/{doc}/validity/exceptions/{eid}/withdraw", json={}).status_code == 403


# =========================================================================== 20-21 versions and deletion

def test_document_replacement_is_reanalysed_and_restores_reuse_results(alice, ai):
    doc = alice.doc("licence.txt", TEXT, folder_id=monitored_folder(alice))
    process()
    confirm(alice, doc, T(5))
    daily(T())
    assert len(notes(alice.user.id)) == 1
    assert alice.version(doc, "licence.txt", TEXT + b"renewed").status_code == 201
    assert len(jobs_for(doc)) == 2
    v = validity(alice, doc)
    assert v["record"]["stale"] is True and v["summary"]["alerts_active"] is False
    daily(T(1))
    assert len(notes(alice.user.id)) == 1                      # unverified new version: no notices
    process()
    assert len(ai.calls) == 2 and record(doc).verification_status == "requires_confirmation"
    confirm(alice, doc, T(400))
    daily(T(2))
    assert len(notes(alice.user.id)) == 1                      # renewed: outside the window
    # Restoring version 1 brings back identical content: its analysis is reused, no new AI call.
    assert alice.post(f"/documents/{doc}/versions/1/restore", json={"idempotency_key": str(uuid.uuid4())}).status_code \
        in (200, 201)
    process()
    assert len(ai.calls) == 2
    assert db.session.query(ValidityAnalysis).filter_by(document_id=doc, status="reused").count() == 1


def test_deleted_document_is_skipped_and_restored_document_resumes(alice, ai):
    folder = monitored_folder(alice)
    doc = tracked(alice, T(5), folder=folder)
    queued = alice.doc("pending.txt", TEXT, folder_id=folder)
    assert alice.delete(f"/documents/{doc}").status_code == 200
    assert alice.delete(f"/documents/{queued}").status_code == 200
    daily(T())
    assert notes(alice.user.id) == []
    process()
    assert jobs_for(queued)[0].status == "cancelled" and ai.calls == []
    assert alice.post(f"/documents/{doc}/restore").status_code == 200
    daily(T(1))
    assert len(notes(alice.user.id)) == 1


# =========================================================================== 22 privacy

def test_privacy_isolation_and_no_admin_privilege(api, alice, bob):
    doc = tracked(alice, T(5))
    for other in (bob, api):                                   # api = administrator
        assert other.get(f"/documents/{doc}/validity").status_code == 404
        assert other.put(f"/documents/{doc}/validity", json={"expiry_date": T(900).isoformat()}).status_code == 404
        assert other.get("/validity/documents").json["total"] == 0
    alice.post(f"/permissions/document/{doc}/share",
               json={"principal_type": "user", "principal_id": bob.user.id, "level": "view"})
    v = validity(bob, doc)
    assert v["can_edit"] is False and v["subscribed"] is False
    assert confirm(bob, doc, T(900)).status_code == 403
    assert bob.post(f"/documents/{doc}/validity/monitoring", json={"state": "disabled"}).status_code == 403
    assert bob.put(f"/documents/{doc}/validity/subscription", json={"subscribed": True}).json["subscribed"] is True
    daily(T())
    assert len(notes(alice.user.id)) == 1 and len(notes(bob.user.id)) == 1 and notes(api.user.id) == []
    # Bob loses access: no new notices, and the old one is no longer shown.
    from dms.services.access import set_entry
    set_entry(db.session.get(Document, doc), "user", bob.user.id, set())
    daily(T(1))
    assert len(notes(bob.user.id)) == 1
    assert bob.get("/notifications").json["notifications"] == []
    assert bob.get("/validity/documents").json["total"] == 0


def test_administrator_uses_the_module_for_their_own_documents(api):
    doc = tracked(api, T(2))
    assert validity(api, doc)["can_edit"] is True
    daily(T())
    assert len(notes(api.user.id)) == 1


# =========================================================================== 24 worker recovery

def test_worker_crash_job_recovery_and_lease_fencing(alice, ai):
    doc = alice.doc("licence.txt", TEXT, folder_id=monitored_folder(alice))
    db.session.commit()
    job_id = vjobs.claim_next("worker-A")
    assert job_id is not None and vjobs.claim_next("worker-B") is None      # held by A
    db.session.execute(update(ValidityJob).values(locked_until=utcnow() - timedelta(seconds=1)))  # A crashed
    db.session.commit()
    assert vjobs.claim_next("worker-B") == job_id
    assert vjobs.process_job(job_id, "worker-A") == "lost"                   # A's late result is discarded
    assert vjobs.process_job(job_id, "worker-B") == "succeeded"
    job = jobs_for(doc)[0]
    assert (job.status, job.attempts) == ("succeeded", 2) and len(ai.calls) == 1


def test_one_job_per_version(alice):
    doc = alice.doc("licence.txt", TEXT, folder_id=monitored_folder(alice))
    version = db.session.get(Document, doc).current_version_id
    assert vjobs.enqueue_versions([version]) == 0
    db.session.commit()
    assert vjobs.reconcile() == 0 and len(jobs_for(doc)) == 1


def test_manual_reanalysis_resets_the_job(alice, ai):
    doc = alice.doc("licence.txt", TEXT, folder_id=monitored_folder(alice))
    process()
    r = alice.post(f"/documents/{doc}/validity/analyse")
    assert r.status_code == 202 and r.json["job"]["status"] == "queued"
    process()
    assert len(ai.calls) == 2 and len(jobs_for(doc)) == 1


# =========================================================================== notification centre and email

def test_notification_centre(alice):
    tracked(alice, T(1))
    daily(T())
    r = alice.get("/notifications")
    assert r.json["unread"] == 1 and r.json["notifications"][0]["severity"] == "warning"
    nid = r.json["notifications"][0]["id"]
    assert alice.post(f"/notifications/{nid}/read").json["unread"] == 0
    assert alice.post("/notifications/read-all").status_code == 200
    assert alice.get("/notifications/unread-count").json["unread"] == 0
    assert alice.put("/notifications/preferences", json={"email_mode": "daily"}).json["email_mode"] == "daily"
    assert alice.put("/notifications/preferences", json={"email_mode": "hourly"}).status_code == 400


def test_email_digest_milestones_and_retry(app, alice):
    sent, fail = [], {"on": True}

    def mailer(to, subject, body):
        if fail["on"]:
            raise OSError("smtp down")
        sent.append((to, subject, body))

    app.extensions["validity_mailer"] = mailer
    try:
        tracked(alice, T(30))
        daily(T())                                             # 30 days: a milestone
        n = notes(alice.user.id)[0]
        assert (n.email_status, n.email_attempts) == ("failed", 1)
        fail["on"] = False
        from dms.validity.notifications import deliver_pending
        assert deliver_pending(now=utcnow() + timedelta(hours=1)) == 1
        assert sent[0][0] == "alice@example.com" and "expires in 30 days" in sent[0][2]
        daily(T(1))                                            # 29 days: no alert
        assert len(notes(alice.user.id)) == 1
        daily(T(28))                                           # 2 days: a milestone
        assert notes(alice.user.id)[1].email_status == "sent"
        alice.put("/notifications/preferences", json={"email_mode": "off"})
        daily(T(30))                                           # expiry day, but email is off
        assert notes(alice.user.id)[2].email_status == "none"
    finally:
        app.extensions.pop("validity_mailer", None)


def test_dashboard_filters(alice):
    tracked(alice, T(-3), name="old.txt")
    tracked(alice, T(10), name="soon.txt", folder=monitored_folder(alice, "Soon"))
    tracked(alice, T(200), name="fine.txt", folder=monitored_folder(alice, "Fine"))
    r = alice.get("/validity/documents").json
    assert (r["counts"]["expired"], r["counts"]["expiring"], r["counts"]["valid"]) == (1, 1, 1)
    assert [d["name"] for d in alice.get("/validity/documents?status=expiring").json["documents"]] == ["soon.txt"]
    assert alice.get("/validity/documents?status=bogus").status_code == 400


def test_operational_status_is_admin_only(api, alice):
    assert api.get("/validity/status").status_code == 200
    assert alice.get("/validity/status").status_code == 403


# =========================================================================== expired on upload (warning only)

def test_expired_document_is_saved_and_only_the_uploader_is_warned_in_the_app(alice, ai):
    ai.responses.append(answer(issue_date=T(-400).isoformat(), expiry_date=T(-1).isoformat()))
    doc = alice.doc("licence.txt", TEXT, folder_id=monitored_folder(alice))
    assert process() == 1
    r = record(doc)
    assert r.expiry_date == T(-1) and r.verification_status == "requires_confirmation"   # workflow unchanged
    assert db.session.get(Document, doc).deleted_at is None                               # saved, not rejected
    warnings = [n for n in notes(alice.user.id) if n.kind == "expired_on_upload"]
    assert len(warnings) == 1
    assert warnings[0].severity == "warning" and warnings[0].email_status == "none"       # in the app only
    assert validity(alice, doc)["record"]["expired_on_upload"] is True
    assert db.session.query(ActivityLog).filter_by(action="validity_expired_on_upload", entity_id=doc).count() == 1

    # A manual re-analysis of the same version does not warn again.
    ai.responses.append(answer(issue_date=T(-400).isoformat(), expiry_date=T(-1).isoformat()))
    assert alice.post(f"/documents/{doc}/validity/analyse").status_code == 202
    process()
    assert len([n for n in notes(alice.user.id) if n.kind == "expired_on_upload"]) == 1


@pytest.mark.parametrize("days_left", [0, 100])
def test_document_not_yet_expired_at_upload_gets_no_warning(alice, ai, days_left):
    ai.responses.append(answer(issue_date=T(-400).isoformat(), expiry_date=T(days_left).isoformat()))
    doc = alice.doc("licence.txt", TEXT, folder_id=monitored_folder(alice))
    assert process() == 1
    assert [n for n in notes(alice.user.id) if n.kind == "expired_on_upload"] == []
    assert validity(alice, doc)["record"]["expired_on_upload"] is False


def check_upload(client, data, name="licence.txt", **target):
    form = {"file": (io.BytesIO(data), name), **{k: str(v) for k, v in target.items()}}
    return client.post("/validity/check-upload", data=form, content_type="multipart/form-data")


def test_check_before_upload_warns_about_an_expired_file_and_stores_nothing(alice, ai):
    folder = monitored_folder(alice)
    ai.responses.append(answer(issue_date=T(-400).isoformat(), expiry_date=T(-1).isoformat()))
    r = check_upload(alice, TEXT, folder_id=folder)
    assert r.status_code == 200
    assert r.json == {"checked": True, "has_expiry": True, "expiry_date": T(-1).isoformat(), "expired": True}
    assert db.session.query(Document).count() == 0                          # nothing stored

    # Uploading the same file afterwards reuses the answer: no second AI call.
    doc = alice.doc("licence.txt", TEXT, folder_id=folder)
    assert process() == 1 and len(ai.calls) == 1
    assert record(doc).expiry_date == T(-1)
    assert len([n for n in notes(alice.user.id) if n.kind == "expired_on_upload"]) == 1


def test_check_before_upload_of_a_new_version(alice, ai):
    folder = monitored_folder(alice)
    doc = alice.doc("licence.txt", TEXT, folder_id=folder)
    ai.responses.append(answer(expiry_date=T(30).isoformat(), issue_date=T(-300).isoformat()))
    r = check_upload(alice, TEXT + b"renewed", document_id=doc)
    assert r.json["checked"] is True and r.json["expired"] is False


def test_check_before_upload_is_skipped_outside_monitored_folders_and_needs_upload_access(alice, bob, ai):
    plain = alice.folder("Plain", home(alice))
    assert check_upload(alice, TEXT, folder_id=plain).json == {"checked": False}
    assert ai.calls == []
    assert check_upload(bob, TEXT, folder_id=monitored_folder(alice)).status_code in (403, 404)
