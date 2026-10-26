#version 140
// SPDX-License-Identifier: MIT
// Jarvis ASCII: the window resolves out of (or dissolves into) a cyan glyph
// mosaic sampled from its own pixels. One shader instance per motion mode;
// only `phase` is animated per window.
uniform sampler2D sampler;
uniform vec4 modulation;
uniform float phase;   // 0 = absent, 1 = fully present
uniform float mode;    // 0 window, 1 dock, 2 scan, 3 notify, 4 popup, 5 desktop, 6 geometry
uniform float strong;  // 1 for Jarvis-commanded actions: bigger glyphs, slower read
in vec2 texcoord0;
out vec4 fragColor;
#include "colormanagement.glsl"

const vec3 CY = vec3(0.37, 0.89, 1.0);
const vec3 ICE = vec3(0.85, 0.97, 1.0);
const vec3 INK = vec3(0.008, 0.035, 0.06);

float hash(vec2 p)
{
    return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453);
}

// 5x5 bitmaps packed into 25 bits, centred in a 7x7 cell.
float glyph(int n, vec2 uv)
{
    vec2 g = floor(uv * 7.0) - 1.0;
    if (g.x < 0.0 || g.y < 0.0 || g.x > 4.0 || g.y > 4.0)
        return 0.0;
    int a = int(4.0 - g.x) + 5 * int(4.0 - g.y);
    return float((n >> a) & 1);
}

int ramp(float l)
{
    if (l > 0.82) return 11512810; // #
    if (l > 0.70) return 13199452; // @
    if (l > 0.60) return 15252014; // 8
    if (l > 0.50) return 23385164; // &
    if (l > 0.40) return 15255086; // o
    if (l > 0.28) return 332772;   // *
    if (l > 0.16) return 65600;    // :
    return 4096;                   // .
}

void main()
{
    vec2 size = max(vec2(textureSize(sampler, 0)), vec2(1.0));
    vec2 px = texcoord0 * size;
    float small = step(min(size.x, size.y), 420.0);
    vec2 cell = mix(vec2(9.0, 15.0), vec2(6.0, 10.0), small) * mix(1.0, 1.4, strong);
    vec2 id = floor(px / cell);
    vec2 local = fract(px / cell);
    vec2 cuv = min((id + 0.5) * cell / size, vec2(1.0));
    float n = hash(id);
    float p = clamp(phase, 0.0, 1.0);
    int m = int(mode + 0.5);

    vec4 tex = texture(sampler, texcoord0);
    vec4 base = texture(sampler, cuv);
    float lum = dot(base.rgb / max(base.a, 1e-3), vec3(0.299, 0.587, 0.114));

    float real = 1.0;
    float asc = 0.0;
    float lp = 1.0;
    if (m == 2) {
        // Focus scan: a glyph band reads down the window once.
        // Starts on-screen and peaks early so short switches still read.
        float b = p * 1.2 - 0.1;
        float d = abs(cuv.y - b);
        asc = (1.0 - smoothstep(0.0, 0.11, d)) * smoothstep(0.0, 0.06, p) * pow(1.0 - p, 0.7) * 0.9;
        lp = d * 4.0;
    } else if (m == 6) {
        // Geometry: the moved window is re-read from glyphs into pixels.
        asc = (1.0 - smoothstep(0.15 + n * 0.3, 0.75 + n * 0.25, p)) * 0.95;
        lp = p;
    } else {
        float order;
        vec2 c = cuv - 0.5;
        float aspect = size.x / size.y;
        if (m == 0) order = length(c * vec2(aspect, 1.0)) / length(vec2(aspect, 1.0) * 0.5);
        else if (m == 1) order = 1.0 - cuv.y;
        else if (m == 3) order = cuv.x;
        else if (m == 4) order = cuv.y;
        else order = n;
        order = clamp(order * 0.78 + n * 0.22, 0.0, 1.0);
        float w = 0.32;
        lp = clamp((p * (1.0 + w) - order) / w, 0.0, 1.0);
        real = smoothstep(0.55, 1.0, lp);
        asc = smoothstep(0.0, 0.1, lp) * (1.0 - real);
    }

    vec4 outc = tex * real;
    if (asc > 0.001) {
        float tick = floor(p * 26.0);
        bool scramble = hash(id + tick) > 0.6 && lp < 0.6;
        int code = scramble ? ramp(hash(id * 1.7 + tick)) : ramp(clamp(lum * 0.9 + 0.12, 0.0, 1.0));
        float g = glyph(code, local);
        vec3 ink = mix(CY, ICE, lum);
        float flash = 1.0 - smoothstep(0.0, 0.3, lp);
        ink = mix(ink, vec3(1.0), flash * 0.75);
        float scan = 0.8 + 0.2 * step(0.5, fract(px.y * 0.5));
        vec3 rgb = g * ink * scan + (1.0 - g) * mix(INK, CY * 0.25, flash * 0.5);
        float a = mix(0.74, 1.0, g) * smoothstep(0.0, 0.5, base.a);
        vec4 glyphs = vec4(rgb * a, a);
        if (m == 2 || m == 6)
            outc = mix(tex, glyphs * tex.a, asc);
        else
            outc += glyphs * asc;
    }

    outc = encodingToNits(outc, sourceNamedTransferFunction, sourceTransferFunctionParams.x, sourceTransferFunctionParams.y);
    outc.rgb = (colorimetryTransform * vec4(outc.rgb, 1.0)).rgb;
    outc *= modulation;
    outc.rgb = doTonemapping(outc.rgb);
    fragColor = nitsToDestinationEncoding(outc);
}
