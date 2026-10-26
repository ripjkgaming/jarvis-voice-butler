/* SPDX-License-Identifier: GPL-2.0-or-later
 * Native KWin thumbnails and navigation; Plasma provides compositor backdrop blur.
 */
import QtQuick
import QtQuick.Layouts
import org.kde.plasma.core as PlasmaCore
import org.kde.kirigami as Kirigami
import org.kde.kwin as KWin

KWin.TabBoxSwitcher {
    id: tabBox
    currentIndex: (factory.object as GlassDialog)?.currentIndex ?? currentIndex
    Instantiator {
        id: factory
        active: tabBox.visible
        delegate: GlassDialog {}
    }
    component GlassDialog: PlasmaCore.Dialog {
        id: dialog
        property alias currentIndex: gallery.currentIndex
        location: PlasmaCore.Types.Floating
        visible: tabBox.visible
        backgroundHints: PlasmaCore.Types.StandardBackground
        flags: Qt.Popup | Qt.X11BypassWindowManagerHint
        x: tabBox.screenGeometry.x + (tabBox.screenGeometry.width - width) / 2
        y: tabBox.screenGeometry.y + (tabBox.screenGeometry.height - height) / 2
        mainItem: Item {
            id: shell
            readonly property bool motion: Kirigami.Units.shortDuration > 0
            readonly property int cardWidth: Math.min(290, Math.round(tabBox.screenGeometry.width * 0.23))
            readonly property int cardHeight: Math.min(224, Math.round(tabBox.screenGeometry.height * 0.28))
            width: Math.min(tabBox.screenGeometry.width * 0.9, Math.max(560, Math.min(gallery.count, 4) * (cardWidth + 16) + 40))
            height: cardHeight + 120
            focus: true
            property real entrance: 0
            opacity: 0.35 + entrance * 0.65
            scale: 0.97 + entrance * 0.03
            Component.onCompleted: {
                entrance = 1;
                console.info("Jarvis Electric Glass: live gallery ready, " + gallery.count + " windows");
            }
            Behavior on entrance { enabled: shell.motion; NumberAnimation { duration: Kirigami.Units.longDuration; easing.type: Easing.OutCubic } }

            // Transparent lens edges only: the real desktop remains behind this surface.
            Rectangle {
                anchors.fill: parent; anchors.margins: -2
                radius: 20; color: "transparent"
                border.width: 1; border.color: "#6576cfff"
                Rectangle {
                    anchors.fill: parent; anchors.margins: 2
                    radius: 18; color: "transparent"
                    border.width: 1; border.color: "#183694ff"
                }
            }
            Rectangle {
                x: 22; y: 0; width: parent.width * 0.58; height: 1
                gradient: Gradient {
                    orientation: Gradient.Horizontal
                    GradientStop { position: 0; color: "#007acbff" }
                    GradientStop { position: 0.38; color: "#caaff0ff" }
                    GradientStop { position: 1; color: "#00448eff" }
                }
            }
            RowLayout {
                x: 24; y: 18; width: parent.width - 48; height: 25
                Text { text: "J A R V I S"; color: "#d2f4ff"; font.family: "Rajdhani"; font.weight: Font.DemiBold; font.pixelSize: 17; font.letterSpacing: 2 }
                Text { text: "WINDOWS"; color: "#89bceb"; font.family: "Share Tech Mono"; font.pixelSize: 11; Layout.leftMargin: 12 }
                Item { Layout.fillWidth: true }
                Text { text: String(gallery.currentIndex + 1).padStart(2, "0") + "  /  " + String(gallery.count).padStart(2, "0"); color: "#ade9ff"; font.family: "Share Tech Mono"; font.pixelSize: 12 }
            }
            ListView {
                id: gallery
                x: 20; y: 56; width: parent.width - 40; height: shell.cardHeight + 12
                orientation: ListView.Horizontal
                spacing: 16; clip: true; focus: true
                model: tabBox.model
                currentIndex: tabBox.currentIndex
                boundsBehavior: Flickable.StopAtBounds
                keyNavigationWraps: true
                highlightFollowsCurrentItem: true
                highlightMoveDuration: shell.motion ? Kirigami.Units.longDuration : 0
                highlightResizeDuration: 0
                preferredHighlightBegin: 0
                preferredHighlightEnd: width - shell.cardWidth
                highlightRangeMode: ListView.ApplyRange
                highlight: Rectangle {
                    width: shell.cardWidth; height: shell.cardHeight; y: 6
                    radius: 16
                    gradient: Gradient {
                        GradientStop { position: 0; color: "#243d9bff" }
                        GradientStop { position: 0.48; color: "#10347fff" }
                        GradientStop { position: 1; color: "#2c147ddd" }
                    }
                    border.color: "#a56bcaff"; border.width: 1
                }
                delegate: Item {
                    id: card
                    width: shell.cardWidth; height: shell.cardHeight + 12
                    readonly property bool selected: index === gallery.currentIndex
                    property real fault: 0
                    Accessible.name: model.caption
                    Accessible.role: Accessible.ListItem
                    onSelectedChanged: if (selected && shell.motion) faultAnimation.restart()
                    NumberAnimation { id: faultAnimation; target: card; property: "fault"; from: 1; to: 0; duration: Kirigami.Units.longDuration; easing.type: Easing.OutCubic }
                    Item {
                        anchors.fill: parent; anchors.topMargin: 6; anchors.bottomMargin: 6
                        scale: card.selected ? 1 : 0.96
                        opacity: card.selected ? 1 : 0.66
                        Behavior on scale { enabled: shell.motion; NumberAnimation { duration: Kirigami.Units.longDuration; easing.type: Easing.OutCubic } }
                        Behavior on opacity { enabled: shell.motion; NumberAnimation { duration: Kirigami.Units.shortDuration } }
                        Rectangle {
                            anchors.fill: parent; radius: 16
                            color: "#071966dd"; border.color: card.selected ? "#459bdeff" : "#234f94d0"; border.width: 1
                        }
                        Item {
                            id: preview
                            x: 12; y: 12; width: parent.width - 24; height: parent.height - 68
                            KWin.WindowThumbnail {
                                anchors.fill: parent
                                wId: windowId
                                opacity: card.selected ? 0.92 : 0.75
                            }
                            Rectangle { anchors.fill: parent; color: "#102b8aff" }
                            Repeater {
                                model: 3
                                Rectangle {
                                    x: parent.width * (0.12 + index * 0.2) + Math.floor(card.fault * 5) * (index % 2 ? 2 : -2)
                                    y: parent.height * (0.25 + index * 0.25)
                                    width: parent.width * 0.22; height: 1
                                    color: "#b9eeff"; opacity: card.fault * 0.5
                                }
                            }
                        }
                        RowLayout {
                            x: 14; anchors.bottom: parent.bottom; anchors.bottomMargin: 13
                            width: parent.width - 28; height: 30; spacing: 11
                            Kirigami.Icon { source: model.icon; Layout.preferredWidth: 28; Layout.preferredHeight: 28 }
                            Text {
                                text: model.caption; textFormat: Text.PlainText; color: "#e1f6ff"
                                font.family: "Rajdhani"; font.pixelSize: 16; font.weight: card.selected ? Font.DemiBold : Font.Normal
                                elide: Text.ElideMiddle; Layout.fillWidth: true
                            }
                        }
                        Rectangle {
                            anchors.bottom: parent.bottom; anchors.horizontalCenter: parent.horizontalCenter
                            width: card.selected ? parent.width * 0.5 : 0; height: 1
                            color: "#94deff"; opacity: 0.85
                            Behavior on width { enabled: shell.motion; NumberAnimation { duration: Kirigami.Units.longDuration; easing.type: Easing.OutCubic } }
                        }
                    }
                    TapHandler {
                        onTapped: {
                            gallery.currentIndex = index;
                            gallery.model.activate(index);
                        }
                    }
                }
                Connections {
                    target: tabBox
                    function onCurrentIndexChanged() {
                        gallery.currentIndex = tabBox.currentIndex;
                        gallery.positionViewAtIndex(gallery.currentIndex, ListView.Contain);
                    }
                }
            }
            Text {
                anchors.horizontalCenter: parent.horizontalCenter; anchors.bottom: parent.bottom; anchors.bottomMargin: 18
                text: gallery.count ? "ALT + TAB   navigate    /    release ALT   switch" : "No open windows"
                color: "#91bce4"; font.family: "Share Tech Mono"; font.pixelSize: 11
            }
            Keys.onPressed: event => {
                if (gallery.count < 1) return;
                if (event.key === Qt.Key_Left || event.key === Qt.Key_Up) {
                    gallery.currentIndex = (gallery.currentIndex + gallery.count - 1) % gallery.count; event.accepted = true;
                } else if (event.key === Qt.Key_Right || event.key === Qt.Key_Down) {
                    gallery.currentIndex = (gallery.currentIndex + 1) % gallery.count; event.accepted = true;
                }
            }
        }
        onSceneGraphError: () => {}
    }
}
