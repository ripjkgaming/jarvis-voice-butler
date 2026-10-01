"""Second Brain graph: categories, excludes, incremental build, search."""

import json

import pytest

import second_brain
from second_brain import categorize, node_id


@pytest.fixture()
def brain_home(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / ".jarvis"))
    return tmp_path


def _tree(root):
    (root / "app").mkdir(parents=True)
    (root / "app" / "main.py").write_text("print(1)")
    (root / "app" / "notes.md").write_text("# hi")
    (root / "app" / "clip.mp4").write_text("x")
    (root / "app" / "invoice-2024.pdf").write_text("x")
    (root / "app" / "n8n-workflow.json").write_text("{}")
    (root / "app" / "data.csv").write_text("a,b")
    (root / "app" / "logo.blend").write_text("x")
    (root / "app" / ".git").mkdir()
    (root / "app" / ".git" / "HEAD").write_text("ref")
    (root / "app" / "__pycache__").mkdir()
    (root / "app" / "__pycache__" / "c.pyc").write_text("x")
    (root / "app" / ".hidden").mkdir()
    (root / "app" / ".hidden" / "s.txt").write_text("x")
    return root / "app"


def _config(root):
    return {
        "roots": [str(root)],
        "excludes": list(second_brain.DEFAULT_EXCLUDES),
        "exclude_hidden": True,
        "max_depth": 4,
        "max_nodes": 20000,
    }


def test_categorize_rules():
    assert categorize("main.py")[0] == "code"
    assert categorize("a.TS")[0] == "code"
    assert categorize("notes.md")[0] == "docs"
    assert categorize("clip.mp4")[0] == "media"
    assert categorize("photo.JPG")[0] == "media"
    assert categorize("logo.blend")[0] == "design"
    assert categorize("data.csv")[0] == "data"
    assert categorize("n8n-workflow.json")[0] == "automation"
    assert categorize("my-automation-script.sh")[0] == "automation"
    assert categorize("invoice-2024.pdf")[0] == "finance"
    assert categorize("social/instagram/post.mp4")[0] == "social"
    assert categorize("weird.xyz")[0] == "other"
    # Palette is a fixed hex per category.
    for cat in (
        "code",
        "docs",
        "media",
        "design",
        "data",
        "automation",
        "finance",
        "social",
        "jarvis-project",
        "other",
    ):
        assert second_brain.PALETTE[cat].startswith("#")


def test_node_id_stable():
    assert node_id("file", "/a/b") == node_id("file", "/a/b")
    assert node_id("file", "/a/b") != node_id("folder", "/a/b")


def test_build_respects_excludes(brain_home, tmp_path):
    app = _tree(tmp_path / "tree")
    stats = second_brain.build(incremental=False, config=_config(app))
    assert stats["nodes"] > 5
    paths = [n["path"] for n in second_brain.load_graph()["nodes"]]
    assert not any(".git" in p for p in paths)
    assert not any("__pycache__" in p for p in paths)
    assert not any(".hidden" in p for p in paths)
    by_label = {n["label"]: n for n in second_brain.load_graph()["nodes"]}
    assert by_label["main.py"]["category"] == "code"
    assert by_label["invoice-2024.pdf"]["category"] == "finance"
    assert by_label["n8n-workflow.json"]["category"] == "automation"


def test_incremental_picks_up_new_files(brain_home, tmp_path):
    app = _tree(tmp_path / "tree")
    cfg = _config(app)
    first = second_brain.build(incremental=False, config=cfg)
    second = second_brain.build(incremental=True, config=cfg)
    assert second["nodes"] == first["nodes"]  # nothing changed: same graph
    (app / "newfile.py").write_text("x = 1")
    third = second_brain.build(incremental=True, config=cfg)
    labels = [n["label"] for n in second_brain.load_graph()["nodes"]]
    assert "newfile.py" in labels
    assert third["nodes"] == first["nodes"] + 1


def test_incremental_keeps_stable_edges(brain_home, tmp_path):
    app = _tree(tmp_path / "tree")
    cfg = _config(app)
    second_brain.build(incremental=False, config=cfg)
    first = second_brain.load_graph()
    second_brain.build(incremental=True, config=cfg)
    again = second_brain.load_graph()
    key = lambda e: (e["from"], e["to"], e["kind"])  # noqa: E731
    assert sorted(map(key, again["edges"])) == sorted(map(key, first["edges"]))


def test_max_depth_and_nodes_caps(brain_home, tmp_path):
    deep = tmp_path / "tree"
    cur = deep
    for i in range(8):
        cur = cur / f"lvl{i}"
        cur.mkdir(parents=True)
    (cur / "deep.txt").write_text("x")
    cfg = _config(deep)
    cfg["max_depth"] = 2
    stats = second_brain.build(incremental=False, config=cfg)
    assert all("lvl5" not in n["path"] for n in second_brain.load_graph()["nodes"])
    assert stats["nodes"] <= 20
    cfg["max_nodes"] = 3
    stats = second_brain.build(incremental=False, config=cfg)
    assert stats["nodes"] <= 3


def test_search_ranking(brain_home, tmp_path):
    app = _tree(tmp_path / "tree")
    second_brain.build(incremental=False, config=_config(app))
    hits = second_brain.search("main.py")
    assert hits and hits[0]["label"] == "main.py"
    hits = second_brain.search("invoice")
    assert hits and hits[0]["label"] == "invoice-2024.pdf"
    assert second_brain.search("zzz-no-such-file") == []


def test_neighbors(brain_home, tmp_path):
    app = _tree(tmp_path / "tree")
    second_brain.build(incremental=False, config=_config(app))
    graph = second_brain.load_graph()
    folder = next(n for n in graph["nodes"] if n["path"] == str(app))
    near = second_brain.neighbors(folder["id"], depth=1, graph=graph)
    assert {n["label"] for n in near} >= {"main.py", "notes.md"}
    assert second_brain.neighbors("folder-deadbeef0000", graph=graph) == []


def test_operations_example_and_live(brain_home, tmp_path):
    app = _tree(tmp_path / "tree")
    assert second_brain.load_operations() == []  # first run: examples only
    ops_file = second_brain.brain_dir() / "operations.json"
    data = json.loads(ops_file.read_text())
    assert len(data["examples"]) == 2 and data["operations"] == []
    data["operations"] = [
        {
            "name": "clips",
            "description": "social clips",
            "category": "social",
            "paths": [str(app)],
            "tools": ["n8n"],
            "connections": [],
        }
    ]
    ops_file.write_text(json.dumps(data))
    second_brain.build(incremental=False, config=_config(app))
    graph = second_brain.load_graph()
    by_label = {n["label"]: n for n in graph["nodes"]}
    assert by_label["clips"]["type"] == "operation"
    assert by_label["n8n"]["type"] == "tool"
    kinds = {(e["from"], e["kind"]) for e in graph["edges"]}
    assert (by_label["clips"]["id"], "uses") in kinds


def test_default_config_created(brain_home):
    cfg = second_brain.load_config()
    assert (second_brain.brain_dir() / "config.json").exists()
    assert cfg["max_depth"] == 4 and cfg["max_nodes"] == 20000
