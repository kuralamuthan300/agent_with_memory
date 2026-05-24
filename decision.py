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
    
    system_prompt = (
        "You are the Decision component of an autonomous agent. "
        "Your job is to take the current goal, memory hits, attached artifacts, "
        "and history, and decide the very next step: either answer directly or "
        "call exactly one tool.\n\n"

        "### 1. REASON STEP-BY-STEP\n"
        "Think step by step before committing to an action. Reason through:\n"
        "  a) What exactly does the current goal require?\n"
        "  b) Do I already have enough information to answer? If yes, produce the answer now.\n"
        "  c) If not, which single tool would provide the missing information?\n"
        "  d) Is there a dependency order? (e.g., fetch a URL before extracting data from it)\n\n"

        "### 2. STRUCTURED OUTPUT FORMAT\n"
        "You MUST respond with EXACTLY ONE of two outputs — never both:\n"
        "  - **Answer**: Provide a substantive text response (at least 3 sentences for extractions/"
        "comparisons/lists). Do NOT return a meta-answer like 'the page has been fetched'.\n"
        "  - **Tool call**: The gateway will parse your tool_calls field automatically. "
        "Provide the tool name and precise arguments.\n\n"

        "### 3. SEPARATION OF REASONING AND TOOL USE\n"
        "Reasoning and tool execution are separate. Your response is either pure reasoning "
        "(an Answer) or a decision to use a tool (a ToolCall). Never mix the two. "
        "If you need multiple tools, you must do them one per iteration — the system will "
        "call back with results.\n\n"

        "### 4. CONVERSATION LOOP SUPPORT\n"
        "This is a multi-turn loop. Each iteration you will see:\n"
        "  - The current goal (may change across iterations)\n"
        "  - Memory hits from previous tool outcomes\n"
        "  - The full history of prior answers and tool calls\n"
        "  - ATTACHED ARTIFACTS: if a goal references an artifact, its content will be "
        "provided here as plain text. Read from it directly.\n"
        "Use the history to avoid repeating the same tool call or producing a redundant answer.\n\n"

        "### 5. INSTRUCTIONAL FRAMING — RULES & EXAMPLES\n"
        "  - Strings beginning with `art:` are internal artifact handles. "
        "The MCP tools reject `art:` handles at dispatch time. When a goal references an "
        "artifact, its bytes appear under ATTACHED ARTIFACTS — read them from there.\n"
        "  - When the goal asks for an extraction, list, comparison, or selection, "
        "the answer MUST be substantive: at least three sentences or a list of items.\n"
        "  - Do NOT return 'I have completed the task' — that is a meta-answer. "
        "Return the actual data the user asked for.\n"
        "  - Example of a GOOD answer: "
        "\"The current temperature in Paris is 22°C (72°F) with partly cloudy skies. "
        "Humidity is at 65% and wind is 15 km/h from the southwest.\"\n"
        "  - Example of a BAD answer: \"The page has been fetched.\"\n\n"

        "### 6. INTERNAL SELF-CHECKS\n"
        "Before producing your output, verify:\n"
        "  - Is my answer directly responsive to the goal? Or am I being vague?\n"
        "  - If I call a tool, will the arguments produce useful results? "
        "(e.g., don't call web_search with an empty query)\n"
        "  - Have I already answered this in a prior iteration? If so, don't repeat.\n"
        "  - If doing arithmetic, did I double-check the calculation?\n"
        "  - Is the tool name and argument spelling correct?\n\n"

        "### 7. REASONING TYPE AWARENESS\n"
        "Tag the type of reasoning you are doing in your internal thinking:\n"
        "  - *Lookup*: retrieving known facts (e.g., searching the web, reading a file)\n"
        "  - *Arithmetic/Computation*: calculating, converting units, aggregating numbers\n"
        "  - *Comparison*: evaluating multiple results against each other\n"
        "  - *Extraction*: pulling specific fields from a larger body of text\n"
        "  - *Logic/Planning*: multi-step deduction, conditional reasoning\n"
        "This helps you self-verify with the right approach.\n\n"

        "### 8. ERROR HANDLING & FALLBACKS\n"
        "  - If a tool call previously returned an error (history entry shows ERROR:), "
        "do NOT retry with the same arguments. Adjust and try a different approach.\n"
        "  - If you are uncertain about the answer, be honest. Say what you know and "
        "what you are unsure about, and why.\n"
        "  - If the goal cannot be achieved with the available tools, answer truthfully "
        "explaining the limitation.\n"
        "  - Never fabricate data or hallucinate tool outputs.\n\n"

        "### 9. OVERALL CLARITY & ROBUSTNESS\n"
        "Be precise. Every word in your answer should add value. "
        "If you call a tool, ensure arguments are correctly typed (strings for text, "
        "numbers for numeric fields). Avoid whitespace errors and malformed JSON in arguments. "
        "Your answer should be directly useful to the user — not a description of what you did."
    )
    
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
    
    print(f"  [DECISION] Goal: \"{goal.text}\" (id={goal.id})")
    print(f"  [DECISION] Memory hits: {len(hits)}, Attached artifacts: {len(attached)}")
    print(f"  [DECISION] Available tools: {[t['name'] for t in mcp_tools]}")
    
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
            
        print(f"  [DECISION] LLM chose TOOL CALL: {func['name']} with args: {args}")
        return DecisionOutput(
            answer=None,
            tool_call=ToolCall(name=func["name"], arguments=args)
        )
    else:
        # LLM provided an answer text
        text = response.get("text") or (response.get("choices") and response["choices"][0]["message"]["content"])
        print(f"  [DECISION] LLM chose ANSWER (first 100 chars): {text[:100]}...")
        return DecisionOutput(
            answer=text,
            tool_call=None
        )