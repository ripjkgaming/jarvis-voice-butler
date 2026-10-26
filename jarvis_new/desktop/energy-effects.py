#!/usr/bin/env python3
"""Install or restore only the affected KWin transition effects; no session restart."""
import configparser
import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share'))
CONFIG = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config'))
DEST = DATA / 'kwin/effects/jarvis-energy'
SWITCHER = DATA / 'kwin/tabbox/jarvis-compact-v3'
BACKUPS = DATA / 'jarvis-desktop-backups'
AFFECTED = ['jarvis-energy', 'locke-tech-fade', 'fade', 'glide', 'scale', 'fallapart', 'magiclamp', 'squash']

def run(*args):
    return subprocess.check_output(args, text=True).strip()

def dbus(method, *args):
    return run('qdbus', 'org.kde.KWin', '/Effects', 'org.kde.kwin.Effects.' + method, *args)

def config():
    c = configparser.RawConfigParser(strict=False); c.optionxform = str
    c.read(CONFIG / 'kwinrc'); return c

def put(group, key, value):
    args = ['kwriteconfig6', '--file', str(CONFIG / 'kwinrc'), '--group', group, '--key', key]
    run(*(args + (['--delete'] if value is None else [value])))

def restore(backup):
    state = json.loads((backup / 'state.json').read_text())
    # Snapshots predating the separate taskbar layer restore its absence too.
    run(sys.executable, str(ROOT/'taskbar-effects.py'), 'start' if state.get('taskbarActive',False) else 'stop')
    if SWITCHER.exists(): shutil.rmtree(SWITCHER)
    if (backup/'jarvis-compact').exists(): shutil.copytree(backup/'jarvis-compact', SWITCHER)
    if 'TabBox' not in state['settings']:
        original=configparser.RawConfigParser(strict=False);original.optionxform=str
        original.read(backup/'kwinrc')
        put('TabBox','LayoutName',original.get('TabBox','LayoutName',fallback=None))
    if 'DelayTime' not in state['settings'].get('TabBox',{}):
        original=configparser.RawConfigParser(strict=False);original.optionxform=str
        original.read(backup/'kwinrc');put('TabBox','DelayTime',original.get('TabBox','DelayTime',fallback=None))
    for name in AFFECTED:
        dbus('unloadEffect', name)
    if DEST.exists(): shutil.rmtree(DEST)
    if (backup / 'jarvis-energy').exists(): shutil.copytree(backup / 'jarvis-energy', DEST)
    for group, entries in state['settings'].items():
        for key, value in entries.items(): put(group, key, value)
    run('qdbus','org.kde.KWin','/KWin','reconfigure')
    for name in state['loaded']:
        if name in AFFECTED and dbus('loadEffect', name) != 'true':
            raise RuntimeError('Could not restore loaded effect: ' + name)
    actual = dbus('loadedEffects').splitlines()
    if set(actual).intersection(AFFECTED) != set(state['loaded']).intersection(AFFECTED):
        raise RuntimeError('Restored active effects do not match snapshot')
    print('Previous exact transition settings and loaded effects restored from ' + str(backup))

def install():
    # Read availability before any mutation; full config and original packages are retained.
    loaded = dbus('loadedEffects').splitlines()
    c = config()
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    backup = BACKUPS / ('energy-' + stamp); backup.mkdir(parents=True)
    shutil.copy2(CONFIG / 'kwinrc', backup / 'kwinrc')
    for name in ['jarvis-energy', 'locke-tech-fade']:
        source = DATA / 'kwin/effects' / name
        if source.exists(): shutil.copytree(source, backup / name)
    if SWITCHER.exists(): shutil.copytree(SWITCHER,backup/'jarvis-compact')
    settings = {'TabBox': {key:c.get('TabBox',key,fallback=None) for key in ['LayoutName','DelayTime']}, 'Plugins': {name+'Enabled': c.get('Plugins',name+'Enabled',fallback=None) for name in AFFECTED},
                'Effect-jarvis-energy': {'ReducedMotion': c.get('Effect-jarvis-energy','ReducedMotion',fallback=None)}}
    (backup/'state.json').write_text(json.dumps({'loaded':loaded,'settings':settings,'taskbarActive': 'org.jarvis.TaskbarSlice' in [line.strip() for line in run('qdbus').splitlines()]},indent=2)+'\n')
    try:
        dbus('unloadEffect','jarvis-energy')
        if DEST.exists(): shutil.rmtree(DEST)
        shutil.copytree(ROOT/'kwin/effects/jarvis-energy',DEST)
        if SWITCHER.exists(): shutil.rmtree(SWITCHER)
        shutil.copytree(ROOT/'kwin/tabbox/jarvis-compact-v3', SWITCHER)
        put('TabBox','LayoutName','jarvis-compact-v3')
        put('TabBox','DelayTime','0')
        # Carry forward the user's existing reduced-motion preference.
        reduced = c.get('Effect-jarvis-energy','ReducedMotion',fallback=c.get('Effect-locke-tech-fade','ReducedMotion',fallback='false'))
        put('Effect-jarvis-energy','ReducedMotion',reduced)
        run('qdbus','org.kde.KWin','/KWin','reconfigure')
        if dbus('loadEffect','jarvis-energy') != 'true': raise RuntimeError('New effect failed to load')
        for name in AFFECTED:
            put('Plugins',name+'Enabled','true' if name=='jarvis-energy' else 'false')
            if name != 'jarvis-energy': dbus('unloadEffect',name)
        if dbus('isEffectLoaded','jarvis-energy') != 'true': raise RuntimeError('New effect is not loaded')
        run(sys.executable, str(ROOT/'taskbar-effects.py'), 'start')
    except Exception:
        restore(backup)
        raise
    (BACKUPS/'energy-latest').write_text(str(backup)+'\n')
    print('Live: Jarvis Energy. Original packages preserved. Backup: '+str(backup))
    print('Rollback: '+str(ROOT/'energy-effects.py')+' restore '+str(backup))

def window_revision(backup=None):
    """Replace only this effect's package; leave all other desktop state intact."""
    loaded = dbus('isEffectLoaded', 'jarvis-energy') == 'true'
    if backup is None:
        backup = BACKUPS / ('window-orbit-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
        backup.mkdir(parents=True)
        if DEST.exists(): shutil.copytree(DEST, backup/'jarvis-energy')
        (backup/'window-state.json').write_text(json.dumps({'loaded': loaded})+'\n')
        source = ROOT/'kwin/effects/jarvis-energy'
    else:
        source = backup/'jarvis-energy'
        state_path=backup/'window-state.json'
        if state_path.exists():
            loaded=json.loads(state_path.read_text())['loaded']
        else:
            loaded='jarvis-energy' in json.loads((backup/'state.json').read_text())['loaded']
    if not source.is_dir(): raise RuntimeError('Missing effect package: '+str(source))
    dbus('unloadEffect','jarvis-energy')
    try:
        if DEST.exists(): shutil.rmtree(DEST)
        shutil.copytree(source,DEST)
        if loaded and dbus('loadEffect','jarvis-energy') != 'true':
            raise RuntimeError('Effect did not load')
    except Exception:
        if source != backup/'jarvis-energy':
            window_revision(backup)
        raise
    print('Window effect package updated; other effects/settings untouched.')
    print('Exact package rollback: '+str(ROOT/'energy-effects.py')+' restore-window '+str(backup))
    return backup

if __name__ == '__main__':
    action = sys.argv[1] if len(sys.argv)>1 else 'status'
    if action == 'update-window': window_revision()
    elif action == 'restore-window': window_revision(Path(sys.argv[2]))
    elif action == 'install': install()
    elif action == 'restore': restore(Path(sys.argv[2]) if len(sys.argv)>2 else Path((BACKUPS/'energy-latest').read_text().strip()))
    elif action == 'status': print(dbus('loadedEffects'))
    else: raise SystemExit('Usage: energy-effects.py install|update-window|status|restore|restore-window [backup]')
