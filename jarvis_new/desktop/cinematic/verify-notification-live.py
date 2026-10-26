"""Exercise a transient owned notification and inspect its live compositor tracks."""
import json
import subprocess
import time
from pathlib import Path

def trace():
    return subprocess.check_output(['qdbus','org.kde.KWin','/Effects','org.kde.kwin.Effects.debug','jarvis-surfaces',''],text=True).strip()

nid=subprocess.check_output(['notify-send','--print-id','--app-name=Jarvis','--hint=int:transient:1','--expire-time=2500','Energy effects online','Blue scan reveal · illuminated edges · fragment dissolve'],text=True).strip()
rows=[]
try:
    start=time.monotonic()
    while time.monotonic()-start<4.2:
        rows.append({'ms':round((time.monotonic()-start)*1000),'trace':trace()})
        time.sleep(.035)
finally:
    subprocess.run(['qdbus','org.freedesktop.Notifications','/org/freedesktop/Notifications','org.freedesktop.Notifications.CloseNotification',nid],check=True)
path=Path(__file__).with_name('validation')/'notification-heavy-live.json'
path.write_text(json.dumps(rows,indent=2)+'\n')
# KWin 6.7 leaves ShaderUniform's attribute name blank in debug output.
# The fourth track and constant opacity distinguish shader from native fallback.
assert any(r['trace'].count('Animation:')==4 and 'From: 0.84,0.94' in r['trace'] for r in rows), 'Shader arrival missing'
assert any(r['trace'].count('Animation:')==4 and 'To: 0.88,0.94' in r['trace'] for r in rows), 'Shader departure missing'
assert rows[-1]['trace']=='No window is animated', 'Animation did not clean up'
print('PASS: four live tracks on arrival and dismissal, including the shader; clean completion.')
