from backend.plugins.memory_palace import MemoryPalace


class MemoryBudget:
    """Enforces a simple memory size budget for Sarah."""

    def __init__(self, memory: MemoryPalace, max_items: int = 2000):
        self.memory = memory
        self.max_items = max_items

    def enforce_budget(self):
        keys = list(self.memory.graph.keys())
        if len(keys) <= self.max_items:
            return

        # Trim oldest keys first
        to_remove = keys[0 : len(keys) - self.max_items]
        for k in to_remove:
            self.memory.graph.pop(k, None)
        self.memory._save()
