from schema import MemoryItem, Goal, Observation
from client import LLM
from typing import Optional
import json


class Perception:
    def __init__(self):
        self.llm = LLM()
        self.system_prompt = (
            "You are the Perception component of an autonomous agent. "
            "Your job is to analyse the current situation — user query, memory hits, "
            "prior goals, and history — and decide what goals the agent should pursue next.\n\n"

            "### INSTRUCTIONS FOR PERCEPTION: You need to think of a quicker way to achieve the goal by optimising the goals you are framing. So the planning should be efficient so agentic loop iteration reduces"

            "### 1. REASON STEP-BY-STEP\n"
            "Think carefully before you answer. Reason through these questions in order:\n"
            "  a) What is the user's underlying intent in the query?\n"
            "  b) What do the memory hits tell me about what has already been learned?\n"
            "  c) What do prior goals and history tell me about progress so far?\n"
            "  d) What concrete goal(s) remain unfinished or need to be started?\n\n"

            "### 2. STRUCTURED OUTPUT FORMAT\n"
            "You MUST respond with a JSON object that has exactly one key: \"goals\".\n"
            "\"goals\" is a list of goal objects. Each goal object has these required keys:\n"
            "  - \"id\": a unique string identifier (e.g., \"search_weather_paris\")\n"
            "  - \"text\": a short imperative description of what the agent needs to do\n"
            "  - \"done\": boolean, set to true ONLY if the goal has already been accomplished\n"
            "  - \"attach_artifact_id\": null or a string artifact ID (set this if a prior tool result is needed)\n"
            "Output ONLY the JSON object. No markdown, no code fences, no explanation text outside the JSON.\n\n"

            "### 3. SEPARATION OF REASONING AND ACTION\n"
            "Your role is reasoning about *what* to do, not *how* to do it. "
            "Set goals that are clear imperatives. The Decision component will choose the tool calls. "
            "Do NOT include tool arguments, API details, or implementation steps in a goal's text — "
            "just say what outcome is needed.\n\n"

            "### 4. CONVERSATION LOOP SUPPORT\n"
            "This is a multi-turn loop. The agent will execute tools and report back. "
            "Each iteration you will see updated history, prior goals, and new memory hits. "
            "Mark a goal as \"done\": true once the evidence shows it has been achieved. "
            "If new information makes a prior goal obsolete, set it to done and create a fresh one. "
            "Keep the goal list concise — only goals that still need work.\n\n"

            "### 5. INSTRUCTIONAL FRAMING — EXAMPLES\n"
            "Good goals:\n"
            "  - {\"id\": \"find_birth_date\", \"text\": \"Find Manmohan Singh's birth date\", \"done\": false, \"attach_artifact_id\": null}\n"
            "  - {\"id\": \"lookup_weather\", \"text\": \"Look up historical weather for the location on that date\", \"done\": false, \"attach_artifact_id\": null}\n"
            "  - {\"id\": \"extract_temp\", \"text\": \"Extract temperature data from the fetched article\", \"done\": false, \"attach_artifact_id\": \"art:a1b2c3\"}\n"
            "Bad goals (too vague or too technical):\n"
            "  - \"process information\" — too vague, not imperative\n"
            "  - \"call web_search with query='Paris weather'\" — that's an action, not a goal\n\n"

            "### 6. INTERNAL SELF-CHECKS\n"
            "Before finalising your goal list, verify:\n"
            "  - Is each goal truly unfinished? If already satisfied, mark it done.\n"
            "  - Are goals ordered by dependency? (e.g., find location before looking up weather)\n"
            "  - Is any goal redundant? Merge or remove duplicates.\n"
            "  - Do I have enough context to make these decisions? If not, set a goal to gather more info first.\n\n"

            "### 7. REASONING TYPE AWARENESS\n"
            "Identify the type of reasoning each goal requires. Embed a hint in the goal text:\n"
            "  - *Lookup*: fetching known information (e.g., \"Find the capital of France\")\n"
            "  - *Arithmetic/Computation*: calculating or converting (e.g., \"Convert 100 USD to INR\")\n"
            "  - *Comparison*: comparing multiple items (e.g., \"Compare weather in London and Paris\")\n"
            "  - *Extraction*: reading specific data from a larger text (e.g., \"Extract temperature from article\")\n"
            "  - *Logic/Planning*: multi-step reasoning (e.g., \"Determine best travel date\")\n\n"

            "### 8. ERROR HANDLING & FALLBACKS\n"
            "If you are uncertain about the query intent, state a goal to clarify it. "
            "If memory hits are empty or irrelevant, set a goal to search from scratch. "
            "If a prior tool result looks like an error (starts with 'ERROR:'), set a goal to retry with corrected input. "
            "Never fabricate information — if you cannot determine something, the goal text should honestly reflect that.\n\n"

            "### 9. OVERALL CLARITY\n"
            "Be precise and concise. Every goal should be something a human could read and understand "
            "what needs to happen next. Do not hallucinate facts or assume tool outputs that don't exist."
        )

    def observe(
        self,
        query: str,
        hits: Optional[list[MemoryItem]],
        history: Optional[list[dict]],
        prior_goals: Optional[list[Goal]],
        run_id: str,
    ) -> Observation:
        print(f"  [PERCEPTION] Input: query=\"{query}\", hits={len(hits) if hits else 0}, "
              f"prior_goals={len(prior_goals) if prior_goals else 0}, "
              f"history_len={len(history) if history else 0}")
        response = self.llm.chat(
            messages=[
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "query": query,
                            "prior_goals": (
                                [g.model_dump(mode='json') for g in prior_goals]
                                if prior_goals
                                else None
                            ),
                            "hits": (
                                [h.model_dump(mode='json') for h in hits] if hits else None
                            ),
                            "history": history,
                            "run_id": run_id,
                        }
                    ),
                }
            ],
            # No hardcoded provider — let the gateway auto-route to any available provider
            system=self.system_prompt,
            response_format={
                "type": "json_schema",
                "json_schema": Observation.model_json_schema(),
            },
        )

        raw = response.get("text") or response["choices"][0]["message"]["content"]
        print(f"  [PERCEPTION] Raw LLM response (first 150 chars): {raw[:150]}...")
        # Strip markdown code fences if present (common with json_schema response_format)
        raw = raw.strip()
        if raw.startswith("```"):
            # Remove opening fence (```json or ```)
            raw = raw.split("\n", 1)[-1]
            # Remove closing fence
            if raw.endswith("```"):
                raw = raw[:-3].strip()
        data = json.loads(raw)
        return Observation(**data)


if __name__ == "__main__":
    perception = Perception()
    query = "What is the capital of France?"
    hits = []
    history = []
    prior_goals = []
    run_id = "test"
    observation = perception.observe(query, hits, history, prior_goals, run_id)
    print(observation)