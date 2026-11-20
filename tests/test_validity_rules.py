"""Pure business rules of the validity module (no database needed).

Also runnable on their own without the MySQL test database:
    python -m pytest --noconftest -p no:cacheprovider tests/test_validity_rules.py
"""
import json
from datetime import date, datetime, timedelta

import pytest
import requests

from dms.validity import clock
from dms.validity.gemini import GeminiClient, PermanentAIError, TransientAIError
from dms.validity.rules import (
    InvalidResponse, backoff_seconds, effective_state, exception_applies, notice_for, validate_extraction,
    validity_status,
)

EXPIRY = date(2026, 12, 19)


# --------------------------------------------------------------------------- countdown

def test_no_notice_before_the_window():
    assert notice_for(EXPIRY, date(2026, 11, 18), 30) is None   # 31 days left


def test_thirty_and_twenty_nine_day_notices():
    first = notice_for(EXPIRY, date(2026, 11, 19), 30)
    assert (first.kind, first.days_left) == ("countdown", 30)
    assert first.message("Vessel Operating License") == "Vessel Operating License expires in 30 days."
    second = notice_for(EXPIRY, date(2026, 11, 20), 30)
    assert second.message("Vessel Operating License") == "Vessel Operating License expires in 29 days."


def test_daily_countdown_has_one_notice_per_day_without_gaps():
    days = [notice_for(EXPIRY, date(2026, 11, 19) + timedelta(days=i), 30) for i in range(31)]
    assert [n.days_left for n in days] == list(range(30, -1, -1))
    assert days[-2].phrase() == "expires in 1 day"


def test_expiry_day_and_overdue_notices():
    today = notice_for(EXPIRY, EXPIRY, 30)
    assert (today.kind, today.phrase(), today.severity) == ("expires_today", "expires today", "danger")
    after = notice_for(EXPIRY, EXPIRY + timedelta(days=1), 30)
    assert (after.kind, after.message("Licence")) == ("expired", "Licence expired 1 day ago.")
    assert notice_for(EXPIRY, EXPIRY + timedelta(days=40), 30).phrase() == "expired 40 days ago"


def test_configurable_start_days():
    assert notice_for(EXPIRY, EXPIRY - timedelta(days=10), 7) is None
    assert notice_for(EXPIRY, EXPIRY - timedelta(days=7), 7).days_left == 7


def test_email_milestones():
    picks = {d for d in range(30, -20, -1) if notice_for(EXPIRY, EXPIRY - timedelta(days=d), 30).email_milestone}
    assert picks == {30, 15, 7, 3, 1, 0, -1, -8, -15}


def test_validity_status():
    assert validity_status(EXPIRY, True, EXPIRY - timedelta(days=60)) == "valid"
    assert validity_status(EXPIRY, True, EXPIRY - timedelta(days=5)) == "expiring"
    assert validity_status(EXPIRY, True, EXPIRY) == "expires_today"
    assert validity_status(EXPIRY, True, EXPIRY + timedelta(days=1)) == "expired"
    assert validity_status(None, False, EXPIRY) == "no_expiry"
    assert validity_status(None, True, EXPIRY) == "unknown"


# --------------------------------------------------------------------------- pauses, exceptions, retries

def test_pause_ends_by_itself_after_its_last_day():
    assert effective_state("paused", date(2026, 12, 1), date(2026, 12, 1)) == "paused"
    assert effective_state("paused", date(2026, 12, 1), date(2026, 12, 2)) == "active"
    assert effective_state("disabled", None, date(2030, 1, 1)) == "disabled"


def test_exception_applies_only_while_active_and_in_range():
    d = date(2026, 12, 25)
    assert exception_applies("active", date(2026, 12, 20), None, d)
    assert exception_applies("active", date(2026, 12, 20), date(2026, 12, 25), d)
    assert not exception_applies("active", date(2026, 12, 20), date(2026, 12, 24), d)   # ended
    assert not exception_applies("active", date(2026, 12, 26), None, d)                 # not started
    assert not exception_applies("withdrawn", date(2026, 12, 20), None, d)


def test_exponential_backoff_is_capped():
    assert [backoff_seconds(n, 60) for n in (1, 2, 3, 4)] == [60, 120, 240, 480]
    assert backoff_seconds(30, 60, cap=3600) == 3600


# --------------------------------------------------------------------------- AI response validation

def answer(**over):
    data = {"document_type": "Vessel Operating License", "document_title": "Operating License",
            "document_number": "VL-12345", "vessel_name": "MV Example", "issuing_authority": "Port Authority",
            "issue_date": "2026-01-01", "expiry_date": "2027-01-01", "has_expiry": True,
            "expiry_date_ambiguous": False, "confidence": 0.98}
    data.update(over)
    return json.dumps(data)


def test_valid_answer_requires_confirmation():
    result, status, reasons = validate_extraction(answer())
    assert status == "requires_confirmation" and reasons == []
    assert result["expiry_date"] == "2027-01-01" and result["document_number"] == "VL-12345"


def test_markdown_fenced_json_is_accepted():
    assert validate_extraction("```json\n" + answer() + "\n```")[1] == "requires_confirmation"


@pytest.mark.parametrize("raw", ["not json", "[1, 2]", json.dumps({"has_expiry": True}),
                                 answer(has_expiry="yes"), answer(confidence="high"), answer(confidence=170), answer(confidence=-0.5),
                                 answer(document_number=123), answer(expiry_date=20270101)])
def test_unusable_answers_are_rejected(raw):
    with pytest.raises(InvalidResponse):
        validate_extraction(raw)


def test_unreadable_date_is_never_guessed():
    result, status, reasons = validate_extraction(answer(expiry_date="19/12/2026"))
    assert result["expiry_date"] is None and status == "needs_review"
    assert any("could not be read" in r for r in reasons)
    result, status, _ = validate_extraction(answer(expiry_date="2026-02-30"))
    assert result["expiry_date"] is None and status == "needs_review"


def test_swapped_issue_and_expiry_dates_need_review():
    _, status, reasons = validate_extraction(answer(issue_date="2027-01-01", expiry_date="2026-01-01"))
    assert status == "needs_review" and any("mixed up" in r for r in reasons)


def test_ambiguous_expiry_needs_review_and_keeps_candidates():
    result, status, _ = validate_extraction(answer(expiry_date_ambiguous=True, other_dates=[
        {"date": "2027-01-01", "label": "valid until"}, {"date": "bad", "label": "x"}]))
    assert status == "needs_review"
    assert result["other_dates"] == [{"date": "2027-01-01", "label": "valid until"}]


def test_document_without_expiry():
    result, status, _ = validate_extraction(answer(has_expiry=False, expiry_date=None))
    assert status == "requires_confirmation" and result["has_expiry"] is False and result["expiry_date"] is None
    _, status, _ = validate_extraction(answer(has_expiry=False))   # contradictory answer
    assert status == "needs_review"


def test_low_confidence_needs_review_and_percentages_are_scaled():
    assert validate_extraction(answer(confidence=0.4))[1] == "needs_review"
    assert validate_extraction(answer(confidence=98))[0]["confidence"] == 0.98


def test_text_fields_are_cleaned_and_truncated():
    result, _, _ = validate_extraction(answer(vessel_name="MV\x00 Ex\x07ample  ", document_number="X" * 900))
    assert result["vessel_name"] == "MV Example" and len(result["document_number"]) == 200


# --------------------------------------------------------------------------- Gemini client error handling

class FakeResponse:
    def __init__(self, status, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}

    def json(self):
        if self._body is None:
            raise ValueError
        return self._body


def ok_body(text):
    return {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}]}


@pytest.mark.parametrize("resp, error", [
    (FakeResponse(429, {}, {"Retry-After": "30"}), TransientAIError),
    (FakeResponse(503, {}), TransientAIError),
    (FakeResponse(403, {}), TransientAIError),
    (FakeResponse(400, {}), PermanentAIError),
    (FakeResponse(200, None), TransientAIError),
    (FakeResponse(200, {"candidates": []}), TransientAIError),
    (FakeResponse(200, {"promptFeedback": {"blockReason": "SAFETY"}}), PermanentAIError),
])
def test_gemini_errors_are_classified(resp, error):
    with pytest.raises(error):
        GeminiClient._result(resp)


def test_gemini_rate_limit_carries_retry_after():
    with pytest.raises(TransientAIError) as info:
        GeminiClient._result(FakeResponse(429, {}, {"Retry-After": "30"}))
    assert info.value.retry_after == 30


def test_gemini_success_and_request_shape(monkeypatch):
    seen = {}

    def fake_post(url, json=None, timeout=None, headers=None):
        seen.update(url=url, body=json, headers=headers)
        return FakeResponse(200, ok_body(answer()))

    monkeypatch.setattr(requests, "post", fake_post)
    client = GeminiClient({"GEMINI_API_KEY": "secret-key", "GEMINI_MODEL": "gemini-test"})
    text = client.analyse(data=b"%PDF-1.4", mime_type="application/pdf")
    assert validate_extraction(text)[1] == "requires_confirmation"
    assert "secret-key" not in seen["url"] and seen["headers"]["x-goog-api-key"] == "secret-key"
    assert seen["url"].endswith("/models/gemini-test:generateContent")
    config = seen["body"]["generationConfig"]
    assert config["responseMimeType"] == "application/json" and config["temperature"] == 0


def test_gemini_timeout_and_network_failures_are_transient(monkeypatch):
    client = GeminiClient({"GEMINI_API_KEY": "k"})
    for exc in (requests.Timeout(), requests.ConnectionError()):
        def boom(*a, _exc=exc, **k):
            raise _exc
        monkeypatch.setattr(requests, "post", boom)
        with pytest.raises(TransientAIError):
            client.analyse(text="x")


# --------------------------------------------------------------------------- business time zone

def test_business_time_zone_offset():
    moment = datetime(2026, 1, 1)
    assert clock.zone("Asia/Kolkata").utcoffset(moment) == timedelta(hours=5, minutes=30)
    assert clock.zone("UTC+04:00").utcoffset(moment) == timedelta(hours=4)
    assert clock.parse_hhmm("07:45").hour == 7 and clock.parse_hhmm("bad").hour == 9


def test_gemini_error_carries_googles_explanation():
    body = {"error": {"code": 404, "message": "This model models/gemini-2.5-flash is no longer available to new users."}}
    with pytest.raises(TransientAIError) as info:
        GeminiClient._result(FakeResponse(404, body))
    assert "no longer available to new users" in str(info.value)
