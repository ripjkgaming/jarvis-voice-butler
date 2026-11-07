"""Read-only state for a future Jarvis dashboard."""


def snapshot(store, registry):
    return {
        "tasks": store.tasks(),
        "events": store.events(),
        "connectors": {name: registry.probe(name) for name in registry.names},
    }
