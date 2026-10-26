"""Derive translucent electric-blue Plasma assets, preserving native SVG element IDs."""
from pathlib import Path
import json,shutil,xml.etree.ElementTree as ET
ROOT=Path(__file__).resolve().parent
source=Path.home()/'.local/share/plasma/desktoptheme/stark-os'
dest=ROOT/'plasma/desktoptheme/jarvis-blue-glass'
if dest.exists():shutil.rmtree(dest)
shutil.copytree(source,dest)
ET.register_namespace('','http://www.w3.org/2000/svg')
count=0
for path in dest.rglob('*.svg'):
    tree=ET.parse(path)
    original_ids={e.get('id') for e in tree.iter() if e.get('id')}
    def update(el,mask=False):
        mask=mask or 'mask' in el.get('id','')
        if not mask:
            fill=el.get('fill','').lower()
            opacity=float(el.get('fill-opacity','1'))
            if fill.startswith('#') and len(fill)==7 and opacity>.1:
                rgb=[int(fill[i:i+2],16) for i in [1,3,5]]
                if max(rgb)<105 and sum(rgb)<200:
                    el.set('fill','#082e68')
                    if 'opaque' not in path.parts:
                        el.set('fill-opacity','0.24' if path.name in ['background.svg','tooltip.svg'] else '0.19')
            for key in ['fill','stroke','stop-color']:
                if el.get(key,'').lower() in ['#5fe3ff','#38d5f7','#7be7ff']:
                    el.set(key,'#57baff')
        for child in el:update(child,mask)
    update(tree.getroot());assert original_ids=={e.get('id') for e in tree.iter() if e.get('id')}
    tree.write(path,encoding='utf-8',xml_declaration=True);count+=1
meta=json.loads((dest/'metadata.json').read_text());meta['KPlugin'].update(Id='jarvis-blue-glass',Name='Jarvis Electric Glass',Version='3.0',Description='Translucent blue glass surfaces, refractive edge highlights and native backdrop blur.')
(dest/'metadata.json').write_text(json.dumps(meta,indent=2)+'\n')
(dest/'plasmarc').write_text('[Settings]\nFallbackTheme=default\n\n[BlurBehindEffect]\nenabled=true\n\n[ContrastEffect]\nenabled=false\n\n[AdaptiveTransparency]\nenabled=false\n')
print('Generated',count,'blue-glass SVG assets with original IDs preserved.')
