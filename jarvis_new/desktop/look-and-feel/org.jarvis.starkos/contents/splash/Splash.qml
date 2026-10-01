/*
    STARK OS boot splash: the HUD reactor spins up while Plasma loads.
    Rings are pre-drawn SVG layers (desktop/build.py); each one rotates at
    its own speed, the progress hairline fills with the KSplash stage.
*/

import QtQuick
import org.kde.kirigami as Kirigami

Rectangle {
    id: root
    color: "#010509"

    property int stage
    readonly property real progress: Math.min(1, Math.max(0, stage / 6))
    readonly property real reactor: Math.min(width, height) * 0.46
    readonly property bool animate: visible && Kirigami.Units.longDuration > 0

    onStageChanged: {
        if (stage >= 1 && content.opacity === 0) {
            intro.running = true;
        }
    }

    // Blueprint glow behind the reactor.
    Rectangle {
        anchors.centerIn: parent
        width: root.reactor * 2.2
        height: width
        radius: width / 2
        gradient: Gradient {
            GradientStop { position: 0.0; color: "#0a2a44" }
            GradientStop { position: 1.0; color: "#010509" }
        }
        opacity: 0.55
    }

    Item {
        id: content
        anchors.fill: parent
        opacity: 0

        Item {
            id: reactorItem
            width: root.reactor
            height: root.reactor
            anchors.centerIn: parent
            anchors.verticalCenterOffset: -root.height * 0.04

            Image {
                anchors.fill: parent
                source: "images/ring-core.svg"
                sourceSize.width: width
                sourceSize.height: height
            }
            Image {
                anchors.fill: parent
                source: "images/ring-ticks.svg"
                sourceSize.width: width
                sourceSize.height: height
                RotationAnimator on rotation { from: 0; to: 360; duration: 60000; loops: Animation.Infinite; running: root.animate }
            }
            Image {
                anchors.fill: parent
                source: "images/ring-segments.svg"
                sourceSize.width: width
                sourceSize.height: height
                RotationAnimator on rotation { from: 360; to: 0; duration: 40000; loops: Animation.Infinite; running: root.animate }
            }
            Image {
                anchors.fill: parent
                source: "images/ring-inner.svg"
                sourceSize.width: width
                sourceSize.height: height
                RotationAnimator on rotation { from: 0; to: 360; duration: 20000; loops: Animation.Infinite; running: root.animate }
            }
            Text {
                anchors.centerIn: parent
                text: "J"
                color: "#d8f8ff"
                font.family: "Orbitron"
                font.weight: Font.Bold
                font.pixelSize: root.reactor * 0.11
            }
        }

        Column {
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.top: reactorItem.bottom
            anchors.topMargin: root.height * 0.035
            spacing: 14

            Text {
                anchors.horizontalCenter: parent.horizontalCenter
                text: "J.A.R.V.I.S."
                color: "#d8f8ff"
                font.family: "Orbitron"
                font.weight: Font.DemiBold
                font.pixelSize: Math.max(22, root.height * 0.032)
                font.letterSpacing: font.pixelSize * 0.32
            }
            Text {
                anchors.horizontalCenter: parent.horizontalCenter
                text: root.progress >= 1 ? "ALL SYSTEMS ONLINE" : "SYSTEMS ONLINE  " + Math.round(root.progress * 100) + "%"
                color: "#7fa6b8"
                font.family: "Share Tech Mono"
                font.pixelSize: Math.max(12, root.height * 0.014)
                font.letterSpacing: font.pixelSize * 0.25
            }
            // Progress hairline with a bright head.
            Item {
                anchors.horizontalCenter: parent.horizontalCenter
                width: Math.max(280, root.width * 0.22)
                height: 3
                Rectangle { anchors.verticalCenter: parent.verticalCenter; width: parent.width; height: 1; color: "#5fe3ff"; opacity: 0.18 }
                Rectangle {
                    anchors.verticalCenter: parent.verticalCenter
                    height: 2
                    width: parent.width * root.progress
                    color: "#5fe3ff"
                    Behavior on width { enabled: root.animate; NumberAnimation { duration: 240; easing.type: Easing.OutCubic } }
                }
            }
        }

        Text {
            anchors { left: parent.left; bottom: parent.bottom; margins: 28 }
            text: "STARK OS // PERSONAL INTELLIGENCE"
            color: "#46677a"
            font.family: "Rajdhani"
            font.weight: Font.DemiBold
            font.pixelSize: 13
            font.letterSpacing: 4
        }
    }

    OpacityAnimator {
        id: intro
        target: content
        from: 0
        to: 1
        duration: root.animate ? 600 : 0
        easing.type: Easing.InOutQuad
    }
}
