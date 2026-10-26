const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const signal=()=>({connect(){}});
let calls=[], uniforms=[], factor=1,reduced=false,grab=true,stolen=false;
const E={Scale:1,Translation:2,ShaderUniform:3,Opacity:4,MapTexture:1,Modulate:4,WindowAddedGrabRole:10,WindowClosedGrabRole:11,WindowMinimizedGrabRole:12,WindowUnminimizedGrabRole:13,Forward:0,Backward:1,TerminateAtSource:1,TerminateAtTarget:2};
let sid=0;
const c={print(){},Effect:E,KWin:{MaximizeArea:1,ScreenArea:2},QEasingCurve:{Linear:0,OutCubic:1,InOutCubic:2},
 effects:{hasActiveFullScreenEffect:false,stackingOrder:[],clientArea:()=>({x:-1920,y:100,width:1920,height:1040})},
 effect:{readConfig:()=>reduced,grab:()=>grab,isGrabbed:()=>stolen,addFragmentShader:()=>++sid,setUniform:(...a)=>uniforms.push(a),configChanged:signal(),animationEnded:signal()},
 animationTime:n=>n*factor,animate:a=>(calls.push(a),a.animations.map((_,i)=>calls.length*10+i)),set:a=>(calls.push(a),a.animations.map((_,i)=>calls.length*10+i)),cancel:a=>calls.push({cancel:a}),redirect:(...a)=>(calls.push({redirect:a}),true)};
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
factor=0.8;fx.configure();const w=normal();fx.run(w,'minimize');assert.equal(calls.at(-1).duration,480);fx.run(w,'restore');assert.equal(calls.at(-1).redirect[1],E.Backward);fx.run(w,'minimize');assert.equal(calls.at(-1).redirect[1],E.Forward);finish(w);
fx.run(w,'restore');fx.run(w,'minimize');assert.equal(calls.at(-1).redirect[1],E.Backward);finish(w);
fx.run(w,'open');fx.run(w,'close');assert.ok(calls.at(-2).cancel);finish(w);
fx.run(w,'open');w.skipsCloseAnimation=true;fx.run(w,'close');assert.equal(w.jarvisEnergy,undefined);delete w.skipsCloseAnimation;
fx.run(w,'minimize');stolen=true;fx.changed(w,E.WindowUnminimizedGrabRole);assert.equal(w.jarvisEnergy,undefined);stolen=false;
const windows=Array.from({length:fx.slots.length+1},normal);// one more than the shader slots
windows.forEach(w=>fx.run(w,'close'));assert.equal(calls.at(-1).animations[1].type,E.Opacity);fx.stopAll();assert.equal(fx.active.length,0);assert.ok(fx.slots.every(s=>!s.busy));
function globalTarget(w,g) {
 const e=w.expandedGeometry,f=w.geometry;
 return [f.x+(1-g.scale.value1)*f.width/2+g.scale.value1*(e.x-f.x)+g.translation.value1+g.target[0],
 f.y+(1-g.scale.value2)*f.height/2+g.scale.value2*(e.y-f.y)+g.translation.value2+g.target[1]];
}
for(const panel of [{x:-1920,y:1100,width:1920,height:40},{x:-1920,y:100,width:1920,height:40},{x:-1920,y:100,width:40,height:1040},{x:-40,y:100,width:40,height:1040}]) {
 c.effects.stackingOrder=[{dock:true,visible:true,geometry:panel}];
 const w=normal();w.iconGeometry={x:panel.x+4,y:panel.y+4,width:24,height:24};
 const target=globalTarget(w,fx.geometry(w,2));
 assert.ok(Math.abs(target[0]-(panel.x+panel.width/2))<.001);
 assert.ok(Math.abs(target[1]-(panel.y+panel.height/2))<.001);
}
console.log('PASS: minimize targets the panel centre on every taskbar edge.');
c.effects.stackingOrder=[{dock:true,visible:true,geometry:{x:-1920,y:100,width:40,height:1040}}];
const noIcon={...normal(),iconGeometry:{x:0,y:0,width:0,height:0}};
const g2=fx.geometry(noIcon,2);assert.ok(globalTarget(noIcon,g2)[0]<-1870);
for(const screen of [{x:0,y:0,width:1920,height:1080},{x:1920,y:0,width:1920,height:1080},{x:0,y:-1080,width:1920,height:1080}]) {
 const w={...normal(),screen:{geometry:screen},geometry:{x:screen.x+320,y:screen.y+100,width:560,height:300},expandedGeometry:{x:screen.x+310,y:screen.y+90,width:580,height:320},iconGeometry:{x:screen.x+880,y:screen.y+1030,width:40,height:40}};
 assert.deepEqual(globalTarget(w,fx.geometry(w,2)),[screen.x+960,screen.y+1050]);
}
c.effects.stackingOrder=[];
fx.locked=true;fx.run(normal(),'minimize');assert.equal(fx.active.length,0);fx.locked=false;
console.log('PASS: real-panel fallback, offset monitor centres and locked-session rejection.');
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
 assert.deepEqual(Array.from(geo.canvas),[580,320]);
 assert.deepEqual(Array.from(geo.clip),[-310,-90,1920,1040]);
 assert.ok(geo.canvas[0]*geo.canvas[1]<4000000);
}
c.effects.stackingOrder=[{dock:true,geometry:{x:0,y:1100,width:1920,height:40}}];
assert.equal(fx.geometry(normal(),1).panel[1],946); // other monitor ignored
assert.ok(fx.geometry({...normal(),screen:{geometry:{x:0,y:0,width:3840,height:2160}}},1));
assert.equal(fx.geometry({...normal(),expandedGeometry:{x:0,y:0,width:5000,height:3000}},0),null);
const closeWindow=normal();fx.run(closeWindow,'close');
assert.equal(calls.at(-1).keepAlive,true);assert.equal(calls.at(-1).duration,384);finish(closeWindow);
console.log('PASS: window-local close canvas, all panel edges, autohide fallback, 4K monitor support and retained-close lifetime.');

const maximized=normal();fx.maximizeScan(maximized);assert.equal(maximized.jarvisEnergy.mode,5);finish(maximized);
reduced=true;fx.configure();fx.maximizeScan(normal());assert.equal(fx.active.length,0);
console.log('PASS: maximize scan releases its shader and respects reduced motion.');
// Command tickets are finite, app/identity scoped and consumed once.
reduced=false;factor=.8;fx.configure();
const cmd=normal();cmd.pid=123;cmd.caption='owned';
cmd.jarvisCommand={action:'close',expires:Date.now()+5000};fx.run(cmd,'close');assert.equal(calls.at(-1).duration,1450);assert.equal(cmd.jarvisEnergy.commanded,true);finish(cmd);
fx.run(cmd,'close');assert.equal(calls.at(-1).duration,384);finish(cmd);
fx.requests=[{action:'open',classes:['command-test'],expires:Date.now()+5000}];
fx.run(cmd,'open');assert.equal(calls.at(-1).duration,496);finish(cmd);assert.equal(fx.requests.length,1);
cmd.windowClass='command-test command-test';fx.run(cmd,'open');assert.equal(calls.at(-1).duration,1350);finish(cmd);assert.equal(fx.requests.length,0);
cmd.jarvisCommand={action:'focus',expires:Date.now()+5000};fx.run(cmd,'restore');assert.equal(calls.at(-1).duration,1200);finish(cmd);
cmd.jarvisCommand={action:'close',expires:Date.now()-1};fx.run(cmd,'close');assert.equal(calls.at(-1).duration,384);finish(cmd);
reduced=true;fx.configure();cmd.jarvisCommand={action:'close',expires:Date.now()+5000};fx.run(cmd,'close');assert.equal(cmd.jarvisEnergy,undefined);
console.log('PASS: exact command timing, one-use class targeting, expiry and reduced motion.');
// Minimize and restore must survive position-only geometry notifications.
reduced=false;factor=.8;fx.configure();
const restoreWindow=normal();fx.run(restoreWindow,'restore');const restoreState=restoreWindow.jarvisEnergy;
fx.geometryChanged(restoreWindow,{...restoreWindow.geometry});assert.equal(restoreWindow.jarvisEnergy,restoreState);finish(restoreWindow);
// Regression: no compositor cancellation or persistent transforms in completion.
const dragWindow=normal();
const realCancel=c.cancel;let completing=false;
c.cancel=a=>{assert.equal(completing,false,'cancel during animation completion');return realCancel(a)};
c.set=()=>{throw new Error('Persistent transforms are forbidden for drag')};
function endBatch(w){const n=w.jarvisEnergy.remaining;completing=true;try{for(let i=0;i<n;i++)fx.ended(w,0);}finally{completing=false;}}
fx.beginMove(dragWindow);assert.equal(dragWindow.jarvisEnergy.mode,4);
// Cursor attraction uses real desktop coordinates translated to this output.
c.effects.cursorPos={x:-1250,y:440};
fx.pointerChanged();
assert.deepEqual(Array.from(uniforms.filter(u=>u[1]==='gravityTo').at(-1)[2]),[670,340]);
assert.ok(dragWindow.jarvisEnergy.gravity);
assert.equal(calls.at(-1).animations.length,3);
assert.equal(calls.at(-1).keepAlive,false);
endBatch(dragWindow);assert.equal(dragWindow.jarvisEnergy.mode,4);
assert.equal(fx.active.filter(w=>w===dragWindow).length,1);
assert.deepEqual(Array.from(uniforms.filter(u=>u[1]==='velocity').at(-1)[2]),[0,0]);
// Window velocity comes from frame geometry, smoothed, finite and clamped.
for(let k=0;k<6;k++){dragWindow.geometry={...dragWindow.geometry,x:dragWindow.geometry.x-20};fx.geometryChanged(dragWindow,{...dragWindow.geometry,x:dragWindow.geometry.x+20});}
const velocity=uniforms.filter(u=>u[1]==='velocity').at(-1)[2];
assert.ok(velocity[0]<0&&Math.abs(velocity[1])<1e-9&&Math.hypot(...velocity)<=6000,String(velocity));
dragWindow.geometry={...dragWindow.geometry,x:dragWindow.geometry.x+1e9};fx.geometryChanged(dragWindow,{...dragWindow.geometry});
assert.ok(Math.hypot(...uniforms.filter(u=>u[1]==='velocity').at(-1)[2])<=6000.001);
dragWindow.geometry={...dragWindow.geometry,x:-1600};
fx.geometryChanged(dragWindow,{...dragWindow.geometry,x:dragWindow.geometry.x-20});
assert.equal(dragWindow.jarvisEnergy.mode,4);
fx.finishMove(dragWindow);assert.equal(dragWindow.jarvisEnergy.kind,'drag-settle');
assert.equal(calls.at(-1).animations.length,4);
assert.equal(calls.at(-1).duration,720);
endBatch(dragWindow);assert.equal(dragWindow.jarvisEnergy,undefined);
fx.beginMove(dragWindow);dragWindow.visible=false;
endBatch(dragWindow);assert.equal(dragWindow.jarvisEnergy,undefined);dragWindow.visible=true;
fx.beginMove(dragWindow);fx.stopAll();assert.equal(fx.active.length,0);
fx.beginMove(dragWindow);reduced=true;fx.configure();assert.equal(dragWindow.jarvisEnergy,undefined);
fx.beginMove(dragWindow);assert.equal(dragWindow.jarvisEnergy,undefined);reduced=false;fx.configure();
fx.beginMove(dragWindow);dragWindow.expandedGeometry.width=5000;
fx.geometryChanged(dragWindow,{...dragWindow.geometry});assert.equal(dragWindow.jarvisEnergy,undefined);
dragWindow.expandedGeometry.width=580;
for(let i=0;i<100;i++){
 fx.beginMove(dragWindow);
 if(i%2===0)endBatch(dragWindow);
 fx.finishMove(dragWindow);
 if(i%3===0){fx.beginMove(dragWindow);fx.finishMove(dragWindow);}
 endBatch(dragWindow);
 assert.equal(dragWindow.jarvisEnergy,undefined);
 assert.ok(fx.slots.every(slot=>!slot.busy));
}
c.cancel=realCancel;
// Jarvis arrange uses the old rectangle as its visual origin, ending at the real one.
dragWindow.jarvisCommand={action:'geometry',expires:Date.now()+5000};
fx.geometryChanged(dragWindow,{x:dragWindow.geometry.x-100,y:dragWindow.geometry.y-40,width:280,height:150});
assert.equal(calls.at(-1).duration,1250);assert.equal(calls.at(-1).animations[0].from.value1,.5);finish(dragWindow);
console.log('PASS: restore geometry survives, drag persists until release, commanded arrange travels from the old rectangle.');

// Slot contention keeps the same panel-centre destination in the native fallback.
c.effects.stackingOrder=[{dock:true,geometry:{x:-1920,y:1100,width:1920,height:40}}];
const fallbackWindows=Array.from({length:fx.slots.length+1},normal);fallbackWindows.forEach(w=>fx.run(w,'minimize'));
const translation=calls.at(-1).animations.find(a=>a.type===E.Translation).to;
assert.equal(translation.value1,-960-(-1600+560/2));
assert.equal(translation.value2,1120-(200+300/2));
fx.stopAll();console.log('PASS: native fallback also minimizes to the panel centre.');

c.effects.stackingOrder=[{dock:true,geometry:{x:0,y:1100,width:1920,height:40}}];
assert.equal(fx.dockTarget(noIcon).y,1138); // adjacent monitor's panel is not ours
console.log('PASS: missing-icon fallback excludes an adjacent monitor panel.');

// The task icon chooses between multiple panels; landing stays at its centre.
c.effects.stackingOrder=[{dock:true,geometry:{x:-1920,y:100,width:1920,height:40}},
 {dock:true,geometry:{x:-1720,y:1100,width:1520,height:40}}];
assert.deepEqual(JSON.parse(JSON.stringify(fx.dockTarget({...normal(),iconGeometry:{x:-1600,y:1104,width:24,height:24}}))),{x:-960,y:1120});
const topIcon={...normal(),iconGeometry:{x:-1800,y:105,width:24,height:24}};
assert.equal(fx.dockTarget(topIcon).y,120);
c.effects.stackingOrder=[];
assert.equal(fx.dockTarget(noIcon).x,-960);
// Repeated maximise/restore begins exactly at the previous visible rectangle.
const rapid=normal(),origin={x:-1400,y:260,width:280,height:150};
fx.geometryChanged(rapid,origin);
assert.equal(calls.at(-1).duration,336);
assert.equal(calls.at(-1).curve,c.QEasingCurve.InOutCubic);
rapid.jarvisEnergy.started=Date.now()+100; // clamp to the first frame
const prior={...rapid.geometry};rapid.geometry={x:-1500,y:220,width:420,height:250};
fx.geometryChanged(rapid,prior);
assert.equal(calls.at(-1).animations[0].from.value1,280/420);
assert.equal(calls.at(-1).animations[1].from.value1,origin.x+origin.width/2-rapid.geometry.x-rapid.geometry.width/2);
finish(rapid);assert.ok(fx.slots.every(s=>!s.busy));
console.log('PASS: panel selection, centred missing-panel fallback and continuous maximise retargeting.');

// Recovery mode must never enter particle drag or open/close/focus tracks.
fx.motionOnly=true;
const limited=normal();const beforeRestricted=calls.length;
for(const kind of ['open','close'])fx.run(limited,kind);
fx.maximizeScan(limited);fx.beginMove(limited);fx.finishMove(limited);fx.dragFrame(limited,0,1,4200,1,false);
assert.equal(calls.length,beforeRestricted);
fx.run(limited,'minimize');assert.equal(limited.jarvisEnergy.mode,2);
const beforeComplete=calls.length;finish(limited);assert.equal(calls.length,beforeComplete);
fx.run(limited,'restore');finish(limited);
fx.geometryChanged(limited,{x:-1500,y:220,width:280,height:150});
assert.equal(limited.jarvisEnergy.kind,'geometry');
const geometryComplete=calls.length;finish(limited);assert.equal(calls.length,geometryComplete);
fx.geometryChanged(limited,{x:-1500,y:220,width:280,height:150});fx.beginMove(limited);
assert.equal(limited.jarvisEnergy,undefined);assert.ok(fx.slots.every(s=>!s.busy));
console.log('PASS: recovery mode allows only finite dock/geometry tracks, blocks drag/open/close/focus and never mutates tracks on completion.');

// Particle opt-in does not re-enable the unrelated opening/closing/focus tracks.
fx.particleDragEnabled=true;
const optIn=normal(),beforeOptIn=calls.length;
fx.run(optIn,'open');fx.run(optIn,'close');fx.maximizeScan(optIn);
assert.equal(calls.length,beforeOptIn);
fx.beginMove(optIn);assert.equal(optIn.jarvisEnergy.kind,'drag');
const geo=fx.geometry(optIn,4);
assert.deepEqual(Array.from(geo.canvas),[1920,1040]);
assert.deepEqual(Array.from(geo.clip),[0,0,1920,1040]);
fx.finishMove(optIn);endBatch(optIn);assert.equal(optIn.jarvisEnergy,undefined);
assert.ok(fx.slots.every(slot=>!slot.busy));
console.log('PASS: fluid drag opts in independently, with output-sized finite tracks and clean release.');

// ---------------------------------------------------------------------------
// Cinematic transitions (CinematicTransitions=true): modes 6-10 + drag rebuild.
{
 const cfg={WindowMotionOnly:true,ParticleDragEnabled:true,CinematicTransitions:true,ReducedMotion:false,CloseEnabled:false};
 c.effect.readConfig=(k,d)=>k in cfg?cfg[k]:d;
 c.effects.stackingOrder=[{dock:true,geometry:{x:-1920,y:1100,width:1920,height:40}}];
 factor=.8;fx.configure();assert.equal(fx.cine,true);
 const realCancel=c.cancel;let completing=false;
 c.cancel=a=>{assert.equal(completing,false,'cancel during animation completion');return realCancel(a)};
 // keepAlive is true only for the closing pixmap; everything else is false.
 const realAnimate=c.animate;let animated=0;
 c.animate=a=>{animated++;const s=a.window.jarvisEnergy;assert.ok(s,'track without state');
  assert.equal(a.keepAlive,s.kind==='close',`keepAlive for ${s.kind}`);assert.ok(a.duration>0&&isFinite(a.duration));return realAnimate(a)};
 c.set=()=>{throw new Error('Persistent transforms are forbidden')};
 const uni=(slot,name)=>uniforms.filter(u=>u[0]===slot&&u[1]===name).at(-1)?.[2];
 function complete(w){
  const s=w.jarvisEnergy,n=s.remaining,before=calls.length;completing=true;
  try{for(let i=0;i<n;i++)fx.ended(w,0);}finally{completing=false;}
  if(s.mode!==4)assert.equal(calls.length,before,'completion appended a track');
 }
 function settled(){assert.equal(fx.active.length,0);assert.ok(fx.slots.every(s=>!s.busy),'shader slot stuck')}
 const lastAnim=()=>calls.filter(x=>x.animations).at(-1);
 // Every kind uses its own mode, a finite single batch and exact shader endpoints.
 const cases=[['open',6,608,[0,1],false],['close',8,512,[0,1],true],['minimize',7,544,[0,1],false],['restore',7,544,[1,0],false]];
 for(const [kind,mode,duration,[from,to],keep] of cases){
  const w=normal();if(kind==='restore'){w.visible=false;}
  fx.run(w,kind);const s=w.jarvisEnergy,a=lastAnim();
  assert.equal(s.mode,mode,kind);assert.equal(a.duration,duration,kind);assert.equal(a.keepAlive,keep,kind);
  assert.equal(uni(s.slot.id,'mode'),mode);assert.equal(uni(s.slot.id,'cine'),1);
  const ph=a.animations.find(x=>x.uniform==='phase');assert.equal(ph.from,from);assert.equal(ph.to,to);
  assert.ok(a.animations.every(x=>x.type!==E.Scale||JSON.stringify(x.from)===JSON.stringify(x.to)),'canvas transform must be constant');
  complete(w);assert.equal(w.jarvisEnergy,undefined);settled();
 }
 // Close canvas stays window-local; minimise shares the taskbar canvas and target.
 {const w=normal();fx.run(w,'close');assert.deepEqual(Array.from(uniforms.filter(u=>u[1]==='canvasSize').at(-1)[2]),[580,320]);complete(w);
  const m=normal();fx.run(m,'minimize');assert.deepEqual(Array.from(uniforms.filter(u=>u[1]==='target').at(-1)[2]),Array.from(fx.geometry(m,2).target));complete(m);settled();}
 // Mid-flight minimise/restore reversal keeps the same shatter track.
 {const w=normal();fx.run(w,'minimize');const ids=w.jarvisEnergy.ids;fx.run(w,'restore');
  assert.equal(calls.at(-1).redirect[1],E.Backward);assert.equal(w.jarvisEnergy.ids,ids);
  fx.run(w,'minimize');assert.equal(calls.at(-1).redirect[1],E.Forward);complete(w);settled();}
 // Position notifications never interrupt a shatter/materialise track.
 {const w=normal();fx.run(w,'restore');const s=w.jarvisEnergy;fx.geometryChanged(w,{...w.geometry,x:w.geometry.x+40});assert.equal(w.jarvisEnergy,s);complete(w);settled();}
 // Focus: dimensional phase shift, finite, keepAlive false.
 {const w=normal();fx.maximizeScan(w);const s=w.jarvisEnergy,a=lastAnim();
  assert.equal(s.mode,9);assert.equal(a.duration,368);assert.equal(a.keepAlive,false);assert.equal(uni(s.slot.id,'mode'),9);
  complete(w);settled();}
 // Maximise surges outward (+1), unmaximise powers down (-1); geometry travels from the old rectangle to exactly 1/0.
 for(const [old,variant] of [[{x:-1500,y:220,width:280,height:150},1],[{x:-1900,y:100,width:1900,height:1000},-1]]){
  const w=normal();fx.geometryChanged(w,old);const s=w.jarvisEnergy,a=lastAnim();
  assert.equal(s.mode,10);assert.equal(a.duration,416);assert.equal(a.keepAlive,false);
  assert.equal(uni(s.slot.id,'variant'),variant);assert.equal(uni(s.slot.id,'mode'),10);
  assert.equal(a.animations[0].to,1);assert.equal(a.animations[1].to,0);
  complete(w);settled();
 }
 // Drag keeps v2 intact; the release batch carries the cinematic reassembly flag.
 {const w=normal();fx.beginMove(w);assert.equal(w.jarvisEnergy.mode,4);assert.equal(uni(w.jarvisEnergy.slot.id,'cine'),1);
  complete(w);assert.equal(w.jarvisEnergy.kind,'drag');fx.finishMove(w);assert.equal(w.jarvisEnergy.kind,'drag-settle');
  const a=lastAnim();assert.equal(a.duration,720);assert.equal(a.keepAlive,false);
  assert.ok(a.animations.some(x=>x.uniform==='dragFade'&&x.to===0),'release must end at dragFade 0 (exact source)');
  complete(w);assert.equal(w.jarvisEnergy,undefined);settled();}
 // A slot left by a cinematic track never leaks cine into a legacy track.
 cfg.CinematicTransitions=false;fx.configure();assert.equal(fx.cine,false);
 {const w=normal();fx.run(w,'minimize');assert.equal(w.jarvisEnergy.mode,2);assert.equal(uni(w.jarvisEnergy.slot.id,'cine'),0);complete(w);settled();
  const n=calls.length;fx.run(normal(),'open');fx.maximizeScan(normal());assert.equal(calls.length,n,'recovery restriction without the flag');}
 cfg.CinematicTransitions=true;fx.configure();
 // Reduced motion and zero animation scaling suppress every cinematic track.
 for(const [r,f] of [[true,.8],[false,0]]){
  cfg.ReducedMotion=r;factor=f;fx.configure();const n=calls.length;
  for(const kind of ['open','close','minimize','restore'])fx.run(normal(),kind);
  fx.maximizeScan(normal());fx.geometryChanged(normal(),{x:-1500,y:220,width:280,height:150});fx.beginMove(normal());
  assert.equal(calls.filter((x,i)=>i>=n&&x.animations).length,0,'motion while reduced/zero');settled();
 }
 cfg.ReducedMotion=false;factor=.8;fx.configure();
 // Toggling the flag mid-flight stops (from the config handler, not completion).
 {const w=normal();fx.run(w,'open');cfg.CinematicTransitions=false;fx.configure();assert.equal(w.jarvisEnergy,undefined);settled();cfg.CinematicTransitions=true;fx.configure();}
 // Randomised stress: no stuck windows, no busy slots, every track finite.
 let seed=7;const rnd=()=>(seed=(seed*1103515245+12345)%2147483648)/2147483648;
 const pool=[normal(),normal(),normal(),normal()];
 for(let i=0;i<3000;i++){
  const w=pool[Math.floor(rnd()*pool.length)],op=Math.floor(rnd()*10);
  if(op===0)fx.run(w,'open');else if(op===1)fx.run(w,'close');else if(op===2)fx.run(w,'minimize');
  else if(op===3)fx.run(w,'restore');else if(op===4)fx.maximizeScan(w);
  else if(op===5){const g=w.geometry;fx.geometryChanged(w,{x:g.x+40,y:g.y+20,width:g.width-80,height:g.height-40});}
  else if(op===6)fx.beginMove(w);else if(op===7)fx.finishMove(w);
  else if(w.jarvisEnergy)complete(w);
  const a=calls.at(-1);if(a&&a.animations)assert.ok(a.duration>0&&isFinite(a.duration));
  assert.ok(fx.slots.filter(s=>s.busy).length===fx.active.filter(x=>x.jarvisEnergy&&x.jarvisEnergy.slot).length,'slot bookkeeping');
 }
 for(const w of pool){let guard=0;while(w.jarvisEnergy&&guard++<4){if(w.jarvisEnergy.persistent)fx.finishMove(w);complete(w);}assert.equal(w.jarvisEnergy,undefined,'stuck window');}
 settled();
 assert.ok(animated>500,'stress exercised too few tracks');
 c.cancel=realCancel;c.animate=realAnimate;
 console.log('PASS: cinematic modes 6-10 are finite single batches with exact endpoints, reversal, reduced/zero motion, flag isolation and 3000-step stress without stuck windows or slots.');
}
