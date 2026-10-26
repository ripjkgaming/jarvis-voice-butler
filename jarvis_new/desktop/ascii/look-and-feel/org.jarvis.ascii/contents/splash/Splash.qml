/*
 Jarvis ASCII startup splash. KSplash advances `stage` through Plasma's real
 startup steps; the arc reactor assembles ring by ring with them, the boot
 log prints each step as it lands, and the title decodes out of glyphs.
 SPDX-License-Identifier: MIT
 */
import QtQuick

Rectangle {
    id: root
    color: "#02090f"
    property int stage: 0
    property real t: 0
    property real stageAt: 0
    // reactor build tracks the stage, but never jumps: it eases toward it
    property real build: 0
    readonly property real target: [0, 0.6, 1.0, 1.45, 1.8, 2.2, 3.0][Math.min(stage, 6)]
    readonly property string ramp: " .·:-=+*#%▓█"
    readonly property string noise: "▓▒░#%&@$01<>/\\=+*"
    readonly property var steps: [
        ["KERNEL", "session started"],
        ["KCMINIT", "hardware settings applied"],
        ["KSMSERVER", "session manager online"],
        ["KWIN", "compositor + ascii transitions"],
        ["PLASMA", "desktop shell loaded"],
        ["J.A.R.V.I.S", "all systems online"]
    ]
    readonly property string mono: "monospace"

    onStageChanged: stageAt = t

    Timer {
        interval: 33; running: root.visible; repeat: true
        onTriggered: {
            root.t += 0.033;
            root.build += (root.target - root.build) * 0.06 + (root.build < root.target ? 0.012 : 0);
            field.requestPaint();
        }
    }

    function hash(x, y) { var n = Math.sin(x * 127.1 + y * 311.7) * 43758.5453; return n - Math.floor(n); }
    function frac(x) { return x - Math.floor(x); }
    function sstep(a, b, x) { var u = Math.max(0, Math.min(1, (x - a) / (b - a))); return u * u * (3 - 2 * u); }
    function ringf(r, c, w) { var d = (r - c) / w; return (d > -3 && d < 3) ? Math.exp(-d * d) : 0; }
    function scramble(s, p, seed) {
        var n = Math.floor(s.length * Math.max(0, Math.min(1, p))), out = s.substring(0, n);
        for (var i = n; i < s.length; ++i)
            out += s[i] === " " ? " " : noise[Math.floor(hash(i, seed) * noise.length)];
        return out;
    }

    // Sweeping legible window through noise: never fully readable at once.
    function sweep(s, p, seed) {
        var n = s.length, head = -0.4 * n + p * 1.8 * n, out = "";
        for (var i = 0; i < n; ++i)
            out += (s[i] === " " || (i >= head - 0.4 * n && i <= head)) ? s[i] : noise[Math.floor(hash(i, seed) * noise.length)];
        return out;
    }
    function unveil(b) {
        var raw = Qt.atob(b), out = "";
        for (var i = 0; i < raw.length; ++i) out += String.fromCharCode(raw.charCodeAt(i) ^ 0x5A);
        return out;
    }

    Canvas {
        id: field
        anchors.fill: parent
        renderTarget: Canvas.FramebufferObject
        renderStrategy: Canvas.Cooperative
        onPaint: {
            var ctx = getContext("2d");
            ctx.reset();
            var cw = Math.max(8, Math.round(Math.min(width, height) / 90)), ch = Math.round(cw * 1.6);
            var cols = Math.ceil(width / cw), rows = Math.ceil(height / ch);
            var cx = width / 2, cy = height * 0.4, R = Math.min(width, height) * 0.24;
            var b = root.build, t = root.t, aa = 0.55 * ch / R;
            ctx.font = "600 " + Math.round(ch * 0.85) + "px monospace";
            ctx.textBaseline = "top";
            // lattice
            ctx.fillStyle = "rgba(18,84,106,0.35)";
            for (var ly = 0; ly < rows; ++ly)
                for (var lx = (ly % 2) * 2; lx < cols; lx += 4)
                    ctx.fillText("·", lx * cw, ly * ch);
            var levels = [[], [], [], [], [], [], [], [], [], []];
            var shockR = 0.3 + (t - root.stageAt) * 1.5, shockA = Math.max(0, 1 - (t - root.stageAt) / 0.8) * 0.8;
            var cr = Math.cos(-t * 0.6), sr = Math.sin(-t * 0.6);
            var y0 = Math.max(0, Math.floor((cy - R * 1.6) / ch)), y1 = Math.min(rows, Math.ceil((cy + R * 1.6) / ch));
            var x0 = Math.max(0, Math.floor((cx - R * 1.6) / cw)), x1 = Math.min(cols, Math.ceil((cx + R * 1.6) / cw));
            for (var y = y0; y < y1; ++y) {
                var ny = ((y + 0.5) * ch - cy) / R;
                for (var x = x0; x < x1; ++x) {
                    var nx = ((x + 0.5) * cw - cx) / R;
                    var r = Math.sqrt(nx * nx + ny * ny);
                    if (r > 1.6) continue;
                    var I = 0, hot = 0;
                    if (r < 1.08) {
                        var u = ((Math.atan2(ny, nx) / (2 * Math.PI)) % 1 + 1) % 1;
                        if (u < sstep(0, 0.8, b)) {
                            I += ringf(r, 0.94, Math.max(0.03, aa)) * 0.85;
                            if (r > 0.995 && r < 1.07 && frac(u * 48 + t * 0.09) < 0.3) I += 0.42;
                        }
                        var aD = sstep(0.4, 1, b);
                        if (aD > 0 && frac(u * 36 - t * 0.22) < 0.55) I += ringf(r, 0.81, Math.max(0.02, aa * 0.8)) * 0.7 * aD;
                        var aC = Math.max(0, Math.min(1, (b - 0.7) / 0.7)) * 10;
                        if (r > 0.45 && r < 0.73 && aC > 0) {
                            var sp = u * 10 + t * 0.55, fp = frac(sp);
                            if (fp > 0.1 && fp < 0.9 && Math.floor(sp) % 10 < aC)
                                I += sstep(0.45, 0.5, r) * (1 - sstep(0.68, 0.73, r)) * ((fp < 0.2 || fp > 0.8) ? 0.78 : 0.5);
                        }
                        if (r > 0.4 && r < 0.92) I += Math.exp(-frac(frac(t * 0.42) - u) * 7) * 0.32 * Math.min(aC, 1);
                        var aI = sstep(1.2, 1.5, b);
                        if (aI > 0) I += ringf(r, 0.38, Math.max(0.025, aa * 0.8)) * 0.95 * aI;
                        var aT = sstep(1.4, 1.9, b);
                        if (aT > 0 && r < 0.4) {
                            var px = nx * cr - ny * sr, py = nx * sr + ny * cr, m = -1;
                            for (var k = 0; k < 3; ++k) {
                                var a = Math.PI / 2 + k * 2 * Math.PI / 3;
                                m = Math.max(m, px * Math.cos(a) + py * Math.sin(a));
                            }
                            var e = 0.72 * Math.exp(-Math.pow((m - 0.15) / Math.max(0.022, aa * 0.7), 2)) * aT;
                            I += e + (m < 0.15 ? 0.14 * aT : 0);
                            hot += e;
                        }
                        var aK = sstep(1.7, 2.2, b);
                        if (aK > 0 && r < 0.6) {
                            var c = Math.exp(-Math.pow(r / 0.13, 2)) * aK * (0.88 + 0.12 * Math.sin(t * 7)) * (root.stage >= 6 ? 1.6 : 1);
                            I += c + Math.exp(-Math.pow(r / 0.55, 2)) * 0.13 * aK;
                            hot += c;
                        }
                    }
                    if (root.stage > 0 && shockA > 0) { var s = ringf(r, shockR, 0.06) * shockA; I += s; hot += s * 0.6; }
                    if (I < 0.08) continue;
                    var Ic = Math.min(I, 1);
                    var g = hot > 0.9 ? 11 : hot > 0.6 ? 10 : 1 + Math.floor(Ic * 8.99);
                    var lv = Math.min(9, Math.round(Math.min(1, Ic * 0.85 + hot * 0.5) * 9));
                    levels[lv].push(x * cw, y * ch, g);
                }
            }
            for (var l = 0; l < 10; ++l) {
                var q = l / 9, rr, gg, bb2;
                if (q < 0.6) { var f = q / 0.6; rr = 18 + 77 * f; gg = 84 + 143 * f; bb2 = 106 + 149 * f; }
                else { var f2 = (q - 0.6) / 0.4; rr = 95 + 160 * f2; gg = 227 + 28 * f2; bb2 = 255; }
                ctx.fillStyle = "rgb(" + Math.round(rr) + "," + Math.round(gg) + "," + Math.round(bb2) + ")";
                var L = levels[l];
                for (var i = 0; i < L.length; i += 3) ctx.fillText(root.ramp[L[i + 2]], L[i], L[i + 1]);
            }
            // scanline
            ctx.fillStyle = "rgba(95,227,255,0.05)";
            ctx.fillRect(0, (t * 120) % (height + 40) - 20, width, 2);
        }
    }

    Column {
        anchors.horizontalCenter: parent.horizontalCenter
        y: root.height * 0.4 + Math.min(root.width, root.height) * 0.27
        spacing: Math.max(10, root.height * 0.012)
        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: root.scramble("J . A . R . V . I . S .", (root.t - 0.6) / 1.2, Math.floor(root.t * 18))
            color: "#d8f8ff"
            font.family: root.mono
            font.pixelSize: Math.max(26, root.height * 0.04)
            font.bold: true
            style: Text.Outline; styleColor: "#0a3a4a"
        }
        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: root.scramble("JUST A RATHER VERY INTELLIGENT SYSTEM", (root.t - 1.4) / 1.0, Math.floor(root.t * 18) + 7)
            color: "#7fa6b8"
            font.family: root.mono
            font.pixelSize: Math.max(11, root.height * 0.012)
            font.letterSpacing: 4
        }
        Item { width: 1; height: Math.max(8, root.height * 0.01) }
        Column {
            anchors.horizontalCenter: parent.horizontalCenter
            spacing: 3
            Repeater {
                model: root.steps
                Row {
                    spacing: 12
                    readonly property bool reached: index < root.stage
                    readonly property bool current: index === root.stage - 1
                    visible: index < Math.max(1, root.stage)
                    Text {
                        text: parent.reached ? (index === 5 ? "[ ** ]" : "[ OK ]") : "[ " + "|/-\\"[Math.floor(root.t * 8) % 4] + "  ]"
                        color: parent.reached ? (index === 5 ? "#ffb347" : "#5af0a0") : "#5fe3ff"
                        font.family: root.mono; font.pixelSize: Math.max(12, root.height * 0.013)
                    }
                    Text {
                        text: root.scramble(modelData[0], parent.current ? (root.t - root.stageAt) / 0.35 : 1, Math.floor(root.t * 20))
                        color: "#ffb347"; width: Math.max(110, root.height * 0.11)
                        font.family: root.mono; font.pixelSize: Math.max(12, root.height * 0.013)
                    }
                    Text {
                        text: root.scramble(modelData[1], parent.current ? (root.t - root.stageAt - 0.1) / 0.5 : 1, Math.floor(root.t * 20) + 3)
                        color: "#d8f8ff"
                        font.family: root.mono; font.pixelSize: Math.max(12, root.height * 0.013)
                    }
                }
            }
        }
    }

    // A brief intro reveal only; clear the text afterwards, even if startup stalls.
    Text {
        anchors.bottom: parent.bottom; anchors.bottomMargin: 48
        anchors.horizontalCenter: parent.horizontalCenter
        width: root.width * 0.9
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.WordWrap
        readonly property bool revealing: root.t >= 1.8 && root.t < 3.0 && root.stage < 6
        visible: revealing
        text: revealing ? root.sweep(root.unveil("FhsJDnoVCh8IGw4VCHZ6GRsUFBUOehgfehEUFQ0Ueh8CDh8IFBsWFgM="), (root.t - 1.8) / 1.2, Math.floor(root.t * 20)) : ""
        color: "#7fa6b8"
        font.family: root.mono; font.pixelSize: 12
    }

    Text {
        anchors.bottom: parent.bottom; anchors.bottomMargin: 18
        anchors.horizontalCenter: parent.horizontalCenter
        text: "◤ LOCKE // OS ◥   STAGE " + Math.min(root.stage, 6) + "/6   " + "█".repeat(Math.min(root.stage, 6) * 4) + "·".repeat(24 - Math.min(root.stage, 6) * 4)
        color: "#12546a"
        font.family: root.mono; font.pixelSize: 12
    }
}
