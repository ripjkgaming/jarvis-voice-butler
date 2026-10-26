#version 140
// SPDX-License-Identifier: MIT
// One texture lookup; no noise, loops, extra buffers or persistent rendering.
uniform sampler2D sampler;
uniform vec4 modulation;
uniform float reveal;
in vec2 texcoord0;
out vec4 fragColor;
#include "colormanagement.glsl"

void main()
{
    vec4 color = texture(sampler, texcoord0);
    float p = clamp(reveal, 0.0, 1.0);
    float distanceFromCenter = abs(texcoord0.y - 0.5) * 2.0;
    float aperture = p * 1.05;
    float mask = 1.0 - smoothstep(aperture - 0.025, aperture, distanceFromCenter);
    float edge = 1.0 - smoothstep(0.0, 0.022, abs(distanceFromCenter - (aperture - 0.022)));
    float accent = smoothstep(0.0, 0.12, p) * (1.0 - smoothstep(0.75, 1.0, p));
    // Premultiplied cyan scan edge never paints outside the window's alpha.
    color.rgb = mix(color.rgb, vec3(0.06, 0.65, 1.0) * color.a,
                    accent * (0.10 + edge * 0.72));
    color *= mask * smoothstep(0.0, 0.07, p);
    // Follow KWin 6's color pipeline, including HDR/ICC output conversion.
    color = encodingToNits(color, sourceNamedTransferFunction, sourceTransferFunctionParams.x, sourceTransferFunctionParams.y);
    color.rgb = (colorimetryTransform * vec4(color.rgb, 1.0)).rgb;
    color *= modulation;
    color.rgb = doTonemapping(color.rgb);
    fragColor = nitsToDestinationEncoding(color);
}
