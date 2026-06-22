import pytest
import respx
import httpx
from skills.example.ping import PingSkill
from skills.base import SkillStatus


@pytest.mark.asyncio
@respx.mock
async def test_ping_success():
    respx.get("https://example.com").mock(return_value=httpx.Response(200))
    skill = PingSkill()
    result = await skill.run(target="example.com")
    assert result.status == SkillStatus.SUCCESS
    assert result.data["status_code"] == 200


@pytest.mark.asyncio
@respx.mock
async def test_ping_failure():
    respx.get("https://down.com").mock(side_effect=httpx.ConnectError("refused"))
    skill = PingSkill()
    result = await skill.run(target="down.com")
    assert result.status == SkillStatus.FAILURE
    assert len(result.errors) > 0


@pytest.mark.asyncio
@respx.mock
async def test_ping_5xx():
    respx.get("https://broken.com").mock(return_value=httpx.Response(500))
    skill = PingSkill()
    result = await skill.run(target="broken.com")
    assert result.status == SkillStatus.WARNING
    assert result.data["status_code"] == 500
