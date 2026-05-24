from pathlib import Path
from schema import MemoryItem, Goal, Observation
import json

class Memory:

    def __init__(self, memory_path: Path):
        # 1. Create the parent directories if they don't exist
        if not memory_path.parent.exists():
            memory_path.parent.mkdir(parents=True, exist_ok=True)
        
        # 2. Initialize the file if it doesn't exist
        if not memory_path.exists():
            memory_path.write_text('[]', encoding='utf-8')
            
        # 3. Read the text from the path, then parse it with json.loads
        raw_data = json.loads(memory_path.read_text(encoding='utf-8'))
        
        self.memory_data = [MemoryItem(**i) for i in raw_data]
        
    def read(self, query, history, kinds=None, top_k=8):
        Keyword overlap across keywords plus tokens of descriptor. Returns ranked top-k.

    def filter(self, kinds=None, goal_id=None, recent=None):
        Structured filter by kind, goal, recency.

    def relevant(self, query, kinds=None, top_k=5):
        LLM-scored relevance over a kind-filtered candidate pool. Used only when keyword recall is weak.

    def remember(self, raw_text, source, run_id, goal_id):
        Free-form ambiguous content (user input, observed statement).
        pass

    def record_outcome(self, tool_call, result_text, artifact_id):
        An MCP dispatch returned a result.
        pass