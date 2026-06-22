"""Tests for parse_cert_packs — CF certificate_packs response -> min-expiry summary."""
from datetime import date
from projects.fleet_monitoring.cf_api import parse_cert_packs


_TODAY = date(2026, 6, 3)


def _pack(status="active", certs=None, pack_id="pack-1", ca="GoogleTrustServices"):
    return {
        "id": pack_id,
        "status": status,
        "certificate_authority": ca,
        "certificates": certs or [],
    }


def _cert(expires_on, issuer="GoogleTrustServices"):
    return {"expires_on": expires_on, "issuer": issuer}


def test_returns_none_days_when_no_packs():
    out = parse_cert_packs([], _TODAY)
    assert out["min_days_until_expiry"] is None
    assert out["active_pack_count"] == 0


def test_computes_days_until_earliest_cert_expiry():
    packs = [_pack(certs=[_cert("2026-08-15T23:59:59Z")])]
    out = parse_cert_packs(packs, _TODAY)
    # 2026-08-15 minus 2026-06-03 = 73 days
    assert out["min_days_until_expiry"] == 73
    assert out["earliest_expires_on"] == "2026-08-15"
    assert out["active_pack_count"] == 1


def test_picks_earliest_expiry_across_multiple_active_packs():
    packs = [
        _pack(pack_id="pack-A", certs=[_cert("2026-09-30T23:59:59Z")]),
        _pack(pack_id="pack-B", certs=[_cert("2026-06-17T23:59:59Z")]),   # earlier
        _pack(pack_id="pack-C", certs=[_cert("2026-12-01T23:59:59Z")]),
    ]
    out = parse_cert_packs(packs, _TODAY)
    assert out["min_days_until_expiry"] == 14
    assert out["earliest_pack_id"] == "pack-B"
    assert out["active_pack_count"] == 3


def test_skips_non_active_packs():
    packs = [
        _pack(status="pending_validation", certs=[_cert("2026-06-10T00:00:00Z")]),
        _pack(status="initializing",      certs=[_cert("2026-06-12T00:00:00Z")]),
        _pack(status="active",            certs=[_cert("2026-09-01T00:00:00Z")]),
    ]
    out = parse_cert_packs(packs, _TODAY)
    # Only the active pack counts — pending/initializing certs aren't serving.
    assert out["earliest_expires_on"] == "2026-09-01"
    assert out["active_pack_count"] == 1


def test_picks_earliest_cert_within_a_pack():
    # CF can include multiple certs per pack (e.g. ECDSA + RSA bundles).
    packs = [_pack(certs=[
        _cert("2026-10-01T00:00:00Z"),
        _cert("2026-07-15T00:00:00Z"),   # earlier
        _cert("2026-12-01T00:00:00Z"),
    ])]
    out = parse_cert_packs(packs, _TODAY)
    assert out["earliest_expires_on"] == "2026-07-15"


def test_handles_expired_cert_with_negative_days():
    packs = [_pack(certs=[_cert("2026-05-20T00:00:00Z")])]   # 14 days ago
    out = parse_cert_packs(packs, _TODAY)
    assert out["min_days_until_expiry"] == -14


def test_skips_certs_with_unparseable_expiry():
    packs = [_pack(certs=[
        _cert(""),                       # empty
        _cert(None),                     # null
        _cert("garbage"),                # not ISO
        _cert("2026-07-01T00:00:00Z"),   # valid
    ])]
    out = parse_cert_packs(packs, _TODAY)
    assert out["earliest_expires_on"] == "2026-07-01"


def test_falls_back_to_expires_at_field_when_expires_on_missing():
    # Newer CF responses use expires_on; some legacy payloads use expires_at.
    packs = [_pack(certs=[{"expires_at": "2026-09-15T00:00:00Z",
                           "issuer": "DigiCert"}])]
    out = parse_cert_packs(packs, _TODAY)
    assert out["earliest_expires_on"] == "2026-09-15"


def test_captures_issuer_from_earliest_cert():
    packs = [
        _pack(pack_id="pack-A",
              certs=[_cert("2026-10-01T00:00:00Z", issuer="DigiCert")]),
        _pack(pack_id="pack-B",
              certs=[_cert("2026-07-01T00:00:00Z", issuer="GoogleTrustServices")]),
    ]
    out = parse_cert_packs(packs, _TODAY)
    assert out["earliest_issuer"] == "GoogleTrustServices"
    assert out["earliest_pack_id"] == "pack-B"


def test_returns_none_days_when_all_packs_pending():
    packs = [
        _pack(status="pending_validation",
              certs=[_cert("2026-06-10T00:00:00Z")]),
    ]
    out = parse_cert_packs(packs, _TODAY)
    assert out["min_days_until_expiry"] is None
    assert out["active_pack_count"] == 0
