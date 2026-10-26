// SPDX-License-Identifier: MIT
import QtQuick
import org.kde.kirigami as Kirigami
import org.kde.plasma.workspace.osd

OsdWindow {
    id: root
    property int timeout: 1800
    property var osdValue: 0
    property int osdMaxValue: 100
    property string osdAdditionalText: ""
    property string icon: ""
    property bool showingProgress: false
    property bool animateOpacity: false
    property real opacity: 1
    readonly property bool volume: icon.indexOf("audio-volume-") === 0
    readonly property bool muted: icon.indexOf("muted") >= 0
    readonly property bool reducedMotion: MotionConfig.reducedMotion || Kirigami.Units.longDuration <= 1
    property string jarvisVolumeVersion: "3.0"
    borders: volume ? 0 : Qt.TopEdge | Qt.BottomEdge | Qt.LeftEdge | Qt.RightEdge

    LayoutMirroring.enabled: Application.layoutDirection === Qt.RightToLeft
    LayoutMirroring.childrenInherit: true
    width: (volume ? mainItem.implicitWidth : Math.max(Math.min(Screen.desktopAvailableWidth / 2, mainItem.implicitWidth), Kirigami.Units.gridUnit * 15)) + leftPadding + rightPadding
    height: mainItem.implicitHeight + topPadding + bottomPadding

    // Plasma restarts this on every key press, including repeated limit values.
    Behavior on opacity {
        enabled: root.animateOpacity
        SequentialAnimation {
            PauseAnimation { duration: root.volume && !root.reducedMotion ? 1450 : 1800 }
            NumberAnimation { duration: root.volume && !root.reducedMotion ? 250 : 0; easing.type: Easing.InCubic }
        }
    }
    onAnimateOpacityChanged: if (animateOpacity && volume) volumeCard.notified()

    mainItem: Item {
        opacity: root.opacity
        implicitWidth: root.volume ? volumeCard.implicitWidth : nativeItem.implicitWidth
        implicitHeight: root.volume ? volumeCard.implicitHeight : nativeItem.implicitHeight
        VolumeCard {
            id: volumeCard
            objectName: "volumeCard"
            anchors.fill: parent
            visible: root.volume
            active: root.visible && root.volume
            level: root.showingProgress ? Number(root.osdValue) || 0 : 0
            maximum: Math.max(100, root.osdMaxValue)
            muted: root.muted
            reducedMotion: root.reducedMotion
        }
        NativeOsdItem {
            id: nativeItem
            anchors.fill: parent
            visible: !root.volume
            osdValue: root.osdValue
            osdMaxValue: root.osdMaxValue
            osdAdditionalText: root.osdAdditionalText
            showingProgress: root.showingProgress
            icon: root.icon
        }
    }
}
