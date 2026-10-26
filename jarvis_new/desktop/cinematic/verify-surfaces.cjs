const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const signal=()=>({handlers:[],connect(fn){this.handlers.push(fn)},emit(...args){for(const fn of this.handlers)fn(...args)}});let calls=[],cancelled=[],factor=1,reduced=false,grab=true,request='{}',retargeted=[];
const Effect={WindowAddedGrabRole:1,WindowClosedGrabRole:2,Opacity:3,Scale:4,Translation:5,ShaderUniform:6,MapTexture:8,Modulate:16,Forward:1,Backward:2,TerminateAtSource:1,TerminateAtTarget:2};
const c={Effect,QEasingCurve:{OutCubic:1,OutQuart:2,InCubic:3,Linear:4,InOutCubic:5},console,effects:{hasActiveFullScreenEffect:false,stackingOrder:[],cursorPos:{x:100,y:60}},effect:{complete:()=>true,addFragmentShader:()=>0,grab:()=>grab,readConfig:(key)=>key==='ClickRequest'?request:reduced,isGrabbed:()=>true,animationEnded:signal(),configChanged:signal()},animationTime:n=>factor*n,animate:a=>{calls.push(a);return a.animations.map((_,i)=>i+1)},redirect:()=>true,retarget:(id,to,duration)=>{retargeted.push({id,to,duration});return true},cancel:a=>cancelled.push(a)};
for(const name of ['windowAdded','windowClosed','windowDeleted','windowDataChanged','screenLockingChanged','activeFullScreenEffectChanged','desktopChanged','currentActivityChanged','virtualScreenGeometryChanged'])c.effects[name]=signal();
vm.createContext(c);vm.runInContext(fs.readFileSync(__dirname+'/kwin/effects/jarvis-surfaces/contents/code/main.js','utf8'),c);
const elevated=new Set(),elevationCalls=[];
c.effects.setElevatedWindow=(w,set)=>{elevationCalls.push({w,set});if(set)elevated.add(w);else elevated.delete(w)};
const f=c.surfaces,base=()=>({visible:true,popupWindow:true,windowClass:'test',geometry:{x:40,y:40,width:150,height:80},screen:{geometry:{x:0,y:0,width:1920,height:1080}}});
for(const flags of [{popupWindow:true},{notification:true},{appletPopup:true},{onScreenDisplay:true}]) {
 const w={...base(),popupWindow:false,...flags};f.run(w,true);assert.equal(w.jarvisSurface.remaining,3);f.run(w,false);assert.equal(calls.at(-1).keepAlive,true);f.stop(w);assert.equal(f.active.length,0);
}
for(const key of ['lockScreen','desktopWindow','dock','splash','outline','fullScreen','criticalNotification']) {const n=calls.length;f.run({...base(),[key]:true},true);assert.equal(calls.length,n,key);}
for(const windowClass of ['pinentry','polkit-kde','kscreenlocker','jarvis-watch','spectacle']) {const n=calls.length;f.run({...base(),windowClass},true);assert.equal(calls.length,n);}
for(const disabled of ['reduced','zero','grab','locked','fullscreen']) {
 reduced=disabled==='reduced';factor=disabled==='zero'?0:1;grab=disabled!=='grab';f.locked=disabled==='locked';c.effects.hasActiveFullScreenEffect=disabled==='fullscreen';
 const n=calls.length;f.run(base(),true);assert.equal(calls.length,n,disabled);
}
f.locked=false;c.effects.hasActiveFullScreenEffect=false;grab=true;reduced=false;factor=1;
f.run(base(),true);f.run(base(),true);f.stopAll();assert.equal(f.active.length,0);
// Each monitor chooses its own nearest edge, including negative coordinates.
for(const [screenX,x,expectedSign] of [[0,40,-1],[0,1650,1],[-1920,-1880,-1],[-1920,-270,1]]) {
 const w={...base(),notification:true,geometry:{x,y:40,width:240,height:80},screen:{geometry:{x:screenX,y:0,width:1920,height:1080}}};
 f.run(w,true);
 const arrival=calls.at(-1);
 assert.equal(arrival.duration,900);
 assert.equal(arrival.curve,c.QEasingCurve.OutQuart);
 assert.equal(arrival.animations[0].duration,260);
 assert.equal(Math.sign(arrival.animations[2].from.value1),expectedSign);
 assert.equal(arrival.animations[2].to.value1,0);
 // Finishing the fast fade must not discard the slower movement's tracking.
 c.effect.animationEnded.emit(w);assert.equal(w.jarvisSurface.remaining,2);
 c.effect.animationEnded.emit(w);assert.ok(w.jarvisSurface);
 c.effect.animationEnded.emit(w);assert.equal(w.jarvisSurface,undefined);
 f.run(w,false);
 const departure=calls.at(-1);
 assert.equal(departure.duration,620);
 assert.equal(departure.curve,c.QEasingCurve.InCubic);
 assert.equal(departure.keepAlive,true);
 assert.equal(Math.sign(departure.animations[2].to.value1),expectedSign);
 c.effects.windowDeleted.emit(w);assert.equal(f.active.length,0);
}
factor=0.8;const scaled={...base(),notification:true};f.run(scaled,true);
assert.equal(calls.at(-1).duration,720);assert.equal(calls.at(-1).animations[0].duration,208);
c.effects.windowDataChanged.emit(scaled,Effect.WindowAddedGrabRole);
assert.equal(scaled.jarvisSurface,undefined);assert.equal(f.active.length,0);
factor=1;f.run(base(),true);c.effects.screenLockingChanged.emit(true);
assert.equal(f.active.length,0);
f.locked=false;
f.slots=Array.from({length:6},(_,i)=>({id:i+1,busy:false}));
c.effect.setUniform=()=>{};
const stack=Array.from({length:7},()=>({...base(),notification:true}));
for(const w of stack)f.run(w,true);
assert.equal(f.slots.filter(s=>s.busy).length,6);
assert.equal(stack[0].jarvisSurface.remaining,4);
assert.equal(stack[6].jarvisSurface.remaining,3); // Pool contention keeps native motion.
for(let i=0;i<3;i++)c.effect.animationEnded.emit(stack[0]);
assert.ok(stack[0].jarvisSurface);assert.equal(f.slots[0].busy,true);
c.effect.animationEnded.emit(stack[0]);assert.equal(f.slots[0].busy,false);
f.run(stack[1],false);assert.equal(stack[1].jarvisSurface.remaining,4);
c.effect.configChanged.emit();assert.equal(f.active.length,0);
assert.ok(f.slots.every(s=>!s.busy));
c.effect.setUniform=()=>{throw Error('simulated shader failure')};
f.run({...base(),notification:true},true);assert.equal(calls.at(-1).animations.length,3);
f.stopAll();assert.ok(f.slots.every(s=>!s.busy));
console.log('PASS: popup/notification lifecycles, takeover, cancellation, protected surfaces and reduced/zero motion.');

// Explicit default-action requests turn only the clicked card into a launch.
c.effect.setUniform=()=>{};
const clicked={...base(),notification:true};
c.effects.stackingOrder=[clicked];
f.closed(clicked);
request=JSON.stringify({token:'first',time:Date.now()});
c.effect.configChanged.emit();
assert.equal(clicked.jarvisSurface.launch,true);
assert.ok(elevated.has(clicked)); // Retargeting an already closing card also raises it.
assert.equal(retargeted.at(-1).duration,780);
assert.equal(retargeted.find(t=>t.id===2).to.value1,1920/150);
assert.ok(retargeted.find(t=>t.id===3).to.value1>0); // Inward from left edge.
const launched=calls.length;
c.effects.windowClosed.emit(clicked);assert.equal(calls.length,launched);
assert.equal(calls.at(-1).keepAlive,true);assert.equal(clicked.jarvisSurface.launch,true);
f.stopAll();
// Clicking during arrival starts a fresh shader and retains it on close.
f.run(clicked,true);
request=JSON.stringify({token:'arrival',time:Date.now()});
c.effect.configChanged.emit();
assert.equal(calls.at(-1).duration,780);
assert.equal(calls.at(-1).keepAlive,true);
assert.equal(clicked.jarvisSurface.launch,true);
f.stopAll();
// DBus can arrive after windowClosed: retain then replace its closing texture.
f.closed(clicked);c.effects.stackingOrder=[];
request=JSON.stringify({token:'late',time:Date.now()});
c.effect.configChanged.emit();assert.equal(clicked.jarvisSurface.launch,true);
f.stopAll();
// A click outside a card, a stale event, and reduced motion cannot launch.
for(const mode of ['outside','stale','reduced','zero','locked']) {
 c.effects.stackingOrder=[clicked];c.effects.cursorPos={x:mode==='outside'?1000:100,y:60};
 reduced=mode==='reduced';factor=mode==='zero'?0:1;f.locked=mode==='locked';
 request=JSON.stringify({token:mode,time:Date.now()-(mode==='stale'?1000:0)});
 const before=calls.length;c.effect.configChanged.emit();assert.equal(calls.length,before,mode);
}
reduced=false;factor=1;f.locked=false;
f.stopAll();assert.ok(f.slots.every(s=>!s.busy));
console.log('PASS: click launch, close/action ordering, cursor targeting, stale requests and motion gates.');

// Full-screen target geometry is local to the notification's output.
for(const screen of [{x:0,y:0,width:1920,height:1080},{x:-2560,y:-300,width:2560,height:1440}]) {
 const w={...base(),notification:true,screen:{geometry:screen},geometry:{x:screen.x+40,y:screen.y+60,width:360,height:120}};
 f.run(w,false,true);const a=calls.at(-1).animations,g=w.geometry;
 const scale=a[1].to,move=a[2].to;
 assert.equal(g.width*scale.value1,screen.width);
 assert.equal(g.height*scale.value2,screen.height);
 assert.equal(g.x+g.width/2+move.value1-screen.width/2,screen.x);
 assert.equal(g.y+g.height/2+move.value2-screen.height/2,screen.y);
 assert.equal(a[1].duration,440);assert.ok(w.jarvisSurface.held);
 // The two finite visibility tracks finish, then held geometry terminates.
 for(let i=0;i<4;i++)c.effect.animationEnded.emit(w);
 assert.equal(w.jarvisSurface,undefined);
}
console.log('PASS: full-output expansion, negative monitor coordinates, held geometry and final cleanup.');

// A lower card's expansion paints above both settled and arriving siblings.
for(const withShader of [true,false]) {
 const slots=f.slots;if(!withShader)f.slots=[];
 for(const finish of ['complete','stop','delete','configure','lock','takeover','fullscreen']) {
  const lower={...base(),notification:true},upper={...base(),notification:true};
  c.effects.stackingOrder=[lower,upper];
  f.run(upper,true);
  assert.equal(elevated.size,0,'Ordinary arrival keeps its stack position');
  f.run(lower,false,true);
  assert.deepEqual([...elevated],[lower]);
  // Another arrival cannot take elevation away from the launch.
  f.run(upper,true);assert.deepEqual([...elevated],[lower]);
  const raises=elevationCalls.length;
  c.effects.windowClosed.emit(lower);
  assert.equal(elevationCalls.length,raises,'Closing does not lower an active launch');
  if(finish==='complete') {
   const count=lower.jarvisSurface.remaining;
   for(let i=1;i<count;i++){c.effect.animationEnded.emit(lower);assert.ok(elevated.has(lower));}
   c.effect.animationEnded.emit(lower);
  } else if(finish==='stop')f.stop(lower);
  else if(finish==='delete')c.effects.windowDeleted.emit(lower);
  else if(finish==='configure'){request='{}';c.effect.configChanged.emit();}
  else if(finish==='lock'){c.effects.screenLockingChanged.emit(true);f.locked=false;}
  else if(finish==='takeover')c.effects.windowDataChanged.emit(lower,Effect.WindowClosedGrabRole);
  else c.effects.activeFullScreenEffectChanged.emit();
  assert.equal(elevated.size,0,finish+' restores painting order');
  f.stopAll();
 }
 f.slots=slots;
}
console.log('PASS: stacked launch elevation, shader fallback and completion/cancellation cleanup.');
