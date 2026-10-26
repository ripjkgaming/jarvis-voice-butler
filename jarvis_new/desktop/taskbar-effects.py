#!/usr/bin/env python3
"""Start/stop only the Jarvis taskbar overlay and its KWin event bridge."""
from pathlib import Path
import os,subprocess,sys,time
import kwin_bridge_loader
ROOT=Path(__file__).resolve().parent
BRIDGE='jarvis-taskbar-slice-v2'
AUTO=Path.home()/'.config/autostart/jarvis-taskbar-slice.desktop'
def run(*args,check=True):return subprocess.run(args,text=True,capture_output=True,check=check).stdout.strip()
def loaded():return run('qdbus','org.kde.KWin','/Scripting','org.kde.kwin.Scripting.isScriptLoaded',BRIDGE)=='true'
def active():return 'org.jarvis.TaskbarSlice' in [line.strip() for line in run('qdbus').splitlines()]
def start():
    if not active():
        log=Path.home()/'.local/state/jarvis-taskbar-slice.log';log.parent.mkdir(parents=True,exist_ok=True)
        with log.open('a') as stream:
            subprocess.Popen(['/usr/bin/python',str(ROOT/'taskbar-slice.py')],env={**os.environ,'QT_QPA_PLATFORM':'xcb'},stdout=stream,stderr=stream,start_new_session=True)
        for _ in range(30):
            if active():break
            time.sleep(.1)
        else:raise RuntimeError('Taskbar overlay did not register')
    if not loaded():
        kwin_bridge_loader.load(ROOT/'taskbar-slice.js','jarvis-taskbar-slice-v2')
    AUTO.parent.mkdir(parents=True,exist_ok=True)
    AUTO.write_text('[Desktop Entry]\nType=Application\nName=Jarvis taskbar icon animation\nExec=/usr/bin/python '+str(ROOT/'taskbar-effects.py')+' start\nX-KDE-autostart-after=panel\n')
    print('Taskbar icon overlay and event bridge active; login startup installed.')
def stop():
    kwin_bridge_loader.unload('jarvis-taskbar-slice-v2')
    if active():run('qdbus','org.jarvis.TaskbarSlice','/Slice','org.jarvis.TaskbarSlice.Quit')
    for _ in range(30):
        if not active():break
        time.sleep(.1)
    else:raise RuntimeError('Overlay did not stop')
    AUTO.unlink(missing_ok=True)
    print('Taskbar animation stopped; login startup removed.')
if __name__=='__main__':
    action=sys.argv[1] if len(sys.argv)>1 else 'status'
    if action=='start':start()
    elif action=='stop':stop()
    elif action=='status':print({'overlay':active(),'bridge':loaded(),'autostart':AUTO.exists()})
    else:raise SystemExit('Usage: taskbar-effects.py start|stop|status')
