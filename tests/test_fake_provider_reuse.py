import asyncio

from app.agent import Agent
from app.providers.fake import FakeProvider
from app.tools import ToolRegistry


def test_fake_provider_allows_followup_after_script_exhaustion(tmp_path):
    async def scenario():
        agent = Agent(FakeProvider(["first answer"]), ToolRegistry(tmp_path))
        first = await agent.run("first task")
        second = await agent.run("follow up")
        assert first.status == "done" and first.text == "first answer"
        assert second.status == "done" and second.text == "[fake provider exhausted]"
    asyncio.run(scenario())
