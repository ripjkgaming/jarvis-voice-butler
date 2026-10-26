"""Install the native bridge; install the extensions with Brave's Load unpacked UI."""
import json
import shutil
from datetime import datetime
from pathlib import Path

root = Path(__file__).resolve().parent
home = Path.home()
host = home / '.config/BraveSoftware/Brave-Browser/NativeMessagingHosts/org.jarvis.brave_motion.json'
launcher = home / '.local/share/applications/brave-browser.desktop'
backup = home / '.local/share/jarvis-desktop-backups' / ('brave-native-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
backup.mkdir(parents=True)
manifest = {'paths': {}}
for label, path in [('host', host), ('launcher', launcher)]:
    manifest['paths'][label] = {'path':str(path),'existed':path.exists()}
    if path.exists(): shutil.copy2(path, backup / label)
(backup / 'restore.json').write_text(json.dumps(manifest, indent=2)+'\n')
host.parent.mkdir(parents=True, exist_ok=True)
host.write_text(json.dumps({
    'name':'org.jarvis.brave_motion','description':'Local input-transparent Brave tab-bar animations',
    'path':str(root / 'native-host.sh'),'type':'stdio',
    'allowed_origins':['chrome-extension://hbjnbpieebphmfifkeaaeflkhhcekpei/'],
}, indent=2)+'\n')
source = launcher if launcher.exists() else Path('/usr/share/applications/brave-browser.desktop')
lines = source.read_text().splitlines()
owned_paths = {str(root / 'extension'), str(root / 'theme')}
for i, line in enumerate(lines):
    if line.startswith('Exec=') and '--load-extension=' in line:
        import re
        def keep_other_extensions(match):
            remaining = [path for path in match.group(1).split(',') if path not in owned_paths]
            return ' --load-extension=' + ','.join(remaining) if remaining else ''
        lines[i] = re.sub(r' --load-extension=([^\s]+)', keep_other_extensions, line)
launcher.parent.mkdir(parents=True, exist_ok=True)
launcher.write_text('\n'.join(lines)+'\n')
print('Native bridge installed. Load extension/ and theme/ through Brave’s Load unpacked UI to persist them in the profile.')
print('Backup:', backup)
