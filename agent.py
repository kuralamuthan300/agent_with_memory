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
    print("\n=== AGENT START ===")
    print(f"[AGENT] Query: {query}")
    ensure_gateway()
    run_id = uuid.uuid4().hex[:8]
    print(f"[AGENT] Run ID: {run_id}")
    history: list[dict] = []
    prior_goals: list[Goal] = []

    # Durable memory: classify the user's query so facts/preferences
    # in it survive into future runs.
    print("[AGENT] Remembering user query in durable memory...")
    memory.remember(query, source="user_query", run_id=run_id)

    async with mcp_session() as session:
        print("[AGENT] MCP session established. Loading tools...")
        mcp_tools = await load_tools(session)
        tools = mcp_tools_for_decision(mcp_tools)
        print(f"[AGENT] Loaded {len(tools)} MCP tools: {[t['name'] for t in tools]}")

        for it in range(1, MAX_ITERATIONS + 1):
            print(f"\n{'='*50}")
            print(f"[AGENT] ITERATION {it}")
            print(f"{'='*50}")

            # ── Memory retrieval ──
            print("[AGENT] Reading from memory...")
            hits = memory.read(query, history)
            print(f"[AGENT] Memory hits: {len(hits)} item(s)")

            # ── Perception (goal-setting) ──
            print("[AGENT] Perception: analyzing situation and setting goals...")
            obs = perception.observe(query, hits, history, prior_goals, run_id)
            prior_goals = obs.goals
            print(f"[AGENT] Perception returned {len(obs.goals)} goal(s):")
            for g in obs.goals:
                status = "DONE" if g.done else "PENDING"
                attach = f" [attached: {g.attach_artifact_id}]" if g.attach_artifact_id else ""
                print(f"         - [{status}] {g.text}{attach}")

            unfinished_goals = [g for g in obs.goals if not g.done]
            print(f"[AGENT] Unfinished goals: {len(unfinished_goals)}")
            if not unfinished_goals:
                print("[AGENT] All goals complete. Breaking loop.")
                break

            goal = unfinished_goals[0]
            print(f"[AGENT] Working on goal: \"{goal.text}\" (id={goal.id})")

            attached = []
            if goal.attach_artifact_id and artifacts.exists(goal.attach_artifact_id):
                data = artifacts.get_bytes(goal.attach_artifact_id)
                attached.append((goal.attach_artifact_id, data))
                print(f"[AGENT] Attached artifact {goal.attach_artifact_id} ({len(data)} bytes) to goal")

            # ── Decision (LLM picks tool or answer) ──
            print("[AGENT] Decision: asking LLM for next step...")
            out = decision.next_step(goal, hits, attached, history, tools)

            if out.answer is not None:
                print(f"[AGENT] Decision LLM chose ANSWER: \"{out.answer[:100]}...\" (truncated)")
                history.append({"iter": it, "kind": "answer",
                                "goal_id": goal.id, "text": out.answer})
                continue

            if out.tool_call is not None:
                print(f"[AGENT] Decision LLM chose TOOL CALL: {out.tool_call.name}({out.tool_call.arguments})")

            # ── Action (execute tool via MCP) ──
            print(f"[AGENT] Action: executing tool \"{out.tool_call.name}\"...")
            result_text, art_id = await action.execute(session, out.tool_call)
            print(f"[AGENT] Tool result: {result_text[:200]}...")
            if art_id:
                print(f"[AGENT] Large result stored as artifact: {art_id}")

            # ── Record outcome ──
            print("[AGENT] Recording tool outcome in memory...")
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
            print(f"[AGENT] History now has {len(history)} entries")

    final = final_answer_from(history)
    print(f"\n[AGENT] Final answer extracted from history: {final[:150]}...")
    print("=== AGENT END ===\n")
    return final

if __name__ == "__main__":
    import asyncio
    async def main():
        import sys
        if len(sys.argv) > 1 :
            q = [sys.argv[1]]
        else:
            q = ["Fetch https://en.wikipedia.org/wiki/Claude_Shannon and tell me his birth date, death date, and three key contributions to information theory.",
            """Find 3 family-friendly things to do in Tokyo this weekend.
 Check Saturday's weather forecast there and tell me which one
 is most appropriate.""",
             """ My mom's birthday is 15 May 2026. Remember that and give me
        a calendar reminder for two weeks before and on the day. """,
             """When is mom's birthday?""",
             """Search for 'Python asyncio best practices', read the top 3 results,
 and give me a short numbered list of the advice they agree on."""
            ]

        for i, query in enumerate(q):
            print(f"\n{'-'*60}")
            print(f"QUERY {i+1}: {query}")
            print(f"{'-'*60}")
            answer = await run(query)
            print(f"\nANSWER: {answer}")
            print(f"{'='*60}")


    asyncio.run(main())