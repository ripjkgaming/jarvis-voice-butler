#!/usr/bin/env python3
"""Install/revert the cinematic desktop with scoped configuration and package snapshots."""
import configparser
import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from zoneinfo import ZoneInfo
import kwin_bridge_loader

ROOT=Path(__file__).resolve().parent
DATA=Path.home()/'.local/share'
CONFIG=Path.home()/'.config'
BACKUPS=DATA/'jarvis-desktop-backups'
PACKAGES={
    'icons/jarvis-electric-cursors': DATA/'icons/jarvis-electric-cursors',
    'kwin/effects/jarvis-energy': DATA/'kwin/effects/jarvis-energy',
    'kwin/effects/jarvis-surfaces': DATA/'kwin/effects/jarvis-surfaces',
    'kwin/tabbox/jarvis-glass-compact-v2': DATA/'kwin/tabbox/jarvis-glass-compact-v2',
    'plasma/desktoptheme/jarvis-blue-glass': DATA/'plasma/desktoptheme/jarvis-blue-glass',
    'look-and-feel/org.jarvis.cinematic': DATA/'plasma/look-and-feel/org.jarvis.cinematic',
}
ENABLED=['jarvis-energy','jarvis-surfaces','overview','slide','slidingpopups','highlightwindow']
# 'fullscreen': jarvis-energy owns the fullscreen morph (stock parity for other apps),
# so the stock effect must not stack a second size/position track on top.
DISABLED=['fullscreen','maximize','locke-tech-fade','fade','glide','scale','fallapart','magiclamp','squash','wobblywindows','fadingpopups','slidingnotifications']
AUTOS=[CONFIG/'autostart'/n for n in ['jarvis-taskbar-slice.desktop','jarvis-taskbar-puppet.desktop','jarvis-cinematic.desktop']]
BRIDGES=['jarvis-taskbar-slice-v2','jarvis-taskbar-puppet']
SERVICES=[('org.jarvis.TaskbarSlice','/Slice'),('org.jarvis.TaskbarPuppet','/Puppet')]
SETTINGS={
    'kwinrc': {
        'Plugins':{**{n+'Enabled':'true' for n in ENABLED},**{n+'Enabled':'false' for n in DISABLED}},
        'Effect-jarvis-energy':{'CloseEnabled':'true','WindowMotionOnly':'true','ActionRequest':'{}'},
        'Effect-jarvis-surfaces':{'ReducedMotion':'false'},
        'TabBox':{'LayoutName':'jarvis-glass-compact-v2','DelayTime':'0'},
        'Effect-slidingpopups':{'SlideInTime':'220','SlideOutTime':'160'},
        'Effect-overview':{'OrganizedGrid':'true'},
        'Effect-blur':{'BlurStrength':'4','NoiseStrength':'0','Saturation':'120'},
        'Effect-slide':{'HorizontalGap':'28','VerticalGap':'20','SlideBackground':'true'},
    },
    'kcminputrc':{'Mouse':{'cursorTheme':'jarvis-electric-cursors'}},
    'plasmarc':{'Theme':{'name':'jarvis-blue-glass'}},
    'ksplashrc':{'KSplash':{'Engine':'KSplashQML','Theme':'org.jarvis.cinematic'}},
}

def run(*args):return subprocess.check_output([str(a) for a in args],text=True).strip()
def dbus(method,*args):return run('qdbus','org.kde.KWin','/Effects','org.kde.kwin.Effects.'+method,*args)
def services():return {s.strip() for s in run('qdbus').splitlines()}
def conf(filename):
    c=configparser.RawConfigParser(strict=False);c.optionxform=str;c.read(CONFIG/filename);return c

def put(filename,group,key,value):
    args=['kwriteconfig6','--file',CONFIG/filename,'--group',group,'--key',key]
    run(*(args+(['--delete'] if value is None else [value])))

def stop():
    for bridge in BRIDGES:kwin_bridge_loader.unload(bridge)
    for service,path in SERVICES:
        if service in services():run('qdbus',service,path,service+'.Quit')
    for _ in range(30):
        if not any(service in services() for service,_ in SERVICES):break
        time.sleep(.1)
    else:raise RuntimeError('Taskbar overlays did not stop')

def start(kinds=('close','open')):
    # Plasma can start before locally installed scripted effects are discovered.
    # Restore only our explicitly enabled effects, never override a disabled one.
    config=conf('kwinrc')
    for effect_name in ('jarvis-energy','jarvis-surfaces'):
        if config.getboolean('Plugins',effect_name+'Enabled',fallback=False):
            if dbus('isEffectLoaded',effect_name)!='true':
                if dbus('loadEffect',effect_name)!='true':raise RuntimeError('Could not load '+effect_name)
    for kind,(service,path),bridge in zip(['close','open'],SERVICES,BRIDGES):
        if kind not in kinds:continue
        if service not in services():
            log=Path.home()/'.local/state/jarvis-cinematic-taskbar.log';log.parent.mkdir(parents=True,exist_ok=True)
            with log.open('a') as stream:
                subprocess.Popen(['/usr/bin/python',str(ROOT/'taskbar.py'),kind],env={**os.environ,'QT_QPA_PLATFORM':'xcb'},stdout=stream,stderr=stream,start_new_session=True)
            for _ in range(30):
                if service in services():break
                time.sleep(.1)
            else:raise RuntimeError('Taskbar service did not start: '+service)
        run('qdbus',service,path,service+'.Status')
        if run('qdbus','org.kde.KWin','/Scripting','org.kde.kwin.Scripting.isScriptLoaded',bridge)!='true':
            kwin_bridge_loader.load(ROOT/('taskbar-slice.js' if kind=='close' else 'taskbar-puppet.js'),bridge)


def snapshot():
    loaded=dbus('loadedEffects').splitlines();active=services()
    stamp=datetime.datetime.now(ZoneInfo('Asia/Singapore')).strftime('%Y%m%d-%H%M%S-%f')
    folder=BACKUPS/('cinematic-v2-'+stamp);folder.mkdir(parents=True)
    settings={}
    for filename,groups in SETTINGS.items():
        c=conf(filename)
        settings[filename]={group:{key:c.get(group,key,fallback=None) for key in entries} for group,entries in groups.items()}
        if (CONFIG/filename).exists():shutil.copy2(CONFIG/filename,folder/filename)
    paths={}
    for i,path in enumerate([*PACKAGES.values(),*AUTOS]):
        saved='path-'+str(i);paths[str(path)]=saved if path.exists() else None
        if path.is_dir():shutil.copytree(path,folder/saved)
        elif path.exists():shutil.copy2(path,folder/saved)
    state={'loaded':loaded,'settings':settings,'paths':paths,
           'taskbar':[kind for kind,(service,_) in zip(['close','open'],SERVICES) if service in active],
           'previousCinematic':AUTOS[2].exists()}
    (folder/'state.json').write_text(json.dumps(state,indent=2)+'\n')
    return folder


def restore(folder):
    state=json.loads((folder/'state.json').read_text())
    stop()
    for name in ENABLED+DISABLED:
        if name in ['jarvis-energy','jarvis-surfaces']:dbus('unloadEffect',name)
    for path,saved in state['paths'].items():
        p=Path(path)
        if p.is_dir():shutil.rmtree(p)
        else:p.unlink(missing_ok=True)
        if saved:
            s=folder/saved;p.parent.mkdir(parents=True,exist_ok=True)
            if s.is_dir():shutil.copytree(s,p)
            else:shutil.copy2(s,p)
    for filename,groups in state['settings'].items():
        for group,entries in groups.items():
            for key,value in entries.items():
                if filename not in ('plasmarc','kcminputrc'):put(filename,group,key,value)
    if 'plasmarc' in state['settings']:
        theme=state['settings']['plasmarc']['Theme']['name']
        run('plasma-apply-desktoptheme',theme or 'default')
        if theme is None:put('plasmarc','Theme','name',None)
    if 'kcminputrc' in state['settings']:
        cursor=state['settings']['kcminputrc']['Mouse']['cursorTheme']
        run('plasma-apply-cursortheme',cursor or 'breeze_cursors')
        if cursor is None:put('kcminputrc','Mouse','cursorTheme',None)
    run('qdbus','org.kde.KWin','/KWin','reconfigure')
    for name in ENABLED+DISABLED:
        if name in state['loaded']:
            if dbus('isEffectLoaded',name)!='true' and dbus('loadEffect',name)!='true':raise RuntimeError('Could not restore '+name)
        else:dbus('unloadEffect',name)
    if state['previousCinematic']:start(state['taskbar'])
    else:
        for kind in state['taskbar']:
            run('/usr/bin/python',ROOT.parent/('taskbar-effects.py' if kind=='close' else 'taskbar-puppet-effects.py'),'start')
        # Old helpers regenerate autostart files; restore the exact previous bytes.
        for auto in AUTOS:
            saved=state['paths'][str(auto)]
            if saved:shutil.copy2(folder/saved,auto)
            else:auto.unlink(missing_ok=True)
    print('Restored previous desktop animations: '+str(folder))


def install():
    folder=snapshot()
    try:
        stop()
        for name in ['jarvis-energy','jarvis-surfaces']:dbus('unloadEffect',name)
        for source,dest in PACKAGES.items():
            if dest.exists():shutil.rmtree(dest)
            dest.parent.mkdir(parents=True,exist_ok=True);shutil.copytree(ROOT/source,dest)
        SETTINGS['kwinrc']['Effect-jarvis-surfaces']['ReducedMotion']=conf('kwinrc').get('Effect-jarvis-energy','ReducedMotion',fallback='false')
        for filename,groups in SETTINGS.items():
            for group,entries in groups.items():
                for key,value in entries.items():
                    if filename not in ('plasmarc','kcminputrc'):put(filename,group,key,value)
        run('plasma-apply-desktoptheme','jarvis-blue-glass')
        run('plasma-apply-cursortheme','jarvis-electric-cursors')
        run('qdbus','org.kde.KWin','/KWin','reconfigure')
        for name in DISABLED:dbus('unloadEffect',name)
        for name in ENABLED:
            if dbus('isEffectLoaded',name)!='true' and dbus('loadEffect',name)!='true':raise RuntimeError('Effect failed to load: '+name)
        start()
        for auto in AUTOS:auto.unlink(missing_ok=True)
        AUTOS[2].parent.mkdir(parents=True,exist_ok=True)
        AUTOS[2].write_text('[Desktop Entry]\nType=Application\nName=Jarvis cinematic taskbar\nExec=/usr/bin/python "'+str(ROOT/'manage.py')+'" start\nX-KDE-autostart-after=panel\n')
    except Exception:
        restore(folder);raise
    (BACKUPS/'cinematic-v2-latest').write_text(str(folder)+'\n')
    print('Cinematic desktop installed. Backup: '+str(folder))
    print('Rollback: /usr/bin/python '+str(ROOT/'manage.py')+' restore '+str(folder))


def status():
    active=services()
    print(json.dumps({'loadedEffects':dbus('loadedEffects').splitlines(),
        'taskbar':{service:service in active for service,_ in SERVICES},
        'switcher':conf('kwinrc').get('TabBox','LayoutName',fallback=''),
        'splash':conf('ksplashrc').get('KSplash','Theme',fallback='')},indent=2))

if __name__=='__main__':
    action=sys.argv[1] if len(sys.argv)>1 else 'status'
    if action=='install':install()
    elif action=='restore':restore(Path(sys.argv[2]) if len(sys.argv)>2 else Path((BACKUPS/'cinematic-v2-latest').read_text().strip()))
    elif action=='start':start()
    elif action=='stop':stop()
    elif action=='status':status()
    else:raise SystemExit('Usage: manage.py install|restore [snapshot]|status|start|stop')
