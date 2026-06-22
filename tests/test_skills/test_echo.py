import pytest
from skills.example.echo import EchoSkill
from skills.base import SkillStatus


@pytest.mark.asyncio
async def test_echo_returns_inputs():
    skill = EchoSkill()
    result = await skill.run(message="hello", extra="world")
    assert result.status == SkillStatus.SUCCESS
    assert result.data["message"] == "hello"
    assert result.data["extra"] == "world"


@pytest.mark.asyncio
async def test_echo_empty():
    skill = EchoSkill()
    result = await skill.run()
    assert result.status == SkillStatus.SUCCESS
    assert result.data == {}
