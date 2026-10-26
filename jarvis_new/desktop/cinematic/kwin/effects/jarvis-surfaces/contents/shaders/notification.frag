#version 140
// SPDX-License-Identifier: MIT
uniform sampler2D sampler;
uniform vec4 modulation;
uniform vec2 canvasSize;
uniform vec4 sourceRect;
uniform float phase;
uniform float edgeSign;
uniform float closing;
uniform vec2 launchSize;
uniform vec2 clickOrigin;
in vec2 texcoord0;
out vec4 fragColor;
#include "colormanagement.glsl"
float line(float d,float width) {return 1.0-smoothstep(width,width+1.0,abs(d));}
void main() {
    float p=clamp(phase,0.0,1.0);
    vec2 xy=vec2(texcoord0.x,1.0-texcoord0.y)*canvasSize;
    vec2 local=xy-sourceRect.xy;
    vec2 size=sourceRect.zw;
    vec2 edge=min(local,size-local);
    float inside=step(0.0,edge.x)*step(0.0,edge.y);
    float envelope=sin(p*3.14159265);
    float row=floor(local.y/12.0);
    float seed=fract(sin(row*17.13+3.7)*4375.58);
    float across=edgeSign>0.0?size.x-local.x:local.x;
    float progress=smoothstep(0.02,0.62,p);
    float front=progress*(size.x+20.0)-10.0;
    float reveal=smoothstep(-8.0,8.0,front-across);
    float release=smoothstep(0.16+seed*0.18,0.72+seed*0.24,p);
    float launch=step(1.5,closing);
    float opacity=closing>0.5?1.0-release:reveal;
    float fault=(1.0-smoothstep(0.20,0.48,p))*smoothstep(0.02,0.08,p);
    if(closing>0.5)fault=envelope*(1.0-launch);
    float offset=(seed-0.5)*24.0*step(0.68,seed)*fault;
    vec2 uv=texcoord0+vec2(offset/canvasSize.x,0.0);
    float valid=step(0.0,uv.x)*step(uv.x,1.0);
    vec4 color=texture(sampler,clamp(uv,0.0,1.0))*opacity*valid;
    float border=line(min(edge.x,edge.y)-3.0,0.7);
    float corners=border*step(min(edge.x,edge.y),5.0)*(1.0-step(30.0,max(edge.x,edge.y)));
    float rail=line(edge.y-7.0,0.6)*step(0.55,fract(local.x/28.0-p*2.0));
    float beam=exp(-abs(across-front)/7.0)*(1.0-smoothstep(0.62,0.85,p));
    float scan=line(mod(local.y,5.0)-2.5,0.25)*0.10;
    float fragments=line(mod(local.y,12.0)-1.0,0.4)*step(0.66,seed)*step(0.45,fract(local.x/43.0+seed));
    float accent=(border*0.36+corners*0.58+rail*0.55+scan+fragments*0.4)*envelope;
    if(closing<0.5)accent+=beam*0.72*envelope;
    else accent+=fragments*0.35*envelope;
    accent=clamp(accent*inside,0.0,0.85);
    color.rgb=mix(color.rgb,vec3(0.04,0.36,1.0)*color.a,envelope*0.12);
    vec3 blue=mix(vec3(0.04,0.38,1.0),vec3(0.50,0.88,1.0),accent);
    color=vec4(blue*accent,accent)+color*(1.0-accent);
    if(launch>0.5) {
        // Match the compositor's 440 / 780 ms InOutCubic expansion. Work in
        // displayed pixels so the outline stays fine as the card fills output.
        float travel=clamp(p*780.0/440.0,0.0,1.0);
        float eased=travel<0.5?4.0*travel*travel*travel:
            1.0-pow(-2.0*travel+2.0,3.0)/2.0;
        vec2 displaySize=mix(size,max(launchSize,size),eased);
        vec2 normalized=local/max(size,vec2(1.0));
        vec2 pixels=normalized*displaySize;
        vec2 pixelEdge=min(pixels,displaySize-pixels);
        float boundary=min(pixelEdge.x,pixelEdge.y);
        float stroke=exp(-abs(boundary-1.3)/1.1);
        float glow=exp(-max(boundary,0.0)/14.0)*0.16;
        // Discard the readable texture before it can become giant, stretched
        // lettering. A translucent glass gradient carries the expansion.
        float textAlpha=1.0-smoothstep(0.015,0.19,p);
        vec4 source=texture(sampler,texcoord0)*textAlpha;
        float wash=smoothstep(0.0,0.13,p)*0.64;
        vec3 glass=mix(vec3(0.018,0.045,0.10),vec3(0.035,0.16,0.30),
            clamp(0.28+normalized.x*0.25+(1.0-normalized.y)*0.4,0.0,1.0));
        float sheen=exp(-pow((normalized.x+normalized.y*0.22-p*1.8)/0.10,2.0))*0.055*envelope;
        vec4 panel=vec4((glass+vec3(0.05,0.22,0.38)*sheen)*wash,wash);
        panel=source+panel*(1.0-source.a);
        // Once full-size, open a circular reveal from the actual click point.
        // The last corners clear before the compositor releases its texture.
        float revealPhase=smoothstep(0.64,1.0,p);
        vec2 origin=clamp(clickOrigin,vec2(0.0),vec2(1.0))*displaySize;
        float farthest=length(max(origin,displaySize-origin));
        float distance=length(pixels-origin);
        float radius=revealPhase*(farthest+36.0)-18.0;
        float cover=smoothstep(radius-14.0,radius+14.0,distance);
        float rim=exp(-abs(distance-radius)/4.0)*sin(revealPhase*3.14159265)*0.42;
        float edgeLight=(stroke*0.45+glow)*envelope*cover;
        float light=clamp(edgeLight+rim,0.0,0.65)*inside;
        panel*=inside*cover;
        color=vec4(vec3(0.13,0.58,1.0)*light,light)+panel*(1.0-light);
    }
    // Exact endpoints avoid a visible jump when KWin releases the texture.
    if(p<=0.0)color=closing>0.5?texture(sampler,texcoord0):vec4(0.0);
    if(p>=1.0)color=closing>0.5?vec4(0.0):texture(sampler,texcoord0);
    color=encodingToNits(color,sourceNamedTransferFunction,sourceTransferFunctionParams.x,sourceTransferFunctionParams.y);
    color.rgb=(colorimetryTransform*vec4(color.rgb,1.0)).rgb;
    color*=modulation;
    color.rgb=doTonemapping(color.rgb);
    fragColor=nitsToDestinationEncoding(color);
}
