// SPDX-License-Identifier: MIT
import QtQuick
import org.kde.kirigami as Kirigami

Item {
    id: card
    implicitWidth: 264
    implicitHeight: 72
    property real level: 0
    property real maximum: 100
    property bool muted: false
    property bool active: false
    property bool reducedMotion: Kirigami.Units.longDuration <= 1
    property real reveal: 1
    property real pulse: 1
    readonly property real boundedLevel: Math.max(0, Math.min(maximum, level))
    // The OSD reports percentages even when the available range is 150%.
    readonly property int zone: muted || boundedLevel === 0 ? 0 : boundedLevel <= 25 ? 1 : boundedLevel <= 60 ? 2 : boundedLevel <= 100 ? 3 : boundedLevel <= 125 ? 4 : 5
    readonly property bool danger: zone === 5
    readonly property color accent: zone === 0 ? "#8baabd" : zone === 1 ? "#7bafff" : zone === 2 ? "#8be6ff" : zone === 3 ? "#78ffd0" : zone === 4 ? "#ffd18b" : "#ff5269"
    readonly property color deepAccent: zone === 5 ? "#a90b30" : zone === 4 ? "#a66516" : zone === 3 ? "#087c67" : "#155cbd"
    property int direction: 0
    property real previousLevel: 0
    property bool previousMuted: false
    property int previousZone: 0
    property string eventKind: "level"
    property real dangerBeat: 0
    readonly property real alarm: danger && !reducedMotion ? 0.25 + dangerBeat * 0.75 : 0
    readonly property bool limitHit: eventKind === "ceiling"
    readonly property string statusText: muted ? "SIGNAL CUT" : limitHit ? "LIMIT REACHED" : boundedLevel === 0 ? "SILENT" : danger ? "DANGER // HIGH GAIN" : zone === 4 ? "GAIN BOOST" : zone === 1 ? "QUIET OUTPUT" : zone === 2 ? "SIGNAL ACTIVE" : "FULL SIGNAL"
    readonly property real energy: 1 - pulse
    readonly property real glitch: pulse < 0.38 ? Math.sin(pulse * 54) * energy : 0
    property real displayedLevel: muted ? 0 : boundedLevel

    Behavior on displayedLevel {
        enabled: card.active && !card.reducedMotion
        NumberAnimation { duration: 180; easing.type: Easing.OutCubic }
    }
    function changed() {
        if (active && !reducedMotion) feedback.restart()
    }
    function updateLevelEffect() {
        direction = boundedLevel > previousLevel ? 1 : boundedLevel < previousLevel ? -1 : 0
        eventKind = muted ? "mute" : boundedLevel === 0 ? "floor" : zone !== previousZone ? "zone" : "level"
        changed()
    }
    // Called once per native OSD notification, including unchanged limit values.
    function notified() {
        direction = boundedLevel > previousLevel ? 1 : boundedLevel < previousLevel ? -1 : 0
        if (muted !== previousMuted) eventKind = muted ? "mute" : "unmute"
        else if (!muted && boundedLevel >= maximum && previousLevel >= maximum) eventKind = "ceiling"
        else if (!muted && boundedLevel === 0) eventKind = "floor"
        else if (zone !== previousZone) eventKind = "zone"
        else eventKind = "level"
        previousLevel = boundedLevel
        previousMuted = muted
        previousZone = zone
        changed()
    }
    onLevelChanged: updateLevelEffect()
    onMaximumChanged: updateLevelEffect()
    onMutedChanged: {
        eventKind = muted ? "mute" : "unmute"
        changed()
    }
    onActiveChanged: {
        if (active && !reducedMotion) {
            arrival.restart()
            feedback.restart()
        } else {
            arrival.stop()
            feedback.stop()
            reveal = 1
            pulse = 1
        }
    }
    onReducedMotionChanged: {
        if (reducedMotion) {
            arrival.stop()
            feedback.stop()
            reveal = 1
            pulse = 1
        }
    }
    NumberAnimation { id: arrival; target: card; property: "reveal"; from: 0; to: 1; duration: 480; easing.type: Easing.OutExpo }
    NumberAnimation { id: feedback; target: card; property: "pulse"; from: 0; to: 1; duration: card.limitHit ? 1250 : 860; easing.type: Easing.Linear }
    SequentialAnimation on dangerBeat {
        running: card.active && card.danger && !card.reducedMotion
        loops: Animation.Infinite
        NumberAnimation { from: 0; to: 1; duration: 260; easing.type: Easing.InOutSine }
        NumberAnimation { from: 1; to: 0; duration: 540; easing.type: Easing.InOutSine }
    }

    Item {
        id: chassis
        anchors.fill: parent
        clip: true
        opacity: 0.2 + card.reveal * 0.8
        scale: 0.88 + card.reveal * 0.12 - (card.limitHit ? card.energy * 0.025 : card.alarm * 0.008)
        transform: Translate { x: card.glitch * (card.limitHit ? 6 : 2); y: (1 - card.reveal) * 7 }
        Rectangle { anchors.fill: parent; color: "#f0081421"; radius: 2; border.color: card.accent }
        Rectangle {
            anchors.fill: parent; anchors.margins: 2
            color: "transparent"
            border.color: card.accent
            border.width: card.danger ? 2 : 1
            opacity: Math.max(card.energy * 0.55, card.alarm * 0.9)
        }
        Rectangle {
            anchors.fill: parent; anchors.margins: 3
            color: card.accent
            opacity: card.danger ? card.alarm * 0.16 + (card.limitHit ? card.energy * 0.13 : 0) : card.energy * 0.035
        }
        // Quiet output has soft sonar rings; unmute and range crossings bloom.
        Repeater {
            model: 3
            Rectangle {
                required property int index
                readonly property real phase: Math.max(0, Math.min(1, card.pulse * 1.4 - index * 0.16))
                width: 18 + phase * (card.zone === 1 ? 65 : 160); height: width
                x: 23 - width / 2; y: 44 - height / 2; radius: width / 2
                color: "transparent"; border.color: card.accent; border.width: 1
                visible: !card.muted && (card.zone === 1 || card.eventKind === "unmute" || card.eventKind === "zone")
                opacity: card.energy * (1 - phase) * 0.65
            }
        }
        // Fine static raster under the finite reconstruction sweeps.
        Repeater {
            model: 18
            Rectangle {
                required property int index
                y: index * 4; width: chassis.width; height: 1
                color: "#163b57"; opacity: 0.23
            }
        }
        Repeater {
            model: 4
            Item {
                id: corner
                required property int index
                readonly property bool atRight: index % 2 === 1
                readonly property bool atBottom: index > 1
                x: atRight ? chassis.width - 15 : 0
                y: atBottom ? chassis.height - 11 : 0
                width: 15; height: 11
                opacity: 0.65 + card.energy * 0.35
                transform: Translate { x: (corner.atRight ? 1 : -1) * (1 - card.reveal) * 18 }
                Rectangle { width: 15; height: 2; y: parent.atBottom ? 9 : 0; color: card.accent }
                Rectangle { width: 2; height: 11; x: parent.atRight ? 13 : 0; color: card.accent }
            }
        }
        // Acquisition rail expands from the center and leaves staggered breaks.
        Rectangle { x: 20; y: 0; width: (chassis.width - 40) * card.reveal; height: 1; color: card.accent }
        Repeater {
            model: 7
            Rectangle {
                required property int index
                x: 23 + index * 10; y: 4; width: 6; height: 2
                color: card.accent
                opacity: card.energy > 0 ? 0.15 + 0.85 * Math.abs(Math.sin(card.pulse * 32 - index)) : 0.3
            }
        }
        Text {
            x: 13; y: 13
            text: card.muted ? "AUDIO // MUTE" : card.limitHit ? "! MAX OUTPUT !" : card.danger ? "! AUDIO DANGER" : "AUDIO // OUT"
            color: card.danger ? card.accent : "#a6d7ed"; font.pixelSize: 9; font.letterSpacing: 1.8
        }
        Text {
            anchors.right: parent.right; anchors.rightMargin: 13; y: 8
            text: card.muted ? "MUTE" : Math.round(card.boundedLevel) + "%"
            color: card.accent; font.pixelSize: 22; font.weight: Font.DemiBold
            transform: Translate { x: card.glitch * -1.5 }
        }
        // Short equalizer burst follows each adjustment, then rests completely.
        Repeater {
            model: 5
            Rectangle {
                required property int index
                visible: !card.danger
                x: 13 + index * 4; width: 2
                height: card.muted ? 2 : 4 + (1 - Math.abs(index - 2) / 3) * 10 + Math.abs(Math.sin(card.pulse * 40 + index * 2)) * card.energy * 8
                y: 52 - height
                color: index % 2 ? card.deepAccent : card.accent
                opacity: card.muted ? 0.35 : 0.85
            }
        }
        // A geometric warning sign avoids platform-dependent emoji rendering.
        Item {
            x: 12; y: 32; width: 22; height: 22
            visible: card.danger
            scale: 1 + card.alarm * 0.12 + (card.limitHit ? card.energy * 0.18 : 0)
            Canvas {
                anchors.fill: parent
                onPaint: {
                    var ctx = getContext("2d")
                    ctx.reset(); ctx.beginPath()
                    ctx.moveTo(11, 1); ctx.lineTo(21, 20); ctx.lineTo(1, 20); ctx.closePath()
                    ctx.fillStyle = "#481326"; ctx.fill()
                    ctx.strokeStyle = "#ff5269"; ctx.lineWidth = 2; ctx.stroke()
                }
            }
            Text { anchors.horizontalCenter: parent.horizontalCenter; y: 4; text: "!"; color: "#ffb9c4"; font.bold: true; font.pixelSize: 15 }
        }
        Item {
            id: track
            x: 43; y: 37; width: parent.width - 56; height: 15
            clip: true
            Rectangle { anchors.fill: parent; color: "#192f43" }
            Rectangle {
                height: parent.height
                width: parent.width * card.displayedLevel / Math.max(1, card.maximum)
                gradient: Gradient {
                    orientation: Gradient.Horizontal
                    GradientStop { position: 0; color: card.deepAccent }
                    GradientStop { position: 0.8; color: card.accent }
                    GradientStop { position: 1; color: card.accent }
                }
            }
            Repeater {
                model: 30
                Rectangle {
                    required property int index
                    x: (index + 1) * track.width / 31
                    width: 2; height: track.height; color: "#081421"
                }
            }
            Rectangle {
                x: parent.width * card.displayedLevel / Math.max(1, card.maximum) - 2
                width: 2; height: parent.height; color: "#e0faff"
                opacity: card.muted ? 0 : 0.5 + card.energy * 0.5
            }
            Repeater {
                model: 3
                Rectangle {
                    required property int index
                    x: (((card.direction < 0 ? 1 - card.pulse : card.pulse) * 2.4 + index / 3) % 1) * (track.width + 28) - 28
                    width: 18 - index * 4; height: track.height
                    color: card.accent; opacity: card.energy * (0.65 - index * 0.15)
                }
            }
        }
        // Mute closes the signal like a shutter; zero sends an inward collapse.
        Rectangle {
            x: 43; y: 37; width: (chassis.width - 56) * card.energy; height: 15
            visible: card.eventKind === "mute" || card.eventKind === "floor"
            color: card.accent; opacity: card.energy * 0.45
        }
        // Amber gain has climbing sparks. Red turns this rail into hazard stripes.
        Item {
            x: 43; y: 29; width: chassis.width - 56; height: 5; clip: true
            visible: card.zone >= 4
            Repeater {
                model: 24
                Rectangle {
                    required property int index
                    x: index * 10 + (card.reducedMotion ? 0 : card.pulse * 18); y: 1
                    width: card.danger ? 6 : 2; height: card.danger ? 4 : 2
                    rotation: card.danger ? -45 : 0
                    color: card.accent
                    opacity: card.danger ? 0.3 + card.alarm * 0.6 : card.energy * Math.max(0, Math.sin(index + card.pulse * 24))
                }
            }
        }
        Text { x: 43; y: 57; text: card.statusText; color: card.accent; font.pixelSize: 7; font.letterSpacing: 1 }
        Text {
            x: 112; y: 12
            visible: card.energy > 0 && !card.danger && !card.muted && card.direction !== 0
            text: card.direction > 0 ? ">>" : "<<"; color: card.accent
            font.pixelSize: 10; opacity: card.energy
            transform: Translate { x: card.direction * card.pulse * 15 }
        }
        Text { anchors.right: parent.right; anchors.rightMargin: 13; y: 57; text: Math.round(card.maximum) + " MAX"; color: "#739ebc"; font.pixelSize: 7; font.letterSpacing: 1 }
        // Moving scan head and broken horizontal slices: finite, input-free.
        Rectangle {
            y: (card.pulse * 2 % 1) * chassis.height; width: chassis.width; height: 8
            visible: card.energy > 0
            gradient: Gradient {
                GradientStop { position: 0; color: "transparent" }
                GradientStop { position: 1; color: card.accent }
            }
            opacity: card.energy * 0.65
        }
        Repeater {
            model: 6
            Rectangle {
                required property int index
                x: ((index * 47 + card.pulse * 130) % chassis.width) - 20
                y: 9 + index * 10 + card.glitch * 2
                width: 18 + (index % 3) * 24; height: index % 2 ? 1 : 2
                color: index % 2 ? card.deepAccent : card.accent
                opacity: card.energy * (0.25 + 0.6 * Math.max(0, Math.sin(card.pulse * 70 + index * 3)))
            }
        }
        Rectangle { x: chassis.width - 48; y: chassis.height - 1; width: 30; height: 1; color: card.accent }
    }
}
