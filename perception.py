from schema import MemoryItem, Goal, Observation
from client import LLM
from typing import Optional
import json


class Perception:
    def __init__(self):
        self.llm = LLM()
        self.system_prompt = """You are a important part in an agentic architecture. 
You will receive user query, history of the agent, memory it has and the goals it has set. Based on all these you have to decide the next step of the agent.

You MUST respond with a JSON object that has exactly one key: "goals".
"goals" is a list of goal objects. Each goal object has these required keys:
  - "id": a unique string identifier
  - "text": a short imperative description of what the agent needs to do
  - "done": boolean, set to true if the goal has been accomplished already
  - "attach_artifact_id": null or a string artifact ID

Output ONLY the JSON object, no markdown, no code fences, no other text.
"""

    def observe(
        self,
        query: str,
        hits: Optional[list[MemoryItem]],
        history: Optional[list[dict]],
        prior_goals: Optional[list[Goal]],
        run_id: str,
    ) -> Observation:
        response = self.llm.chat(
            messages=[
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "query": query,
                            "prior_goals": (
                                [g.model_dump() for g in prior_goals]
                                if prior_goals
                                else None
                            ),
                            "hits": (
                                [h.model_dump() for h in hits] if hits else None
                            ),
                            "history": history,
                            "run_id": run_id,
                        }
                    ),
                }
            ],
            provider = 'gr',
            system=self.system_prompt,
            response_format={
                "type": "json_schema",
                "json_schema": Observation.model_json_schema(),
            },
        )

        raw = response.get("text") or response["choices"][0]["message"]["content"]
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