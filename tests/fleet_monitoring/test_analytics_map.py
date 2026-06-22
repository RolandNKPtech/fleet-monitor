"""Tests for the apex -> GA4/GSC mapping resolver."""
import pyarrow as pa
import pyarrow.parquet as pq

from projects.fleet_monitoring import analytics_map


def _write_meta(tmp_path):
    """Materialise a tiny meta/properties.parquet and meta/sites.parquet."""
    meta = tmp_path / "meta"
    meta.mkdir()
    props = pa.Table.from_pylist([
        {"account": "analyticsuser3", "account_id": "a1",
         "account_name": "NKP Medical Marketing",
         "property_id": "111", "property_name": "example.com - GA4"},
        {"account": "analyticsuser3", "account_id": "a1",
         "account_name": "NKP Medical Marketing",
         "property_id": "222", "property_name": "example-intl-client.com - GA4"},
        {"account": "analyticsuser", "account_id": "a2",
         "account_name": "Other", "property_id": "333",
         "property_name": "example.com (legacy UA, migrated)"},
    ])
    sites = pa.Table.from_pylist([
        {"account": "analyticsuser3", "site_url": "sc-domain:example.com",
         "host": "example.com", "permission_level": "siteOwner"},
        {"account": "analyticsuser3",
         "site_url": "https://example-intl-client.com/",
         "host": "example-intl-client.com", "permission_level": "siteOwner"},
    ])
    pq.write_table(props, meta / "properties.parquet")
    pq.write_table(sites, meta / "sites.parquet")
    return tmp_path


def _empty_overrides(tmp_path):
    p = tmp_path / "overrides.yml"
    p.write_text("overrides: []\n", encoding="utf-8")
    return p


def test_auto_match_picks_property_when_name_contains_apex(tmp_path):
    _write_meta(tmp_path)
    overrides = _empty_overrides(tmp_path)
    m = analytics_map.build_mapping(
        ["example.com", "example-intl-client.com"],
        lake_path=tmp_path, overrides_path=overrides)
    # Two properties match "example.com" (ids "111" and "333"); the
    # lexicographic tiebreak in build_mapping picks the smaller string.
    assert m["example.com"]["ga4_property_id"] == "111"
    assert m["example-intl-client.com"]["ga4_property_id"] == "222"


def test_gsc_auto_match_is_direct_equality_on_host(tmp_path):
    _write_meta(tmp_path)
    overrides = _empty_overrides(tmp_path)
    m = analytics_map.build_mapping(
        ["example.com", "example-clinic.com"],
        lake_path=tmp_path, overrides_path=overrides)
    assert m["example.com"]["gsc_site_url"] == "sc-domain:example.com"
    assert m["example-clinic.com"]["gsc_site_url"] is None   # no GSC row -> no coverage


def test_no_match_returns_none_for_both_blocks(tmp_path):
    _write_meta(tmp_path)
    overrides = _empty_overrides(tmp_path)
    m = analytics_map.build_mapping(
        ["never-shared.com"], lake_path=tmp_path, overrides_path=overrides)
    assert m["never-shared.com"]["ga4_property_id"] is None
    assert m["never-shared.com"]["gsc_site_url"] is None
    assert m["never-shared.com"]["ga4_source"] is None
    assert m["never-shared.com"]["gsc_source"] is None


def test_override_beats_auto_match(tmp_path):
    _write_meta(tmp_path)
    p = tmp_path / "overrides.yml"
    p.write_text(
        "overrides:\n"
        "  - apex: example.com\n"
        "    ga4_property_id: '999'\n"
        "    gsc_site_url: https://forced.example/\n",
        encoding="utf-8")
    m = analytics_map.build_mapping(
        ["example.com"], lake_path=tmp_path, overrides_path=p)
    assert m["example.com"]["ga4_property_id"] == "999"
    assert m["example.com"]["gsc_site_url"] == "https://forced.example/"
    assert m["example.com"]["ga4_source"] == "override"
    assert m["example.com"]["gsc_source"] == "override"


def test_www_prefix_is_stripped_for_both_ga4_and_gsc(tmp_path):
    _write_meta(tmp_path)
    overrides = _empty_overrides(tmp_path)
    m = analytics_map.build_mapping(
        ["www.example.com"], lake_path=tmp_path, overrides_path=overrides)
    # auto-match looks up the apex without the www. prefix for both signals
    assert m["www.example.com"]["gsc_site_url"] == "sc-domain:example.com"
    assert m["www.example.com"]["ga4_property_id"] == "111"


def test_missing_overrides_file_yields_no_overrides(tmp_path):
    _write_meta(tmp_path)
    # Point at a path that does NOT exist — _load_overrides should handle this
    # by returning {} so auto-match still works for fresh installs.
    m = analytics_map.build_mapping(
        ["example.com"], lake_path=tmp_path,
        overrides_path=tmp_path / "does-not-exist.yml")
    assert m["example.com"]["ga4_property_id"] == "111"
    assert m["example.com"]["ga4_source"] == "auto"
