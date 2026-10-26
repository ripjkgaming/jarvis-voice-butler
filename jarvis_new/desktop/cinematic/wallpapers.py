#!/usr/bin/env python3
"""Apply the generated wallpaper pair through Plasma's native desktop API."""
import datetime,json,shutil,subprocess,sys
from pathlib import Path
from zoneinfo import ZoneInfo
ROOT=Path(__file__).resolve().parent
DEST=Path.home()/'.local/share/wallpapers/JarvisGlass'
BACKUPS=Path.home()/'.local/share/jarvis-desktop-backups'
FILES=['jarvis-glass-signal-core.png','jarvis-glass-signal-drift.png']
def plasma(script):
 return subprocess.check_output(['qdbus','org.kde.plasmashell','/PlasmaShell','org.kde.PlasmaShell.evaluateScript',script],text=True).strip()
def status():
 return json.loads(plasma('var out=[]; desktops().forEach(function(d){d.currentConfigGroup=["Wallpaper","org.kde.image","General"];out.push({id:d.id,plugin:d.wallpaperPlugin,image:d.readConfig("Image",""),fillMode:d.readConfig("FillMode",2)});});print(JSON.stringify(out));'))
def apply(rows):
 plasma('var rows='+json.dumps(rows)+'; desktops().forEach(function(d){var s=rows.find(function(v){return v.id===d.id;});if(!s)return;d.wallpaperPlugin=s.plugin;d.currentConfigGroup=["Wallpaper","org.kde.image","General"];d.writeConfig("Image",s.image);d.writeConfig("FillMode",s.fillMode);});')
def restore(folder):
 state=json.loads((folder/'wallpapers.json').read_text())
 for name in FILES:
  if (folder/name).exists():shutil.copy2(folder/name,DEST/name)
 apply(state)
 assert status()==state,'Wallpaper state differs after restoration'
 print('Wallpaper settings restored: '+str(folder))
def install():
 old=status();stamp=datetime.datetime.now(ZoneInfo('Asia/Singapore')).strftime('%Y%m%d-%H%M%S-%f')
 folder=BACKUPS/('glass-wallpapers-'+stamp);folder.mkdir(parents=True)
 (folder/'wallpapers.json').write_text(json.dumps(old,indent=2)+'\n')
 DEST.mkdir(parents=True,exist_ok=True)
 for name in FILES:
  if (DEST/name).exists():shutil.copy2(DEST/name,folder/name)
  shutil.copy2(ROOT.parent/'wallpapers'/name,DEST/name)
 new=[{**row,'plugin':'org.kde.image','image':(DEST/FILES[1 if 'sector-02' in row['image'] or 'signal-drift' in row['image'] else 0]).as_uri(),'fillMode':2} for row in old]
 try:
  apply(new)
  assert status()==new,'Wallpaper state differs after application'
 except Exception:
  restore(folder);raise
 (BACKUPS/'glass-wallpapers-latest').write_text(str(folder)+'\n')
 print('Applied wallpaper pair to '+str(len(new))+' desktop containments. Backup: '+str(folder))
if __name__=='__main__':
 action=sys.argv[1] if len(sys.argv)>1 else 'status'
 if action=='install':install()
 elif action=='restore':restore(Path(sys.argv[2]) if len(sys.argv)>2 else Path((BACKUPS/'glass-wallpapers-latest').read_text().strip()))
 elif action=='status':print(json.dumps(status(),indent=2))
 else:raise SystemExit('Usage: wallpapers.py install|restore [snapshot]|status')
