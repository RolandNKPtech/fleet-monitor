import pytest
import respx
import httpx
from skills.monday.site_status import MondaySiteStatusSkill
from skills.base import SkillStatus


@pytest.fixture
def skill():
    return MondaySiteStatusSkill()


@pytest.mark.asyncio
@respx.mock
async def test_site_active(skill, monkeypatch):
    monkeypatch.setenv("MONDAY_API_TOKEN", "test-token")
    monkeypatch.setenv("MONDAY_BOARD_ID", "123")
    from skills.monday.client import _reset_client
    _reset_client()

    respx.post("https://api.monday.com/v2").mock(
        return_value=httpx.Response(200, json={
            "data": {"boards": [{"items_page": {"items": [
                {"name": "Dr. Jones", "column_values": [
                    {"id": "status", "text": "Working on it"},
                    {"id": "text0", "text": "drjones.com"},
                    {"id": "text1", "text": "Dr. Jones"}
                ]}
            ]}}]}
        })
    )
    result = await skill.run(target="drjones.com")
    assert result.status == SkillStatus.SUCCESS
    assert result.data["status"] == "active"
    assert result.data["domain"] == "drjones.com"
    _reset_client()


@pytest.mark.asyncio
@respx.mock
async def test_site_cancelling(skill, monkeypatch):
    monkeypatch.setenv("MONDAY_API_TOKEN", "test-token")
    monkeypatch.setenv("MONDAY_BOARD_ID", "123")
    from skills.monday.client import _reset_client
    _reset_client()

    respx.post("https://api.monday.com/v2").mock(
        return_value=httpx.Response(200, json={
            "data": {"boards": [{"items_page": {"items": [
                {"name": "Smith Derm", "column_values": [
                    {"id": "status", "text": "Notice to Cancel"},
                    {"id": "text0", "text": "smithderm.com"},
                    {"id": "text1", "text": "Smith Dermatology"}
                ]}
            ]}}]}
        })
    )
    result = await skill.run(target="smithderm.com")
    assert result.status == SkillStatus.SUCCESS
    assert result.data["status"] == "cancelling"
    _reset_client()


@pytest.mark.asyncio
async def test_site_not_found(skill, monkeypatch):
    monkeypatch.setenv("MONDAY_API_TOKEN", "test-token")
    monkeypatch.setenv("MONDAY_BOARD_ID", "123")
    from skills.monday.client import _reset_client
    _reset_client()

    import respx as rx
    with rx.mock:
        rx.post("https://api.monday.com/v2").mock(
            return_value=httpx.Response(200, json={
                "data": {"boards": [{"items_page": {"items": []}}]}
            })
        )
        result = await skill.run(target="unknown.com")
        assert result.status == SkillStatus.WARNING
        assert result.data["status"] == "not_found"
    _reset_client()


@pytest.mark.asyncio
async def test_monday_unavailable_fallback(skill, monkeypatch):
    monkeypatch.delenv("MONDAY_API_TOKEN", raising=False)
    from skills.monday.client import _reset_client
    _reset_client()

    result = await skill.run(target="drjones.com")
    assert result.status == SkillStatus.WARNING
    assert result.data["status"] == "unknown"
    assert "unavailable" in result.message.lower() or "not set" in result.message.lower()
    _reset_client()
