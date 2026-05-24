import uuid
import sys
from pathlib import Path
from contextlib import asynccontextmanager

from mcp.client.session import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters, get_default_environment

import memory as memory_module
import perception as perception_module
import artifacts as artifacts_module
import decision
import action
from schema import Goal

MAX_ITERATIONS = 15

# Instantiate global components
memory = memory_module.Memory(Path(__file__).parent / "state" / "memory.json")
perception = perception_module.Perception()
artifacts = artifacts_module.ArtifactStore(Path(__file__).parent / "state" / "artifacts")


def ensure_gateway():
    import httpx
    try:
        httpx.get("http://0.0.0.0:8101/v1/capabilities", timeout=2.0)
    except Exception as e:
        print(f"Warning: LLM Gateway might not be running: {e}")


@asynccontextmanager
async def mcp_session():
    server_script = Path(__file__).parent / "mcp_server.py"
    server_params = StdioServerParameters(
        command=sys.executable,
        args=[str(server_script)],
        env=get_default_environment()
    )
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


async def load_tools(session: ClientSession):
    result = await session.list_tools()
    return result.tools


def mcp_tools_for_decision(mcp_tools):
    return [
        {
            "name": t.name,
            "description": t.description,
            "parameters": t.inputSchema,
        }
        for t in mcp_tools
    ]


def final_answer_from(history: list[dict]) -> str:
    for h in reversed(history):
        if h.get("kind") == "answer":
            return h["text"]
    return "Task finished without a specific answer."


async def run(query: str) -> str:
    ensure_gateway()
    run_id = uuid.uuid4().hex[:8]
    history: list[dict] = []
    prior_goals: list[Goal] = []

    # Durable memory: classify the user's query so facts/preferences
    # in it survive into future runs.
    memory.remember(query, source="user_query", run_id=run_id)

    async with mcp_session() as session:
        mcp_tools = await load_tools(session)
        tools = mcp_tools_for_decision(mcp_tools)

        for it in range(1, MAX_ITERATIONS + 1):
            hits = memory.read(query, history)
            obs = perception.observe(query, hits, history, prior_goals, run_id)
            prior_goals = obs.goals
            
            unfinished_goals = [g for g in obs.goals if not g.done]
            if not unfinished_goals:
                break

            goal = unfinished_goals[0]
            
            attached = []
            if goal.attach_artifact_id and artifacts.exists(goal.attach_artifact_id):
                attached.append((
                    goal.attach_artifact_id,
                    artifacts.get_bytes(goal.attach_artifact_id),
                ))

            out = decision.next_step(goal, hits, attached, history, tools)

            if out.answer is not None:
                history.append({"iter": it, "kind": "answer",
                                "goal_id": goal.id, "text": out.answer})
                continue

            result_text, art_id = await action.execute(session, out.tool_call)
            memory.record_outcome(
                tool_call=out.tool_call,
                result_text=result_text,
                artifact_id=art_id,
                run_id=run_id,
                goal_id=goal.id,
            )
            history.append({"iter": it, "kind": "action",
                            "goal_id": goal.id, "tool": out.tool_call.name,
                            "arguments": out.tool_call.arguments,
                            "result_descriptor": result_text[:300],
                            "artifact_id": art_id})

    return final_answer_from(history)

if __name__ == "__main__":
    import asyncio
    async def main():
        import sys
        q = sys.argv[1] if len(sys.argv) > 1 else "What's the weather like in Paris today?"
        print(f"Running agent with query: {q}")
        ans = await run(q)
        print("\n=== FINAL ANSWER ===")
        print(ans)

    asyncio.run(main())