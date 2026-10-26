// Regression for kwin_wayland SEGV in AnimationEffect::postPaintScreen:
// animate()/cancel() inside animationEnded mutated KWin's animation list mid-walk.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const signal=()=>({connect(){}});
let calls=[],inEnded=false,factor=1;
const E={Scale:1,Translation:2,ShaderUniform:3,Opacity:4,MapTexture:1,Modulate:4,WindowAddedGrabRole:10,WindowClosedGrabRole:11,WindowMinimizedGrabRole:12,WindowUnminimizedGrabRole:13,Forward:0,Backward:1,TerminateAtSource:1,TerminateAtTarget:2};
let sid=0;
const cfg={ParticleDragEnabled:true,WindowMotionOnly:true};
const c={print(){},console:{warn(){}},Effect:E,KWin:{MaximizeArea:1,ScreenArea:2},QEasingCurve:{Linear:0,OutCubic:1,InOutCubic:2},
 effects:{hasActiveFullScreenEffect:false,stackingOrder:[],cursorPos:{x:-900,y:400},clientArea:()=>({x:-1920,y:100,width:1920,height:1040})},
 effect:{readConfig:(k,d)=>k in cfg?cfg[k]:d,grab:()=>true,isGrabbed:()=>false,addFragmentShader:()=>++sid,setUniform(){},configChanged:signal(),animationEnded:signal()},
 animationTime:n=>n*factor,
 animate:a=>{assert.equal(inEnded,false,'animate() called during animationEnded');calls.push(a);return a.animations.map((_,i)=>calls.length*10+i)},
 cancel:a=>{assert.equal(inEnded,false,'cancel() called during animationEnded');calls.push({cancel:a})},set(){throw new Error('set forbidden')}};
for(const s of ['windowAdded','windowClosed','windowDeleted','windowDataChanged','activeFullScreenEffectChanged','desktopChanged','screenLockingChanged','showingDesktopChanged','currentActivityChanged','virtualScreenGeometryChanged'])c.effects[s]=signal();
vm.createContext(c);vm.runInContext(fs.readFileSync(__dirname+'/kwin/effects/jarvis-energy/contents/code/main.js','utf8'),c);
const fx=c.energy;
const mk=()=>({visible:true,managed:true,normalWindow:true,deleted:false,move:true,windowClass:'preview',screen:{geometry:{x:-1920,y:100,width:1920,height:1040}},geometry:{x:-1600,y:200,width:560,height:300},expandedGeometry:{x:-1610,y:190,width:580,height:320},iconGeometry:{x:-1900,y:1100,width:40,height:40},minimizedChanged:signal(),windowFrameGeometryChanged:signal()});
const end=w=>{const n=w.jarvisEnergy.remaining;inEnded=true;try{for(let i=0;i<n;i++)fx.ended(w,0)}finally{inEnded=false}};
// 1. The drag track outlives any real drag and completion appends nothing.
const w=mk();fx.beginMove(w);const s=w.jarvisEnergy;assert.equal(s.mode,4);assert.ok(s.clockDuration>=300000,'drag track must be long-lived');
const before=calls.length;end(w);assert.equal(calls.length,before,'completion appended a track');assert.equal(w.jarvisEnergy,undefined);
// 2. Renewal is deferred and happens only from a later geometry/pointer event.
fx.pointerChanged();assert.equal(w.jarvisEnergy&&w.jarvisEnergy.mode,4,'renewed on next event');assert.equal(fx.renewals.length,0);
// 3. Never animate a dead window, nor renew one after the drag ended.
const d=mk();fx.beginMove(d);end(d);d.deleted=true;const n1=calls.length;fx.pointerChanged();fx.geometryChanged(d,{...d.geometry});fx.beginMove(d);assert.equal(calls.length,n1,'dead window animated');
const f=mk();fx.beginMove(f);end(f);f.move=false;fx.finishMove(f);fx.pointerChanged();assert.equal(f.jarvisEnergy,undefined,'stale renewal after drag end');
console.log('PASS: drag track is long-lived, completion never animates/cancels, renewals deferred, dead windows ignored.');
