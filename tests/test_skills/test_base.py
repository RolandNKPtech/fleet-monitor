import pytest
from skills.base import BaseSkill, SkillResult, SkillStatus


class DummySkill(BaseSkill):
    name = "test.dummy"
    description = "A test skill"
    required_inputs = ["target"]
    optional_inputs = ["verbose"]

    async def run(self, **kwargs) -> SkillResult:
        target = kwargs["target"]
        return SkillResult(
            status=SkillStatus.SUCCESS,
            data={"target": target},
            message=f"Checked {target}",
        )


@pytest.mark.asyncio
async def test_skill_run_success():
    skill = DummySkill()
    result = await skill.run(target="example.com")
    assert result.status == SkillStatus.SUCCESS
    assert result.data["target"] == "example.com"
    assert result.message == "Checked example.com"
    assert result.errors == []
    assert result.suggestions == []


@pytest.mark.asyncio
async def test_skill_validate_inputs_passes():
    skill = DummySkill()
    await skill.validate_inputs(target="example.com")


@pytest.mark.asyncio
async def test_skill_validate_inputs_fails():
    skill = DummySkill()
    with pytest.raises(ValueError, match="Missing required inputs"):
        await skill.validate_inputs(verbose=True)


def test_skill_describe():
    skill = DummySkill()
    desc = skill.describe()
    assert desc["name"] == "test.dummy"
    assert desc["description"] == "A test skill"
    assert desc["required_inputs"] == ["target"]
    assert desc["optional_inputs"] == ["verbose"]


def test_skill_result_failure():
    result = SkillResult(
        status=SkillStatus.FAILURE,
        message="Connection failed",
        errors=["timeout after 30s"],
        suggestions=["Check if site is up"],
    )
    assert result.status == SkillStatus.FAILURE
    assert len(result.errors) == 1


def test_skill_status_values():
    assert SkillStatus.SUCCESS.value == "success"
    assert SkillStatus.FAILURE.value == "failure"
    assert SkillStatus.WARNING.value == "warning"
    assert SkillStatus.SKIPPED.value == "skipped"
