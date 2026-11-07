"""Explicit plans and parallel proposal-only workers. Never executes actions."""

from concurrent.futures import ThreadPoolExecutor


class StepPlan:
    def __init__(self, goal, steps):
        self.goal = goal
        self.steps = steps

    @classmethod
    def build(cls, goal, steps):
        if not goal.strip() or not steps:
            raise ValueError("A nonempty plan is required")
        out = []
        for raw in steps:
            effect = raw.get("effect")
            if effect not in ("read", "draft", "external"):
                raise ValueError("Unsupported effect")
            if not raw.get("name", "").strip():
                raise ValueError("Step must have a name")
            out.append(
                {
                    "name": raw["name"],
                    "effect": effect,
                    "requires_approval": effect == "external",
                    "status": "queued",
                }
            )
        return cls(goal, out)

    def next_step(self):
        return next((s for s in self.steps if s["status"] == "queued"), None)


class ProposalPool:
    def __init__(self, max_workers=2):
        self.max_workers = max(1, min(max_workers, 4))

    def gather(self, jobs):
        # Inputs are trusted callable integrations supplied by the caller.
        # Outputs are proposals, never interpreted as instructions or executed.
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = {name: pool.submit(job) for name, job in jobs.items()}
            result = {}
            for name, fut in futures.items():
                try:
                    result[name] = fut.result(timeout=10)
                except Exception:
                    result[name] = {"status": "error"}
        return result
