import QtQuick
import org.kde.kirigami as Kirigami

Rectangle {
    id: root
    color: "#03101a"
    property int stage: 0
    readonly property real progress: Math.min(1, Math.max(0, stage / 6))
    readonly property bool motionEnabled: visible && Kirigami.Units.longDuration > 1
    readonly property real reactorSize: Math.min(width * 0.6, height * 0.36, 440)
    readonly property string status: stage >= 6 ? "ALL SYSTEMS ONLINE" :
        stage >= 4 ? "PREPARING WORKSPACE" : stage >= 2 ? "LOADING DESKTOP" : "INITIALIZING SESSION"

    Item {
        id: content
        anchors.fill: parent
        opacity: stage > 0 ? 1 : 0
        Behavior on opacity {
            enabled: root.motionEnabled
            OpacityAnimator { duration: Kirigami.Units.longDuration * 2; easing.type: Easing.InOutQuad }
        }

        scale: stage > 0 ? 1 : 0.94
        Behavior on scale { enabled: root.motionEnabled; NumberAnimation { duration: Kirigami.Units.longDuration * 3; easing.type: Easing.OutCubic } }

        Item {
            id: reactor
            width: root.reactorSize
            height: width
            anchors.horizontalCenter: parent.horizontalCenter
            y: root.height * 0.235
            Image {
                anchors.fill: parent
                source: "images/reactor.svg"
                sourceSize.width: Math.ceil(width)
                sourceSize.height: Math.ceil(height)
            }
            Image {
                anchors.fill: parent
                source: "images/sweep.svg"
                sourceSize.width: Math.ceil(width)
                sourceSize.height: Math.ceil(height)
                RotationAnimator on rotation {
                    from: 0; to: 360; duration: 30000; loops: Animation.Infinite
                    running: root.motionEnabled && root.stage > 0 && root.stage < 6
                }
            }
        }

        Column {
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.top: reactor.bottom
            anchors.topMargin: root.height * 0.03
            spacing: Math.max(12, root.height * 0.015)
            Text {
                anchors.horizontalCenter: parent.horizontalCenter
                text: "J.A.R.V.I.S."
                color: "#d8f8ff"
                font.family: "Orbitron"
                font.weight: Font.DemiBold
                font.pixelSize: Math.max(22, Math.min(36, root.height * 0.034))
                font.letterSpacing: 3
                Accessible.role: Accessible.StaticText
                Accessible.name: text
            }
            Text {
                anchors.horizontalCenter: parent.horizontalCenter
                text: root.status
                color: "#7fa6b8"
                font.family: "Share Tech Mono"
                font.pixelSize: Math.max(13, Math.min(18, root.height * 0.018))
                Accessible.role: Accessible.StaticText
                Accessible.name: text
            }
            Rectangle {
                width: Math.min(root.width * 0.42, 360)
                height: 2
                color: "#173e50"
                anchors.horizontalCenter: parent.horizontalCenter
                Accessible.role: Accessible.ProgressBar
                Accessible.name: "Desktop loading"
                Accessible.description: Math.round(root.progress * 100) + " percent"
                Rectangle {
                    width: parent.width * root.progress
                    height: parent.height
                    color: "#5fe3ff"
                    Behavior on width {
                        enabled: root.motionEnabled
                        NumberAnimation { duration: Kirigami.Units.longDuration; easing.type: Easing.OutCubic }
                    }
                }
            }
        }
        Rectangle {
            anchors.horizontalCenter: parent.horizontalCenter
            y: root.height * root.progress
            width: root.width * 0.7; height: 1
            color: "#63e5ff"
            opacity: root.stage > 0 && root.stage < 6 ? 0.18 : 0
            Behavior on y { enabled: root.motionEnabled; NumberAnimation { duration: Kirigami.Units.longDuration * 2; easing.type: Easing.OutCubic } }
        }
        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.bottom: parent.bottom
            anchors.bottomMargin: Math.max(20, root.height * 0.04)
            text: "PERSONAL INTELLIGENCE // RIPJK"
            color: "#7fa6b8"
            font.family: "Share Tech Mono"
            font.pixelSize: 12
        }
    }
}
