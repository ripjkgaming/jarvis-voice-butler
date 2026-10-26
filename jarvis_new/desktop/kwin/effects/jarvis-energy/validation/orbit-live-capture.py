from pathlib import Path
import subprocess,os,json
p=Path.home()/'.local/share/kwin/effects/jarvis-energy/contents/code/main.js'
s=p.read_text()
out=Path('/mnt/data/jarvis-voice-butler/jarvis_new/desktop/kwin/effects/jarvis-energy/validation')
def reload():
 for method in ['unloadEffect','loadEffect']:
  result=subprocess.check_output(['qdbus','org.kde.KWin','/Effects','org.kde.kwin.Effects.'+method,'jarvis-energy'],text=True)
  if method=='loadEffect' and result.strip()!='true':raise RuntimeError(result)
records=[]
try:
 for kind,phase in [('open',.48),('close',.10),('close',.35),('close',.68),('close',.87)]:
  hook='\nvar originalRun=energy.run;energy.run=function(w,kind){originalRun(w,kind);if(w.caption === "Jarvis Energy — owned frozen capture" && kind === '+json.dumps(kind)+' && w.jarvisEnergy){freezeInTime(w.jarvisEnergy.ids,'+str(phase)+'*(kind==="open"?energy.openTime:energy.closeTime));console.warn("ORBIT_CAPTURE "+JSON.stringify({kind:kind,geo:energy.geometry(w,kind==="open"?0:1)}));}};'
  p.write_text(s+hook);reload()
  name='orbit-live-'+kind+'-'+str(phase)+'.png'
  subprocess.run(['python','/tmp/jarvis-orbit-work/capture.py'],env={**os.environ,'QT_QPA_PLATFORM':'xcb','CAPTURE_KIND':kind,'CAPTURE_OUTPUT':str(out/name)},check=True,timeout=20)
  records.append({'kind':kind,'phase':phase,'image':name})
finally:
 p.write_text(s);reload()
(out/'orbit-live-captures.json').write_text(json.dumps(records,indent=2))
