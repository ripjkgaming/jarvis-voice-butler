const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const signal=()=>({connect(){}});
let calls=[], uniforms=[], factor=1,reduced=false,grab=true,stolen=false;
const E={Scale:1,Translation:2,ShaderUniform:3,Opacity:4,MapTexture:1,Modulate:4,WindowAddedGrabRole:10,WindowClosedGrabRole:11,WindowMinimizedGrabRole:12,WindowUnminimizedGrabRole:13,Forward:0,Backward:1,TerminateAtSource:1,TerminateAtTarget:2};
let sid=0;
const c={print(){},Effect:E,KWin:{MaximizeArea:1,ScreenArea:2},QEasingCurve:{Linear:0,OutCubic:1},
 effects:{hasActiveFullScreenEffect:false,stackingOrder:[],clientArea:()=>({x:-1920,y:100,width:1920,height:1040})},
 effect:{readConfig:()=>reduced,grab:()=>grab,isGrabbed:()=>stolen,addFragmentShader:()=>++sid,setUniform:(...a)=>uniforms.push(a),configChanged:signal(),animationEnded:signal()},
 animationTime:n=>n*factor,animate:a=>(calls.push(a),a.animations.map((_,i)=>calls.length*10+i)),cancel:a=>calls.push({cancel:a}),redirect:(...a)=>(calls.push({redirect:a}),true)};
for(const s of ['windowAdded','windowClosed','windowDeleted','windowDataChanged','activeFullScreenEffectChanged','desktopChanged','screenLockingChanged','showingDesktopChanged','currentActivityChanged','virtualScreenGeometryChanged'])c.effects[s]=signal();
vm.createContext(c);vm.runInContext(fs.readFileSync(__dirname+'/kwin/effects/jarvis-energy/contents/code/main.js','utf8'),c);
const fx=c.energy;
const normal=()=>({visible:true,managed:true,normalWindow:true,windowClass:'preview',screen:{geometry:{x:-1920,y:100,width:1920,height:1040}},geometry:{x:-1600,y:200,width:560,height:300},expandedGeometry:{x:-1610,y:190,width:580,height:320},iconGeometry:{x:-1900,y:1100,width:40,height:40},minimizedChanged:signal(),windowFrameGeometryChanged:signal()});
function finish(w){let count=w.jarvisEnergy.remaining;while(count--)fx.ended(w,0);assert.equal(w.jarvisEnergy,undefined)}
for(const kind of ['open','close','minimize','restore']){const w=normal();if(kind==='minimize'){w.visible=false;w.minimized=true;}fx.run(w,kind);assert.equal(calls.at(-1).animations.length,3);finish(w);}
assert.equal(fx.active.length,0);assert.ok(fx.slots.every(s=>!s.busy));
for(const prop of ['lockScreen','outline','popupWindow','desktopWindow','dock','fullScreen','splash','notification','onScreenDisplay','hiddenByShowDesktop']){const n=calls.length;for(const kind of ['open','close','minimize','restore'])fx.run({...normal(),[prop]:true},kind);assert.equal(calls.length,n,prop)}
for(const cls of ['jarvis-watch','jarvis_watch','pinentry','polkit-kde','kwallet','kscreenlocker','ksmserver','spectacle','plasmashell']){const n=calls.length;fx.run({...normal(),windowClass:cls},'open');assert.equal(calls.length,n,cls)}
for(const kind of ['open','close','minimize','restore']){const n=calls.length;reduced=true;fx.configure();fx.run(normal(),kind);assert.equal(calls.length,n)}
reduced=false;factor=0;fx.configure();fx.run(normal(),'close');assert.equal(fx.active.length,0);
factor=0.8;fx.configure();const w=normal();fx.run(w,'minimize');assert.equal(calls.at(-1).duration,368);fx.run(w,'restore');assert.equal(calls.at(-1).redirect[1],E.Backward);fx.run(w,'minimize');assert.equal(calls.at(-1).redirect[1],E.Forward);finish(w);
fx.run(w,'restore');fx.run(w,'minimize');assert.equal(calls.at(-1).redirect[1],E.Backward);finish(w);
fx.run(w,'open');fx.run(w,'close');assert.ok(calls.at(-2).cancel);finish(w);
fx.run(w,'open');w.skipsCloseAnimation=true;fx.run(w,'close');assert.equal(w.jarvisEnergy,undefined);delete w.skipsCloseAnimation;
fx.run(w,'minimize');stolen=true;fx.changed(w,E.WindowUnminimizedGrabRole);assert.equal(w.jarvisEnergy,undefined);stolen=false;
const windows=[normal(),normal(),normal()];windows.forEach(w=>fx.run(w,'close'));assert.equal(calls.at(-1).animations[1].type,E.Opacity);fx.stopAll();assert.equal(fx.active.length,0);assert.ok(fx.slots.every(s=>!s.busy));
function globalTarget(w,g) {
 const e=w.expandedGeometry,f=w.geometry;
 return [f.x+(1-g.scale.value1)*f.width/2+g.scale.value1*(e.x-f.x)+g.translation.value1+g.target[0],
 f.y+(1-g.scale.value2)*f.height/2+g.scale.value2*(e.y-f.y)+g.translation.value2+g.target[1]];
}
for(const panel of [{x:-1920,y:1100,width:1920,height:40},{x:-1920,y:100,width:1920,height:40}]) {
 c.effects.stackingOrder=[{dock:true,visible:true,geometry:panel}];
 const w=normal(),target=globalTarget(w,fx.geometry(w,2));
 assert.ok(Math.abs(target[0]-(w.geometry.x+w.geometry.width/2))<.001);
 assert.ok(Math.abs(target[1]-(panel.y+panel.height/2))<.001);
}
console.log('PASS: top/bottom panel entry holds window center X; no sideways task-icon chase.');
c.effects.stackingOrder=[{dock:true,visible:true,geometry:{x:-1920,y:100,width:40,height:1040}}];
const noIcon={...normal(),iconGeometry:{x:0,y:0,width:0,height:0}};
const g2=fx.geometry(noIcon,2);assert.ok(g2.target[0]<g2.source[0]);
c.effects.stackingOrder=[];
fx.locked=true;fx.run(normal(),'minimize');assert.equal(fx.active.length,0);fx.locked=false;
console.log('PASS: top/left/right task icons, real-panel fallback and locked-session rejection.');
// Close canvas is exactly the relevant monitor; panel clipped to that monitor.
for(const panel of [
 {x:-1920,y:1100,width:1920,height:40},
 {x:-1920,y:100,width:1920,height:40},
 {x:-1920,y:100,width:40,height:1040},
 {x:-40,y:100,width:40,height:1040},
 {x:-2200,y:1100,width:1000,height:40},
 {x:-1920,y:1140,width:1920,height:40}
]) {
 c.effects.stackingOrder=[{dock:true,geometry:panel}];
 const geo=fx.geometry(normal(),1);
 assert.deepEqual(Array.from(geo.canvas),[1920,1040]);
 assert.deepEqual(Array.from(geo.clip),[0,0,1920,1040]);
 assert.ok(geo.panel[0]>=0 && geo.panel[1]>=0);
 assert.ok(geo.panel[0]+geo.panel[2]<=1920 && geo.panel[1]+geo.panel[3]<=1040);
}
c.effects.stackingOrder=[{dock:true,geometry:{x:0,y:1100,width:1920,height:40}}];
assert.equal(fx.geometry(normal(),1).panel[1],1036); // other monitor ignored
assert.equal(fx.geometry({...normal(),screen:{geometry:{x:0,y:0,width:3840,height:2160}}},1),null);
assert.equal(fx.geometry({...normal(),expandedGeometry:{x:0,y:0,width:5000,height:3000}},0),null);
const closeWindow=normal();fx.run(closeWindow,'close');
assert.equal(calls.at(-1).keepAlive,true);assert.equal(calls.at(-1).duration,2400);finish(closeWindow);
console.log('PASS: close monitor isolation, all panel edges, autohide fallback, 4K fallback and retained-close lifetime.');

const maximized=normal();fx.maximizeScan(maximized);assert.equal(maximized.jarvisEnergy.mode,3);finish(maximized);
reduced=true;fx.configure();fx.maximizeScan(normal());assert.equal(fx.active.length,0);
console.log('PASS: maximize scan releases its shader and respects reduced motion.');
