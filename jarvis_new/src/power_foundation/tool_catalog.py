"""Read-only MCP-style catalog; no server calls or arbitrary tool execution.

Future adapters must explicitly map validated descriptors to reviewed executors.
A tool advertised by any server is untrusted metadata, never authorization.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolDescriptor:
    connector: str
    name: str
    description: str
    effect: str = "read"

    def __post_init__(self):
        if (
            not self.connector
            or not self.name
            or self.effect not in ("read", "draft", "external", "system")
        ):
            raise ValueError("Invalid tool descriptor")
        if len(self.name) > 100 or len(self.description) > 1000:
            raise ValueError("Tool descriptor too large")


class ToolCatalog:
    def __init__(self):
        self._entries = {}

    def add(self, entry):
        if not isinstance(entry, ToolDescriptor):
            raise TypeError("A validated ToolDescriptor is required")
        key = f"{entry.connector}:{entry.name}"
        if key in self._entries:
            raise ValueError("Duplicate connector tool")
        self._entries[key] = entry

    def describe(self):
        return [
            {
                "connector": e.connector,
                "name": e.name,
                "description": e.description,
                "effect": e.effect,
                "approval_required": e.effect in ("external", "system"),
                "executable": False,
            }
            for e in self._entries.values()
        ]

    def execute(self, name, arguments):
        raise PermissionError(
            "The MCP foundation has no execution transport. "
            "Use a separately reviewed, confirmed tool."
        )


def starter_catalog():
    catalog = ToolCatalog()
    for connector, name, effect in [
        ("desktop", "screen_observation", "read"),
        ("browser", "page_observation", "read"),
        ("whatsie", "chat_listing", "read"),
        ("memory", "evidence_search", "read"),
        ("tasks", "task_status", "read"),
        ("voice", "session_status", "read"),
        ("mobile", "connection_status", "read"),
        ("downloads", "file_listing", "read"),
        ("workers", "proposal", "draft"),
        ("mcp", "server_discovery", "read"),
    ]:
        catalog.add(ToolDescriptor(connector, name, f"Foundation for {name}", effect))
    return catalog
