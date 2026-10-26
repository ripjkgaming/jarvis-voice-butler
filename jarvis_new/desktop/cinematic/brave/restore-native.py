"""Restore the two native integration files from an install-native.py snapshot."""
import argparse
import json
import shutil
import subprocess
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('snapshot', type=Path)
args = parser.parse_args()
manifest = json.loads((args.snapshot / 'restore.json').read_text())
expected = {
    'host': Path.home() / '.config/BraveSoftware/Brave-Browser/NativeMessagingHosts/org.jarvis.brave_motion.json',
    'launcher': Path.home() / '.local/share/applications/brave-browser.desktop',
}
for label, target in expected.items():
    record = manifest['paths'][label]
    if Path(record['path']) != target:
        raise SystemExit('Snapshot belongs to another installation: ' + label)
    if record['existed'] and not (args.snapshot / label).is_file():
        raise SystemExit('Missing backup file: ' + label)
for label, target in expected.items():
    if manifest['paths'][label]['existed']:
        shutil.copy2(args.snapshot / label, target)
    else:
        target.unlink(missing_ok=True)
subprocess.run(['qdbus','org.jarvis.BraveMotion','/Motion','org.jarvis.BraveMotion.Quit'], capture_output=True)
print('Restored the Brave launcher and native host registration. Native accents stopped.')
