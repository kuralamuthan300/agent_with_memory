from pathlib import Path
from schema import MemoryItem, Goal, Observation
import json
from client import LLM
import re

def _tokenize(text: str) -> set[str]:
    # Extract alphanumeric words and lowercase them
    return set(re.findall(r'\w+', text.lower()))

class Memory:

    def __init__(self, memory_path: Path):
        self.memory_path = memory_path
        if not memory_path.exists():
            memory_path.parent.mkdir(parents=True, exist_ok=True)
            memory_path.write_text('[]', encoding='utf-8')
        with open(memory_path, 'r', encoding='utf-8') as f:
            self.memory_data = [MemoryItem(**i) for i in json.load(f)]
        
    def _save(self):
        self.memory_path.write_text(
            json.dumps([item.model_dump(mode='json') for item in self.memory_data], indent=2),
            encoding='utf-8'
        )

    def read(self, query, history, kinds=None, top_k=8):
        """
        Keyword overlap across keywords plus tokens of descriptor. Returns ranked top-k.
        """
        query_tokens = _tokenize(query)
        if not query_tokens:
            return []
        
        candidates = self.memory_data
        if kinds:
            if isinstance(kinds, str):
                kinds = [kinds]
            kinds_set = set(kinds)
            candidates = [c for c in candidates if c.kind in kinds_set]
            
        ranked = []
        for item in candidates:
            # item_tokens is keywords (lowercased) + descriptor tokens (lowercased)
            item_tokens = set(k.lower() for k in item.keywords) | _tokenize(item.descriptor)
            overlap = query_tokens.intersection(item_tokens)
            score = len(overlap)
            if score > 0:
                ranked.append((score, item))
                
        # Sort by score descending.
        ranked.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in ranked[:top_k]]

    def filter(self, kinds=None, goal_id=None, recent=None):
        """
        Structured filter by kind, goal, recency.
        """
        results = list(self.memory_data)
        if kinds:
            if isinstance(kinds, str):
                kinds = [kinds]
            kinds_set = set(kinds)
            results = [r for r in results if r.kind in kinds_set]
        if goal_id is not None:
            results = [r for r in results if r.goal_id == goal_id]
        
        # Sort by created_at descending (most recent first)
        results.sort(key=lambda x: x.created_at, reverse=True)
        if recent is not None:
            if isinstance(recent, int):
                results = results[:recent]
        return results

    def relevant(self, query, kinds=None, top_k=5):
        """
        LLM-scored relevance over a kind-filtered candidate pool. Used only when keyword recall is weak.
        """
        candidates = self.filter(kinds=kinds)
        if not candidates:
            return []
            
        # Format candidates for the LLM
        candidates_data = []
        for item in candidates:
            candidates_data.append({
                "id": item.id,
                "kind": item.kind,
                "descriptor": item.descriptor,
                "keywords": item.keywords,
                "value": item.value
            })
            
        prompt = json.dumps({
            "query": query,
            "candidates": candidates_data,
            "top_k": top_k
        })
        
        system_prompt = (
            "You are a memory retrieval assistant.\n"
            "Your task is to rank the candidate memory items based on their semantic relevance to the query.\n"
            "Respond with a JSON object containing a single key \"relevant_ids\", which is a list of the IDs of "
            "the most relevant memories, ordered from most relevant to least relevant.\n"
            "Return at most the top_k relevant memory IDs.\n"
            "Do not include any other text, markdown, or code fences."
        )
        
        try:
            llm = LLM()
            response = llm.chat(
                messages=[{"role": "user", "content": prompt}],
                system=system_prompt,
                provider="gr",
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "type": "object",
                        "properties": {
                            "relevant_ids": {
                                "type": "array",
                                "items": {"type": "string"}
                            }
                        },
                        "required": ["relevant_ids"]
                    }
                }
            )
            raw = response.get("text") or response["choices"][0]["message"]["content"]
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1]
                if raw.endswith("```"):
                    raw = raw[:-3].strip()
            data = json.loads(raw)
            relevant_ids = data.get("relevant_ids", [])
            
            # Map ids back to MemoryItem objects, maintaining order
            id_to_candidate = {c.id: c for c in candidates}
            result = []
            for rid in relevant_ids:
                if rid in id_to_candidate:
                    result.append(id_to_candidate[rid])
            return result[:top_k]
        except Exception as e:
            # Fallback to keyword read if LLM call fails
            print(f"Error in LLM-scored relevance: {e}. Falling back to keyword search.")
            return self.read(query, history=[], kinds=kinds, top_k=top_k)

    def remember(self, raw_text, source, run_id, goal_id=None):
        """
        Free-form ambiguous content (user input, observed statement).
        """
        import uuid
        from datetime import datetime
        
        # Call LLM to extract memories (facts or preferences)
        llm = LLM()
        
        # JSON Schema for response format
        schema = {
            "type": "object",
            "properties": {
                "memories": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "kind": {
                                "type": "string",
                                "enum": ["fact", "preference"]
                            },
                            "keywords": {
                                "type": "array",
                                "items": {"type": "string"}
                            },
                            "descriptor": {"type": "string"},
                            "value": {"type": "object"},
                            "confidence": {"type": "number"}
                        },
                        "required": ["kind", "keywords", "descriptor", "value", "confidence"]
                    }
                }
            },
            "required": ["memories"]
        }
        
        system_prompt = (
            "You are a memory extraction assistant. Analyze the given raw text (which could be a user query or observed statement) "
            "and extract any useful, long-term facts or user preferences that should be remembered for future runs.\n"
            "If no facts or preferences are found, return an empty list of memories.\n"
            "For each extracted memory, provide:\n"
            "1. kind: either \"fact\" or \"preference\"\n"
            "2. keywords: a list of relevant lowercase keywords for index lookup\n"
            "3. descriptor: a single short human-readable line summarizing the fact/preference\n"
            "4. value: a dictionary containing the structured details (e.g., {\"preference\": \"prefers Python over Javascript\"})\n"
            "5. confidence: a float value between 0.0 and 1.0 indicating your confidence in the extraction\n"
            "Respond ONLY with a JSON object matching the schema."
        )
        
        try:
            response = llm.chat(
                messages=[{"role": "user", "content": raw_text}],
                system=system_prompt,
                provider="gr",
                response_format={
                    "type": "json_schema",
                    "json_schema": schema
                }
            )
            raw = response.get("text") or response["choices"][0]["message"]["content"]
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1]
                if raw.endswith("```"):
                    raw = raw[:-3].strip()
            data = json.loads(raw)
            memories = data.get("memories", [])
            
            for mem in memories:
                item = MemoryItem(
                    id=f"mem_{uuid.uuid4().hex[:8]}",
                    kind=mem["kind"],
                    keywords=[k.lower() for k in mem["keywords"]],
                    descriptor=mem["descriptor"],
                    value=mem["value"],
                    artifact_id=None,
                    source=source,
                    run_id=run_id,
                    goal_id=goal_id,
                    confidence=mem["confidence"],
                    created_at=datetime.now()
                )
                self.memory_data.append(item)
                
            if memories:
                self._save()
        except Exception as e:
            print(f"Error extracting memories: {e}")

    def record_outcome(self, tool_call, result_text, artifact_id, run_id=None, goal_id=None):
        """
        An MCP dispatch returned a result.
        """
        import uuid
        from datetime import datetime
        
        # Heuristic/default values
        desc = f"Tool {tool_call.name} executed with arguments: {tool_call.arguments}"
        # Clean keywords from tool name and arguments
        keywords = [tool_call.name.lower()]
        for v in tool_call.arguments.values():
            if isinstance(v, str):
                keywords.extend([w.lower() for w in v.split() if w.isalnum()])
        # Make keywords unique and limit length
        keywords = list(set(keywords))[:10]
        
        # Try to use LLM to summarize the result for a better descriptor/keywords
        llm = LLM()
        prompt = json.dumps({
            "tool_name": tool_call.name,
            "arguments": tool_call.arguments,
            "result_summary": result_text[:2000] # Limit size to avoid huge prompt
        })
        
        system_prompt = (
            "You are a tool outcome summarizer.\n"
            "Given a tool name, its arguments, and the result text returned by the tool, "
            "generate:\n"
            "1. A descriptor: one short, human-readable line summarizing the outcome (e.g. 'Found weather in Paris is 15C and rainy')\n"
            "2. keywords: a list of relevant lowercase keywords for future lookup\n"
            "Respond ONLY with a JSON object containing keys \"descriptor\" and \"keywords\".\n"
            "Do not include any other text, markdown, or code fences."
        )
        
        try:
            response = llm.chat(
                messages=[{"role": "user", "content": prompt}],
                system=system_prompt,
                provider="gr",
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "type": "object",
                        "properties": {
                            "descriptor": {"type": "string"},
                            "keywords": {
                                "type": "array",
                                "items": {"type": "string"}
                            }
                        },
                        "required": ["descriptor", "keywords"]
                    }
                }
            )
            raw = response.get("text") or response["choices"][0]["message"]["content"]
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1]
                if raw.endswith("```"):
                    raw = raw[:-3].strip()
            data = json.loads(raw)
            if "descriptor" in data and "keywords" in data:
                desc = data["descriptor"]
                keywords = [k.lower() for k in data["keywords"]]
        except Exception as e:
            print(f"Error using LLM to summarize tool outcome: {e}. Using fallback descriptor.")
            
        item = MemoryItem(
            id=f"mem_{uuid.uuid4().hex[:8]}",
            kind="tool_outcome",
            keywords=keywords,
            descriptor=desc,
            value={
                "tool_name": tool_call.name,
                "arguments": tool_call.arguments,
                "result": result_text
            },
            artifact_id=artifact_id,
            source="tool_dispatch",
            run_id=run_id or "unknown",
            goal_id=goal_id,
            confidence=1.0,
            created_at=datetime.now()
        )
        self.memory_data.append(item)
        self._save()


if __name__ == "__main__":
    import tempfile
    import shutil
    from schema import ToolCall
    
    print("=== Testing Memory Component ===")
    
    # Use a temporary file for testing
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_mem_path = Path(tmpdir) / "test_memory.json"
        
        # 1. Initialization and File Creation
        print("\n1. Testing Initialization...")
        mem = Memory(temp_mem_path)
        assert temp_mem_path.exists(), "Memory JSON file should be created"
        assert len(mem.memory_data) == 0, "Initially memory should be empty"
        print("Initialization OK.")
        
        # 2. Testing remember (Memory Extraction)
        print("\n2. Testing remember()...")
        user_query = "Please remember that my favorite language is Python and I am working on the agent with memory project in Paris."
        mem.remember(user_query, source="user_query", run_id="run_123")
        
        print(f"Stored memories count: {len(mem.memory_data)}")
        for item in mem.memory_data:
            print(f"Extracted Memory - Kind: {item.kind}, Descriptor: '{item.descriptor}', Keywords: {item.keywords}")
            
        # 3. Testing read (Keyword Lookup)
        print("\n3. Testing read()...")
        hits = mem.read(query="What is my favorite programming language?", history=[])
        print(f"Keyword search hits count: {len(hits)}")
        for hit in hits:
            print(f"Hit - Descriptor: '{hit.descriptor}', Value: {hit.value}")
            
        # 4. Testing filter
        print("\n4. Testing filter()...")
        facts = mem.filter(kinds="fact")
        preferences = mem.filter(kinds="preference")
        print(f"Facts found: {len(facts)}")
        print(f"Preferences found: {len(preferences)}")
        
        # 5. Testing relevant (LLM-based semantic relevance)
        print("\n5. Testing relevant()...")
        rel_hits = mem.relevant(query="Paris coding project info", kinds=["fact", "preference"], top_k=2)
        print(f"LLM-scored relevance hits count: {len(rel_hits)}")
        for hit in rel_hits:
            print(f"Relevant Hit - Descriptor: '{hit.descriptor}', Value: {hit.value}")
            
        # 6. Testing record_outcome
        print("\n6. Testing record_outcome()...")
        tool = ToolCall(name="web_search", arguments={"query": "Paris weather today"})
        result = "The weather in Paris today is sunny and 18 degrees Celsius."
        mem.record_outcome(tool_call=tool, result_text=result, artifact_id=None, run_id="run_123", goal_id="goal_abc")
        
        outcomes = mem.filter(kinds="tool_outcome")
        print(f"Tool outcomes found: {len(outcomes)}")
        for item in outcomes:
            print(f"Outcome - Descriptor: '{item.descriptor}', Keywords: {item.keywords}")
            
        # 7. Reloading Memory
        print("\n7. Testing Reloading Memory from storage...")
        mem2 = Memory(temp_mem_path)
        print(f"Reloaded memories count: {len(mem2.memory_data)}")
        assert len(mem2.memory_data) == len(mem.memory_data), "Reloaded memory should have the same number of items"
        print("Reload OK.")
        
    print("\n=== All Memory Tests Completed ===")