const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const signal=()=>({connect(){}});
let calls=[], uniforms=[], factor=1,reduced=false,grab=true,stolen=false;
const E={Scale:1,Translation:2,ShaderUniform:3,Opacity:4,MapTexture:1,Modulate:4,WindowAddedGrabRole:10,WindowClosedGrabRole:11,WindowMinimizedGrabRole:12,WindowUnminimizedGrabRole:13,Forward:0,Backward:1,TerminateAtSource:1,TerminateAtTarget:2};
let sid=0;
const c={print(){},Effect:E,KWin:{MaximizeArea:1,ScreenArea:2},QEasingCurve:{Linear:0,OutCubic:1,InOutCubic:2},
 effects:{hasActiveFullScreenEffect:false,stackingOrder:[],clientArea:()=>({x:-1920,y:100,width:1920,height:1040})},
 effect:{readConfig:(key)=>key==="WindowMotionOnly" ? true : key==="ReducedMotion" ? reduced : false,grab:()=>grab,isGrabbed:()=>stolen,addFragmentShader:()=>++sid,setUniform:(...a)=>uniforms.push(a),configChanged:signal(),animationEnded:signal()},
 animationTime:n=>n*factor,animate:a=>(calls.push(a),a.animations.map((_,i)=>calls.length*10+i)),set:a=>(calls.push(a),a.animations.map((_,i)=>calls.length*10+i)),cancel:a=>calls.push({cancel:a}),redirect:(...a)=>(calls.push({redirect:a}),true)};
for(const s of ['windowAdded','windowClosed','windowDeleted','windowDataChanged','activeFullScreenEffectChanged','desktopChanged','screenLockingChanged','showingDesktopChanged','currentActivityChanged','virtualScreenGeometryChanged'])c.effects[s]=signal();
vm.createContext(c);vm.runInContext(fs.readFileSync(__dirname+'/kwin/effects/jarvis-energy/contents/code/main.js','utf8'),c);
const fx=c.energy;
const normal=()=>({visible:true,managed:true,normalWindow:true,windowClass:'preview',screen:{geometry:{x:-1920,y:100,width:1920,height:1040}},geometry:{x:-1600,y:200,width:560,height:300},expandedGeometry:{x:-1610,y:190,width:580,height:320},iconGeometry:{x:-1900,y:1100,width:40,height:40},minimizedChanged:signal(),windowFrameGeometryChanged:signal()});
function finish(w){let count=w.jarvisEnergy.remaining;while(count--)fx.ended(w,0);assert.equal(w.jarvisEnergy,undefined)}
// Recovery mode must never enter particle drag or open/close/focus tracks.
fx.motionOnly=true;
const limited=normal();const beforeRestricted=calls.length;
for(const kind of ['open','close'])fx.run(limited,kind);
fx.maximizeScan(limited);fx.beginMove(limited);fx.finishMove(limited);if(fx.dragCycle)fx.dragCycle(limited,true);
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

for(const panel of [{x:-1920,y:1100,width:1920,height:40},{x:-1920,y:100,width:1920,height:40},{x:-1920,y:100,width:40,height:1040},{x:-40,y:100,width:40,height:1040}]) {
 c.effects.stackingOrder=[{dock:true,geometry:panel}];
 const w=normal();w.iconGeometry={x:panel.x+4,y:panel.y+4,width:24,height:24};
 const target=fx.dockTarget(w);
 assert.equal(target.x,panel.x+panel.width/2);assert.equal(target.y,panel.y+panel.height/2);
}
const rapid=normal();fx.run(rapid,'minimize');fx.run(rapid,'restore');
assert.equal(calls.at(-1).redirect[1],E.Backward);finish(rapid);
reduced=true;fx.configure();const n=calls.length;fx.run(normal(),'minimize');assert.equal(calls.length,n);
reduced=false;factor=0;fx.configure();fx.run(normal(),'minimize');assert.equal(calls.length,n);
factor=.8;fx.configure();fx.geometryChanged(rapid,{x:-1500,y:200,width:280,height:150});
assert.equal(calls.at(-1).duration,336);finish(rapid);
assert.equal(fx.active.length,0);assert.ok(fx.slots.every(s=>!s.busy));
console.log('PASS: recovery-mode panel centres, mid-flight reversal, zero/reduced motion, maximize and cleanup.');

// The cinematic opt-in is off unless explicitly configured; when on, the
// recovery restriction lifts only for finite cinematic tracks and drag stays
// behind its own ParticleDragEnabled flag.
assert.equal(fx.cine,false);
{
 const cfg={WindowMotionOnly:true,CinematicTransitions:true};
 c.effect.readConfig=(k,d)=>k in cfg?cfg[k]:d;fx.configure();assert.equal(fx.cine,true);
 const w=normal();fx.run(w,'open');assert.equal(w.jarvisEnergy.mode,6);finish(w);
 fx.run(w,'minimize');assert.equal(w.jarvisEnergy.mode,7);finish(w);
 const n=calls.length;fx.beginMove(w);assert.equal(calls.length,n,'drag must stay opt-in');assert.equal(w.jarvisEnergy,undefined);
 cfg.CinematicTransitions='true';fx.configure();assert.equal(fx.cine,false,'only a real boolean enables cinematic mode');
 assert.equal(fx.active.length,0);assert.ok(fx.slots.every(s=>!s.busy));
}
console.log('PASS: cinematic transitions stay off without the explicit flag; drag remains separately gated.');
