from pathlib import Path
root=Path('/mnt/data/jarvis-voice-butler/jarvis_new/desktop/cinematic')
f=root/'kwin/effects/jarvis-energy/contents/shaders/energy.frag'
s=f.read_text()
s=s.replace('float split=smoothstep(0.0,0.19,p);','float split=smoothstep(0.035,0.20,p);')
s=s.replace('float halfFade=1.0-smoothstep(0.17,0.36,p);', '''// Stagger each source tile into a mote, retaining the two halves.
        vec2 tile=floor(oldUV*vec2(32.,18.));
        float tileSeed=fract(sin(dot(tile,vec2(127.1,311.7)))*43758.5453);
        float halfFade=1.0-smoothstep(.20+tileSeed*.06,.30+tileSeed*.06,p);''')
s=s.replace('float particles=smoothstep(.19,.32,p)*(1.0-smoothstep(.66,.80,p));','float particles=smoothstep(.22,.34,p)*(1.0-smoothstep(.65,.74,p));')
s=s.replace('vec3 electric=mix(sampled,vec3(.11,.65,1.),.54);','vec3 electric=mix(vec3(.035,.40,1.),vec3(.25,.85,1.),dot(sampled,vec3(.2126,.7152,.0722)));')
s=s.replace('float trace=smoothstep(.58,.66,p)*(1.0-smoothstep(.87,.98,p));','float trace=smoothstep(.60,.68,p)*(1.0-smoothstep(.84,.94,p));')
s=s.replace('float edgeAlpha=(ink(edgeDist-1.0)*.39+glow(edgeDist,15.)*.24)*\n                         max(visited*.85,front)*trace;', '''// Hundreds of moving luminous beads follow the window perimeter.
        // Confine the rail to the finite rectangle (never infinite lines).
        float perimeter=2.*(portalTo.z+portalTo.w);
        float along=route*perimeter;
        float bead=abs(fract(along/6.-p*28.)-.5)*6.;
        float sparkle=pow(max(0.,1.-bead/2.4),3.);
        float bounds=step(-.025,d.x)*step(d.x,1.025)*step(-.025,d.y)*step(d.y,1.025);
        float edgeAlpha=(ink(edgeDist-.6)*(.12+.65*sparkle)+glow(edgeDist,12.)*(.12+.3*sparkle))*
                         max(visited*.65,front)*trace*bounds;''')
needle='''        float built=smoothstep(.77,.985,p);'''
replacement='''        // The perimeter releases a contracting grid of 576 blue motes.
        // Each one lands on its matching image tile as the content returns.
        float gather=smoothstep(.79,.98,p);
        vec2 gatherScale=vec2(mix(1.12,1.,gather));
        vec2 homeUV=(rebuilt-.5)/gatherScale+.5;
        vec2 grid=vec2(32.,18.);
        vec2 cell=floor(homeUV*grid);
        float gatherMotes=0.;
        for(int gy=-1;gy<=1;gy++)for(int gx=-1;gx<=1;gx++) {
            vec2 id=cell+vec2(float(gx),float(gy));
            if(any(lessThan(id,vec2(0))) || any(greaterThanEqual(id,grid)))continue;
            vec2 home=(id+.5)/grid;
            vec2 delta=(homeUV-home)*portalTo.zw*gatherScale;
            gatherMotes=max(gatherMotes,glow(length(delta),3.8)+.18*glow(length(delta),10.));
        }
        float gatherAlpha=min(1.,gatherMotes)*smoothstep(.77,.83,p)*(1.-smoothstep(.94,1.,p));
        vec4 gathered=vec4(vec3(.09,.68,1.)*gatherAlpha,gatherAlpha);
        color=gathered+color*(1.-gathered.a);
        float built=smoothstep(.81,.99,p);'''
assert needle in s
s=s.replace(needle,replacement)
f.write_text(s)
f=root/'kwin/effects/jarvis-energy/contents/code/main.js'
s=f.read_text()
s=s.replace('var left=Math.min(e.x,to.x)-4,top=Math.min(e.y,to.y)-4;\n        var right=Math.max(e.x+e.width,to.x+to.w)+4;\n        var bottom=Math.max(e.y+e.height,to.y+to.h)+4;', '''// Include outward halves, particle glow and the destination gather.
        var splitPad=e.width*.17+16;
        var left=Math.min(e.x-splitPad,to.x-to.w*.06-16);
        var top=Math.min(e.y-16,to.y-to.h*.06-16);
        var right=Math.max(e.x+e.width+splitPad,to.x+to.w*1.06+16);
        var bottom=Math.max(e.y+e.height+16,to.y+to.h*1.06+16);''')
f.write_text(s)
