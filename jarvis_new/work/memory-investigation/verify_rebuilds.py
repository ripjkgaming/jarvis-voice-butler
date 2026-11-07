import json
import os
import resource
import shutil
import tempfile
from pathlib import Path
import second_brain

source = Path.home() / '.jarvis/brain'
with tempfile.TemporaryDirectory(prefix='jarvis-memory-check-') as tmp:
    os.environ['JARVIS_HOME'] = tmp
    target = Path(tmp) / 'brain'
    target.mkdir()
    for name in ('config.json', 'operations.json', 'graph.json', '.mtimes.json'):
        if (source / name).exists():
            shutil.copy2(source / name, target / name)
    for i in range(6):
        stats = second_brain.build(incremental=True)
        graph = second_brain.load_graph()
        keys = {(e['from'], e['to'], e['kind']) for e in graph['edges']}
        assert len(keys) == stats['edges'], 'duplicate edges'
        assert stats['nodes'] <= 20000
        print(json.dumps({'iteration': i + 1, 'nodes': stats['nodes'], 'edges': stats['edges'], 'index_bytes': (target / 'graph.json').stat().st_size, 'peak_rss_mib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024}), flush=True)
