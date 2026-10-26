// Logic checks for the jarvis-energy effect: slot self-healing (the drag
// particles stopped once both shader slots leaked), the cross-monitor drag
// field, the distinct resize trace (mode 11) and the fullscreen deploy (mode 12).
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const signal=()=>({connect(){}});
let calls=[],uniforms=[],factor=.8,sid=0;
const cfg={WindowMotionOnly:true,ParticleDragEnabled:true,CinematicTransitions:true,ReducedMotion:false,CloseEnabled:true,
 ResizeTraceEnabled:true,FullscreenCinematic:true,FullscreenClasses:'brave'};
const E={Scale:1,Translation:2,ShaderUniform:3,Opacity:4,Size:5,Position:6,CrossFadePrevious:7,MapTexture:1,Modulate:4,
 WindowAddedGrabRole:10,WindowClosedGrabRole:11,WindowMinimizedGrabRole:12,WindowUnminimizedGrabRole:13,Forward:0,Backward:1,TerminateAtSource:1,TerminateAtTarget:2};
const c={print(){},console:{warn(){},log(){}},Effect:E,KWin:{},QEasingCurve:{Linear:0,OutCubic:1,InOutCubic:2},
 effects:{hasActiveFullScreenEffect:false,stackingOrder:[],screens:[]},
 effect:{readConfig:(k,d)=>k in cfg?cfg[k]:d,grab:()=>true,isGrabbed:()=>false,addFragmentShader:()=>++sid,setUniform:(...a)=>uniforms.push(a),configChanged:signal(),animationEnded:signal()},
 animationTime:n=>n*factor,animate:a=>(calls.push(a),a.animations.map((_,i)=>calls.length*10+i)),
 cancel:a=>calls.push({cancel:a}),redirect:()=>true,set:()=>{throw new Error('persistent set() is forbidden')}};
for(const s of ['windowAdded','windowClosed','windowDeleted','windowDataChanged','activeFullScreenEffectChanged','desktopChanged','screenLockingChanged','showingDesktopChanged','currentActivityChanged','virtualScreenGeometryChanged'])c.effects[s]=signal();
c.Date=Date;// shared clock so tests can advance time
vm.createContext(c);vm.runInContext(fs.readFileSync(__dirname+'/kwin/effects/jarvis-energy/contents/code/main.js','utf8'),c);
const fx=c.energy;
const left={x:0,y:0,width:1920,height:1080},right={x:1920,y:0,width:1920,height:1080};
c.effects.screens=[{geometry:left},{geometry:right}];
let serial=0;
const win=(extra={})=>({visible:true,managed:true,normalWindow:true,windowClass:'konsole org.kde.konsole',internalId:`{0000000${++serial}-1111-2222-3333-444455556666}`,
 screen:{geometry:left},geometry:{x:300,y:200,width:800,height:500},expandedGeometry:{x:290,y:190,width:820,height:520},...extra});
const uni=(slot,name)=>uniforms.filter(u=>u[0]===slot&&u[1]===name).at(-1)?.[2];
const last=()=>calls.filter(x=>x.animations).at(-1);
const state=w=>fx.stateOf(w);
function complete(w){const s=state(w);for(let i=s.remaining;i>0;i--)fx.ended(w,0);}
function settled(){assert.equal(fx.active.length,0,'active windows left');assert.ok(fx.slots.every(s=>!s.busy),'busy slot left');}
fx.configure();
assert.ok(fx.slots.length>=3,'enough shader slots for focus + drag + one more');

// ---- 1. Slot ownership is identity-safe and self-healing ----------------------
{
 // KWin can hand the script another wrapper for the same window: completion
 // and deletion through that wrapper must still free the slot.
 const a=win(),b={...a};delete b.jarvisEnergy;
 fx.beginMove(a);assert.equal(state(a).mode,4);
 const ownerSlot=state(a).slot;assert.ok(ownerSlot.busy);
 fx.finishMove(b);assert.equal(state(a).kind,'drag-settle','finish via a second wrapper reaches the same track');
 complete(b);assert.equal(state(a),undefined);settled();
 fx.beginMove(a);fx.deleted(b);settled();
 // A track whose completion never arrives is reclaimed instead of leaking.
 const realNow=Date.now;let now=realNow();Date.now=()=>now;
 try{
  const leaks=Array.from({length:fx.slots.length},()=>win());
  leaks.forEach(w=>fx.geometryChanged(w,{x:100,y:100,width:400,height:300}));
  assert.ok(fx.slots.every(s=>s.busy),'all slots in use');
  const dragger=win();fx.beginMove(dragger);assert.equal(state(dragger),undefined,'no slot while every slot is legitimately busy');
  now+=60000;fx.beginMove(dragger);
  assert.equal(state(dragger)&&state(dragger).mode,4,'overdue tracks are reclaimed so drag particles always play');
  leaks.forEach(w=>assert.equal(state(w),undefined));
  fx.stopAll();settled();
 } finally {Date.now=realNow;}
 // Orphaned busy flags (no owner record) are reclaimed on the next request.
 fx.slots.forEach(s=>{s.busy=true;s.state=null;});
 const w=win();fx.beginMove(w);assert.equal(state(w).mode,4);fx.stopAll();settled();
 // A repeated start signal keeps the running hold (no restart, no second slot).
 const r=win();fx.beginMove(r);const first=state(r);fx.beginMove(r);assert.equal(state(r),first);
 assert.equal(fx.slots.filter(s=>s.busy).length,1);fx.stopAll();settled();
}
console.log('PASS: shader slots survive wrapper mismatches, missed completions and orphaned flags.');

// ---- 2. Drag field spans monitors in desktop space -------------------------------
{
 const w=win({geometry:{x:1700,y:200,width:800,height:500},expandedGeometry:{x:1690,y:190,width:820,height:520}});
 c.effects.cursorPos={x:2000,y:400};
 fx.beginMove(w);const s=state(w);
 assert.deepEqual(Array.from(uni(s.slot.id,'canvasSize')),[3840,1080],'canvas grows over both monitors the window touches');
 assert.deepEqual(Array.from(uni(s.slot.id,'desktopOrigin')),[0,0]);
 assert.deepEqual(Array.from(uni(s.slot.id,'gravityTo')),[2000,400],'pointer on the second monitor is not clamped away');
 // Fully on the right monitor: canvas follows, attractor stays at the same desktop point.
 w.screen={geometry:right};w.geometry={x:2400,y:200,width:800,height:500};w.expandedGeometry={x:2390,y:190,width:820,height:520};
 c.effects.cursorPos={x:2700,y:400};
 fx.geometryChanged(w,{x:2380,y:200,width:800,height:500});
 const s2=state(w);assert.equal(s2.mode,4);assert.equal(s2.clockFrom>=0,true);
 assert.deepEqual(Array.from(uni(s2.slot.id,'desktopOrigin')),[1920,0]);
 assert.deepEqual(Array.from(uni(s2.slot.id,'gravityTo')),[780,400]);
 // Oversized unions fall back to the owning output.
 c.effects.screens=[{geometry:left},{geometry:right},{geometry:{x:3840,y:0,width:1920,height:1080}}];
 w.screen={geometry:right};w.geometry={x:1700,y:200,width:2400,height:500};w.expandedGeometry={x:1690,y:190,width:2420,height:520};
 const geo=fx.geometry(w,4);assert.ok(geo.canvas[0]<=4096&&geo.canvas[0]*geo.canvas[1]<=9000000,String(geo.canvas));
 c.effects.screens=[{geometry:left},{geometry:right}];
 fx.finishMove(w);complete(w);settled();
}
console.log('PASS: drag particles span every monitor the window or pointer touches, within texture bounds.');

// ---- 3. Resize gets its own trace, never on move/maximize/fullscreen --------------------
{
 const w=win({resize:true});
 fx.beginMove(w);const s=state(w);
 assert.equal(s.mode,11);assert.equal(s.kind,'resize');assert.equal(uni(s.slot.id,'mode'),11);
 assert.equal(last().keepAlive,false);
 // Dragging the right edge: only that edge is traced; growth streams outward.
 const old={...w.geometry};w.geometry={...old,width:old.width+30};
 fx.geometryChanged(w,old);const s2=state(w);
 assert.equal(s2.mode,11);assert.deepEqual(Array.from(s2.edges),[0,0,1,0]);assert.equal(uni(s2.slot.id,'variant'),1);
 assert.deepEqual(Array.from(uni(s2.slot.id,'edges')),[0,0,1,0]);
 // Then the corner (bottom too): sticky union; shrinking flips the flow.
 const o2={...w.geometry};w.geometry={...o2,width:o2.width-10,height:o2.height-20};
 fx.geometryChanged(w,o2);assert.deepEqual(Array.from(state(w).edges),[0,0,1,1]);assert.equal(state(w).grow,-1);
 // Held clock renews like drag; release fades out to the exact source.
 complete(w);assert.equal(state(w).kind,'resize');
 fx.finishMove(w);const settle=state(w);assert.equal(settle.kind,'resize-settle');
 assert.ok(last().animations.some(a=>a.uniform==='dragFade'&&a.to===0));assert.equal(last().duration,448);
 complete(w);settled();
 // Left/top edges are detected from the origin moving with the size.
 const lt=win({resize:true});fx.beginMove(lt);const o3={...lt.geometry};
 lt.geometry={x:o3.x-40,y:o3.y-25,width:o3.width+40,height:o3.height+25};fx.geometryChanged(lt,o3);
 assert.deepEqual(Array.from(state(lt).edges),[1,1,0,0]);fx.finishMove(lt);complete(lt);settled();
 // Plain move stays the particle drag.
 const m=win({move:true});fx.beginMove(m);assert.equal(state(m).mode,4);fx.finishMove(m);complete(m);settled();
 // Maximize (no interactive flag) is the geometry surge, not the trace.
 const x=win();fx.geometryChanged(x,{x:400,y:300,width:400,height:300});assert.equal(state(x).mode,10);complete(x);settled();
 // A geometry-only start (no start signal) still picks up the edges on its first event.
 const g=win({resize:true});const o4={...g.geometry};g.geometry={...o4,height:o4.height+12};
 fx.geometryChanged(g,o4);assert.equal(state(g).mode,11);assert.deepEqual(Array.from(state(g).edges),[0,0,0,1]);
 fx.finishMove(g);complete(g);settled();
 // Gates: disabled flag, reduced motion, zero scale, locked session, fullscreen windows.
 for(const [key,val] of [['ResizeTraceEnabled',false],['ReducedMotion',true]]){
  const before=cfg[key];cfg[key]=val;fx.configure();const n=calls.length;
  const q=win({resize:true});fx.beginMove(q);
  assert.ok(!state(q)||state(q).mode!==11,key);fx.stopAll();cfg[key]=before;fx.configure();assert.ok(calls.length>=n);
 }
 fx.locked=true;const lk=win({resize:true});fx.beginMove(lk);assert.equal(state(lk),undefined);fx.locked=false;
 const fsw=win({resize:true,fullScreen:true});fx.beginMove(fsw);assert.equal(state(fsw),undefined);
 settled();
}
console.log('PASS: resize has its own edge trace (sticky edges, grow/shrink flow, release lock) and never fires for move, maximize or gated windows.');

// ---- 4. Fullscreen enter/exit -------------------------------------------------
{
 const brave=()=>win({windowClass:'brave-browser brave-browser'});
 const windowed={x:300,y:200,width:800,height:500};
 // Wayland order: state flag first, new buffer size after.
 const w=brave();w.fullScreen=true;fx.fullScreenChanged(w);assert.equal(state(w),undefined,'waits for the new size');
 w.geometry={...left};fx.geometryChanged(w,windowed);
 const s=state(w);assert.equal(s.kind,'fullscreen');assert.equal(s.mode,12);assert.equal(uni(s.slot.id,'mode'),12);
 assert.equal(uni(s.slot.id,'variant'),1);
 const a=last();assert.equal(a.keepAlive,false);assert.equal(a.duration,448);
 assert.equal(a.animations[0].from.value1,800/1920);assert.equal(a.animations[0].to,1);assert.equal(a.animations[1].to,0);
 assert.ok(a.animations.some(x=>x.uniform==='phase'&&x.from===0&&x.to===1));
 complete(w);settled();
 // Exit, X11 order: geometry first (the flag is still set), then the flag.
 w.geometry={...windowed};fx.geometryChanged(w,{...left});
 assert.equal(state(w),undefined,'still-fullscreen geometry is not a normal surge');
 w.fullScreen=false;fx.fullScreenChanged(w);
 const e=state(w);assert.equal(e.kind,'fullscreen');assert.equal(uni(e.slot.id,'variant'),-1);assert.equal(last().duration,368);
 // A follow-up configure retargets from the visible rectangle instead of jumping.
 const t0=last();w.geometry={...windowed,x:windowed.x+8};fx.geometryChanged(w,{...windowed});
 assert.equal(state(w).kind,'fullscreen');assert.notEqual(last(),t0);complete(w);settled();
 // Across monitors: the morph starts on the old output.
 const xm=brave();xm.fullScreen=true;xm.screen={geometry:right};fx.fullScreenChanged(xm);
 xm.geometry={...right};fx.geometryChanged(xm,windowed);
 assert.equal(last().animations[1].from.value1,(300+400)-(1920+960));complete(xm);settled();
 // Other applications keep the stock morph (size/position + cross-fade), no shader.
 const k=win();k.fullScreen=true;fx.fullScreenChanged(k);k.geometry={...left};fx.geometryChanged(k,windowed);
 const st=state(k);assert.equal(st.kind,'fullscreen');assert.equal(st.slot,null);
 assert.deepEqual(Array.from(last().animations,x=>x.type),[E.Size,E.Position,E.CrossFadePrevious]);assert.equal(last().duration,200);
 complete(k);settled();
 // A stale flag never animates an unrelated later resize.
 const stale=brave();const realNow=Date.now;let now=realNow();Date.now=()=>now;
 try{stale.fullScreen=true;fx.fullScreenChanged(stale);now+=5000;stale.fullScreen=false;
  stale.geometry={...windowed,width:900};fx.geometryChanged(stale,windowed);assert.notEqual(state(stale)&&state(stale).kind,'fullscreen');fx.stopAll();}
 finally{Date.now=realNow;}
 // Gates.
 cfg.ReducedMotion=true;fx.configure();const r=brave();r.fullScreen=true;fx.fullScreenChanged(r);r.geometry={...left};fx.geometryChanged(r,windowed);
 assert.equal(state(r),undefined);cfg.ReducedMotion=false;fx.configure();
 fx.locked=true;const l=brave();l.fullScreen=true;fx.fullScreenChanged(l);l.geometry={...left};fx.geometryChanged(l,windowed);assert.equal(state(l),undefined);fx.locked=false;
 cfg.FullscreenCinematic=false;fx.configure();const plain=brave();plain.fullScreen=true;fx.fullScreenChanged(plain);plain.geometry={...left};fx.geometryChanged(plain,windowed);
 assert.equal(state(plain).slot,null);complete(plain);cfg.FullscreenCinematic=true;fx.configure();
 settled();
}
console.log('PASS: fullscreen deploys/folds for Brave in either event order, retargets, crosses monitors, keeps the stock morph elsewhere and honours every gate.');

// ---- 5. Randomised stress across all kinds ------------------------------------
{
 let seed=11;const rnd=()=>(seed=(seed*1103515245+12345)%2147483648)/2147483648;
 const pool=[win(),win({windowClass:'brave'}),win(),win()];
 for(let i=0;i<4000;i++){
  const w=pool[Math.floor(rnd()*pool.length)],op=Math.floor(rnd()*12),g=w.geometry;
  w.move=false;w.resize=false;
  if(op===0){w.move=true;fx.beginMove(w);}else if(op===1){w.resize=true;fx.beginMove(w);}
  else if(op===2)fx.finishMove(w);
  else if(op===3){const o={...g};w.geometry={...g,width:Math.max(200,g.width+Math.round((rnd()-.5)*40))};w.resize=rnd()<.5;fx.geometryChanged(w,o);}
  else if(op===4){w.fullScreen=!w.fullScreen;fx.fullScreenChanged(w);}
  else if(op===5){const o={...g};w.geometry=w.fullScreen?{...left}:{x:300,y:200,width:800,height:500};fx.geometryChanged(w,o);}
  else if(op===6)fx.run(w,rnd()<.5?'minimize':'restore');else if(op===7)fx.maximizeScan(w);
  else if(op===8&&state(w))complete(w);else if(op===9)fx.run(w,'open');
  const busy=fx.slots.filter(s=>s.busy).length,owned=Object.values(fx.states).filter(s=>s.slot).length;
  assert.equal(busy,owned,'slot bookkeeping at step '+i);
  assert.equal(new Set(fx.active.map(x=>fx.key(x))).size,fx.active.length,'duplicate active entries');
 }
 for(const w of pool){let guard=0;while(state(w)&&guard++<6){w.move=w.resize=false;if(state(w).persistent)fx.finishMove(w);if(state(w))complete(w);}assert.equal(state(w),undefined);}
 settled();
}
console.log('PASS: 4000-step stress over drag, resize, fullscreen, dock and focus leaves no stuck window or slot.');

// Regression: QtQml WeakMap.get segfaulted KWin (windowClosed -> WeakMap) on a
// window wrapper being released. The effect must never use WeakMap.
{
  const src = require('fs').readFileSync(require('path').join(__dirname, 'kwin/effects/jarvis-energy/contents/code/main.js'), 'utf8');
  require('assert').ok(!/\bnew WeakMap\b|\bWeakMap\(/.test(src), 'jarvis-energy must not use WeakMap');
  console.log('PASS: jarvis-energy avoids QtQml WeakMap (KWin SEGV regression).');
}
