import json
from schema import Goal, MemoryItem, DecisionOutput, ToolCall
from client import LLM

def next_step(
    goal: Goal,
    hits: list[MemoryItem],
    attached: list[tuple[str, bytes]],
    history: list[dict],
    mcp_tools: list[dict],
) -> DecisionOutput:
    llm = LLM()
    
    system_prompt = """You are the Decision component of an agent.
Your task is to take the current goal, memory hits, and history, and decide the next step.

INSTRUCTIONS:
1. Respond with exactly one of two outputs: Answer or call a tool. Do not do both.
2. Strings beginning with `art:` are internal artifact handles. The MCP tools accept real file paths and URLs as their arguments and reject the `art:` prefix at dispatch time. When a goal requires the bytes of an artifact, those bytes appear in the prompt under ATTACHED ARTIFACTS:. Read them from there instead of passing `art:` handles to tools.
3. When the goal asks for an extraction, a list, a comparison, or a selection, the answer must be substantive: at least three sentences or a list of items. Do not return a meta-answer like "the page has been fetched".
"""
    
    attached_text = []
    for aid, b in attached:
        try:
            attached_text.append(f"Artifact {aid}:\n{b.decode('utf-8')}")
        except:
            attached_text.append(f"Artifact {aid}: <binary data, size {len(b)}>")

    user_content = {
        "current_goal": goal.model_dump(),
        "memory_hits": [h.model_dump(mode='json') for h in hits] if hits else [],
        "history": history,
    }
    
    user_message = json.dumps(user_content, indent=2)
    if attached_text:
        user_message += "\n\nATTACHED ARTIFACTS:\n" + "\n---\n".join(attached_text)
    
    response = llm.chat(
        messages=[{"role": "user", "content": user_message}],
        system=system_prompt,
        tools=mcp_tools,
        tool_choice="auto",
        auto_route="decision"
    )
    
    # Check if the LLM made a tool call
    if response.get("tool_calls"):
        tc = response["tool_calls"][0]
        # tc format: {"function": {"name": ..., "arguments": ...}} or {"name": ..., "arguments": ...} depending on gateway mapping
        # client.py standardizes response usually as {"function": ...} but let's handle both
        func = tc.get("function", tc)
        
        args = func.get("arguments", "{}")
        if isinstance(args, str):
            args = json.loads(args)
            
        return DecisionOutput(
            answer=None,
            tool_call=ToolCall(name=func["name"], arguments=args)
        )
    else:
        # LLM provided an answer text
        text = response.get("text") or (response.get("choices") and response["choices"][0]["message"]["content"])
        return DecisionOutput(
            answer=text,
            tool_call=None
        )