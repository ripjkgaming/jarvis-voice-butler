const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const sig=()=>({callbacks:[],connect(f){this.callbacks.push(f)},emit(...a){this.callbacks.forEach(f=>f(...a))}});
let list=[],calls=[],now=0;
const workspace={windowList:()=>list};for(const k of ['windowAdded','windowRemoved','currentDesktopChanged','currentActivityChanged','screensChanged'])workspace[k]=sig();
const c={workspace,Date:{now:()=>now+=100},callDBus:(...a)=>calls.push(a)};vm.createContext(c);vm.runInContext(fs.readFileSync(__dirname+'/taskbar-puppet.js','utf8'),c);
const panel={dock:true,frameGeometry:{x:0,y:1024,width:1920,height:56},damaged:sig()};list.push(panel);workspace.windowAdded.emit(panel);
function window(id){return {normalWindow:true,desktopFileName:id,output:{geometry:{x:0,y:0,width:1920,height:1080}},iconGeometry:{x:0,y:0,width:0,height:0},activeChanged:sig(),minimizedChanged:sig(),iconGeometryChanged:sig()};}
const w=window('owned');list.push(w);workspace.windowAdded.emit(w);assert.equal(calls.length,0);
w.iconGeometry={x:400,y:1040,width:48,height:40};panel.damaged.emit();assert.equal(calls.filter(x=>x[3]==='Play').length,1);
const two=window('owned');list.push(two);workspace.windowAdded.emit(two);panel.damaged.emit();assert.equal(calls.filter(x=>x[3]==='Play').length,1);
w.iconGeometry.x+=48;panel.damaged.emit();assert.equal(calls.at(-1)[3],'Cancel');
const secure=window('jarvis-watch');list.push(secure);workspace.windowAdded.emit(secure);secure.iconGeometry=w.iconGeometry;panel.damaged.emit();assert.equal(calls.filter(x=>x[3]==='Play').length,1);
list=list.filter(x=>x!==w);workspace.windowRemoved.emit(w);list=list.filter(x=>x!==two);workspace.windowRemoved.emit(two);
const again=window('owned');again.iconGeometry={x:400,y:1040,width:48,height:40};list.push(again);workspace.windowAdded.emit(again);assert.equal(calls.filter(x=>x[3]==='Play').length,2);
workspace.currentDesktopChanged.emit();assert.equal(calls.at(-1)[3],'Cancel');
console.log('PASS: delayed real icon geometry, first-task-only grouping, re-open, protected exclusion, moving-slot and desktop cancellation.');
