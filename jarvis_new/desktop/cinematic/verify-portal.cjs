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

const w=normal();
const request={to:{x:500,y:150,w:900,h:700}};
const geo=fx.portalGeometry(w,request);
assert.ok(geo.source[0]+.001 >= w.expandedGeometry.width*.17+16, 'outward source half clipped');
assert.ok(geo.dest[0]+geo.dest[2]*1.06 < geo.canvas[0], 'destination gather clipped');
assert.equal(geo.monitor[2],1920);assert.equal(geo.monitor[3],1040);
assert.ok(geo.monitor[0]>=8 && geo.monitor[1]>=8,'source monitor clipped');
fx.preparePortal(w,request);
assert.ok(w.jarvisEnergy.staged);
assert.equal(calls.at(-1).animations[0].type,E.Opacity);
assert.equal(calls.at(-1).animations[0].to,0);
const origin=w.jarvisEnergy.origin;
// Simulate a delayed compositor move and a destination resized by its work area.
w.geometry={x:500,y:150,width:900,height:700};
w.expandedGeometry={x:490,y:140,width:920,height:720};
w.screen.geometry={x:400,y:0,width:1920,height:1080};
const countBeforeGeometry=calls.length;
fx.geometryChanged(w,{x:-1600,y:200,width:560,height:300});
assert.equal(calls.length,countBeforeGeometry,'staging triggered a second geometry animation');
const finalRequest={to:{x:490,y:140,w:920,h:720}};
const rebased=fx.portalGeometry(w,finalRequest,origin);
assert.equal(rebased.source[2],580,'lost original source dimensions');
assert.equal(rebased.monitor[2],1920);
// The transformed destination texture must fill the canvas in desktop coordinates.
const e=w.expandedGeometry,g=w.geometry,sx=rebased.scale.value1,sy=rebased.scale.value2;
const left=g.x+sx*(e.x-g.x)+(1-sx)*g.width/2+rebased.translation.value1;
const top=g.y+sy*(e.y-g.y)+(1-sy)*g.height/2+rebased.translation.value2;
assert.ok(Math.abs(left+rebased.dest[0]-e.x)<1e-6);
assert.ok(Math.abs(top+rebased.dest[1]-e.y)<1e-6);
assert.equal(rebased.dest[2],e.width);
assert.equal(rebased.dest[3],e.height);
fx.commitPortal(w);
assert.equal(w.jarvisEnergy.kind,'portal');
assert.equal(calls.at(-1).duration,7200);
assert.equal(calls.at(-1).animations.length,3);
finish(w);
assert.equal(fx.active.length,0);assert.ok(fx.slots.every(s=>!s.busy));
fx.preparePortal(w,finalRequest);finish(w); // abandoned stage expires and releases
fx.commitPortal(w);assert.equal(w.jarvisEnergy,undefined);
fx.reduced=true;const count=calls.length;fx.preparePortal(w,request);assert.equal(calls.length,count);
assert.equal(fx.portalGeometry(w,{to:{x:Infinity,y:0,w:900,h:700}}),null);
assert.equal(fx.portalGeometry(w,{to:{x:20000,y:0,w:900,h:700}}),null);
console.log('PASS: portal split/gather padding, geometry handoff without snap-back, finite track cleanup, reduced motion and allocation guards.');

// The other installed effect must yield too: otherwise it adds its own move.
c.effects.stackingOrder=[];
let cue={};
c.effect.readConfig=(key,fallback)=>key==='ActionRequest'?JSON.stringify(cue):fallback;
vm.runInContext(fs.readFileSync(__dirname+'/../ascii/kwin/effects/jarvis-ascii/contents/code/main.js','utf8'),c);
const aw=normal();aw.pid=123;aw.caption='Portal check';c.effects.stackingOrder=[aw];
for(const action of ['portal','portal-commit']) {
 cue={token:action,action,pid:123,caption:aw.caption,expires:Date.now()+8000};
 c.ascii.receive();
 assert.ok(aw.asciiPortalUntil>Date.now());
 const before=calls.length;
 c.ascii.geometry(aw,{x:0,y:0,width:800,height:600});
 c.ascii.scan(aw,true);
 assert.equal(calls.length,before,'ASCII added a competing transition');
}
cue={token:'cancel',action:'portal-cancel',pid:123,caption:aw.caption,expires:Date.now()+8000};
c.ascii.receive();assert.equal(aw.asciiPortalUntil,0);
console.log('PASS: ASCII yields geometry/focus to portal and cancel releases the guard.');
