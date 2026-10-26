/*
 KWin - the KDE window manager
 This file is part of the KDE project.

 SPDX-FileCopyrightText: 2011 Martin Gräßlin <mgraesslin@kde.org>

 SPDX-License-Identifier: GPL-2.0-or-later
 */
import QtQuick
import QtQuick.Layouts
import org.kde.plasma.core as PlasmaCore
import org.kde.kirigami as Kirigami
import org.kde.ksvg as KSvg
import org.kde.plasma.components as PlasmaComponents3
import org.kde.kwin as KWin

KWin.TabBoxSwitcher {
    id: tabBox
    Component.onCompleted: console.info("Jarvis Cinematic HUD: expanded cyan command panel ready")
    currentIndex: compactListView.currentIndex

    /**
    * Returns the caption with adjustments for minimized items.
    * @param caption the original caption
    * @param mimized whether the item is minimized
    * @return Caption adjusted for minimized state
    **/
    function itemCaption(caption, minimized) {
        if (minimized) {
            return "(" + caption + ")";
        }
        return caption;
    }

    TextMetrics {
        id: textMetrics
        property string longestCaption: tabBox.model?.longestCaption() || placeholderLabel.text
        text: itemCaption(longestCaption, true)
        font.bold: true
    }

    onVisibleChanged: {
        if (visible) {
            // Window captions may have change completely
            textMetrics.longestCaption = tabBox.model?.longestCaption() || placeholderLabel.text;
        }
    }
    onModelChanged: {
        textMetrics.longestCaption = tabBox.model?.longestCaption() || placeholderLabel.text;
    }

    // For mouse clicking, we delay the activation just enough to have
    // visual feedback of the highlight switching to the selected item
    Timer {
        id: activationTimer
        interval: Kirigami.Units.shortDuration
        onTriggered: {
            tabBox.model.activate(compactListView.currentIndex)
        }
    }

    PlasmaCore.Dialog {
        id: dialog
        location: PlasmaCore.Types.Floating
        visible: tabBox.visible
        flags: Qt.Popup | Qt.X11BypassWindowManagerHint
        x: tabBox.screenGeometry.x + tabBox.screenGeometry.width * 0.5 - dialogMainItem.width * 0.5
        y: tabBox.screenGeometry.y + tabBox.screenGeometry.height * 0.5 - dialogMainItem.height * 0.5

        mainItem: Item {
            id: dialogMainItem

            property int optimalWidth: textMetrics.width + Kirigami.Units.iconSizes.small + 2 * Kirigami.Units.smallSpacing + hoverItem.margins.right + hoverItem.margins.left
            property int optimalHeight: compactListView.rowHeight * (compactListView.count || 1)
            width: Math.min(Math.max(tabBox.screenGeometry.width * 0.28, optimalWidth), tabBox.screenGeometry.width * 0.8)
            height: Math.min(optimalHeight + 52, tabBox.screenGeometry.height * 0.8)

            Rectangle {
                anchors.fill: parent
                anchors.margins: -8
                radius: 5
                gradient: Gradient {
                    GradientStop { position: 0; color: "#ec102b3b" }
                    GradientStop { position: 1; color: "#f205101c" }
                }
                border.color: "#448ad9ef"; border.width: 1
                Rectangle { width: 52; height: 2; color: "#b3f6ff"; anchors.top: parent.top; anchors.left: parent.left; anchors.margins: 1 }
                Rectangle { width: 52; height: 2; color: "#b3f6ff"; anchors.bottom: parent.bottom; anchors.right: parent.right; anchors.margins: 1 }
            }
            Text {
                x: 8; y: 5
                text: "J.A.R.V.I.S.   /   WINDOW CONTROL"
                color: "#7de7ff"; font.family: "Share Tech Mono"; font.pixelSize: 12; font.letterSpacing: 1.6
            }
            Text {
                anchors.right: parent.right; anchors.rightMargin: 8; y: 24
                text: (compactListView.currentIndex + 1) + " / " + compactListView.count
                color: "#77aabb"; font.family: "Share Tech Mono"; font.pixelSize: 10
            }
            Rectangle { x: 8; y: 39; width: parent.width - 16; height: 1; color: "#365565" }

            // just to get the margin sizes
            KSvg.FrameSvgItem {
                id: hoverItem
                imagePath: "widgets/viewitem"
                prefix: "hover"
                visible: false
            }

            ListView {
                id: compactListView

                // the maximum text width + icon item width (32 + 4 margin) + margins for hover item
                property int rowHeight: Math.max(48, textMetrics.height + hoverItem.margins.top + hoverItem.margins.bottom)

                anchors.fill: parent
                anchors.topMargin: 48
                clip: true
                focus: true

                model: tabBox.model
                delegate: RowLayout {

                    width: compactListView.width
                    height: compactListView.rowHeight
                    opacity: minimized ? 0.75 : 1.0

                    spacing: 2 * Kirigami.Units.smallSpacing

                    Accessible.name: captionItem.text//Accessible.name
                    Kirigami.Icon {
                        id: iconItem
                        source: model.icon
                        Layout.preferredWidth: 28
                        Layout.preferredHeight: 28
                        Layout.leftMargin: hoverItem.margins.left
                    }
                    PlasmaComponents3.Label {
                        id: captionItem
                        horizontalAlignment: Text.AlignLeft
                        verticalAlignment: Text.AlignVCenter
                        text: itemCaption(caption, minimized)
                        textFormat: Text.PlainText
                        color: index === compactListView.currentIndex ? "#effcff" : "#a6bfca"
                        font.pixelSize: 15
                        font.weight: index === compactListView.currentIndex ? Font.Bold : Font.Normal
                        elide: Text.ElideMiddle
                        Layout.fillWidth: true
                        Layout.rightMargin: hoverItem.margins.right
                        Layout.topMargin: hoverItem.margins.top
                        Layout.bottomMargin: hoverItem.margins.bottom
                    }
                    TapHandler {
                        onTapped: {
                            compactListView.currentIndex = index;
                            activationTimer.start();
                        }
                    }
                }
                highlight: KSvg.FrameSvgItem {
                    imagePath: "widgets/viewitem"
                    prefix: "hover"
                    width: compactListView.width
                }
                highlightMoveDuration: 0
                highlightResizeDuration: 0
                boundsBehavior: Flickable.StopAtBounds
                Connections {
                    target: tabBox
                    function onCurrentIndexChanged() {compactListView.currentIndex = tabBox.currentIndex;}
                }

                RowLayout {
                    visible: compactListView.count === 0
                    anchors.centerIn: parent
                    spacing: 2 * Kirigami.Units.smallSpacing

                    Kirigami.Icon {
                        source:  "edit-none"
                        Layout.preferredWidth: 28
                        Layout.preferredHeight: 28
                        Layout.leftMargin: hoverItem.margins.left
                    }
                    PlasmaComponents3.Label {
                        id: placeholderLabel
                        horizontalAlignment: Text.AlignLeft
                        verticalAlignment: Text.AlignVCenter
                        text: i18ndc("kwin", "@info:placeholder no entries in the task switcher", "No open windows")
                        textFormat: Text.PlainText
                        Layout.fillWidth: true
                        Layout.rightMargin: hoverItem.margins.right
                        Layout.topMargin: hoverItem.margins.top
                        Layout.bottomMargin: hoverItem.margins.bottom
                    }
                }
            }
            // Top-level overlay: independent of ListView highlight item lifetime.
            Item {
                id: selectionFx
                anchors.fill: compactListView
                z: 100
                enabled: false
                clip: true
                property real strength: 0
                property real sweep: 0
                property bool reported: false
                function trigger() {
                    pulse.stop(); scan.stop(); strength = 0;
                    if (tabBox.visible && Kirigami.Units.shortDuration > 0) {
                        pulse.restart(); scan.restart();
                        if (!reported) {console.info("Jarvis Cinematic HUD runtime pulse " + pulse.duration + " ms, row " + compactListView.rowHeight); reported = true;}
                    }
                }
                Rectangle {
                    x: 0
                    y: compactListView.currentItem ? compactListView.currentItem.y - compactListView.contentY : 0
                    Behavior on y { enabled: tabBox.visible && Kirigami.Units.shortDuration > 0; NumberAnimation { duration: Kirigami.Units.shortDuration; easing.type: Easing.OutCubic } }
                    width: selectionFx.width
                    height: compactListView.rowHeight
                    color: "#184559"
                    border.color: "#93efff"
                    border.width: 2
                    radius: 2
                    opacity: tabBox.visible ? 0.4 + selectionFx.strength * 0.60 : 0
                    Rectangle {
                        x: (parent.width - width) * selectionFx.sweep
                        y: parent.height - height
                        width: parent.width * 0.32
                        height: 3
                        color: "#e2fcff"
                    }
                }
                NumberAnimation { id: pulse; target: selectionFx; property: "strength"; from: 0.95; to: 0; duration: Math.round(Kirigami.Units.longDuration * 1.5) }
                NumberAnimation { id: scan; target: selectionFx; property: "sweep"; from: 0; to: 1; duration: Math.round(Kirigami.Units.longDuration * 1.5) }
                Connections {
                    target: tabBox
                    function onCurrentIndexChanged() { selectionFx.trigger(); }
                    function onVisibleChanged() { selectionFx.trigger(); }
                }
                Component.onCompleted: trigger()
            }
            /*
            * Key navigation on outer item for two reasons:
            * @li we have to emit the change signal
            * @li on multiple invocation it does not work on the list view. Focus seems to be lost.
            **/
            Keys.onPressed: event => {
                if (event.key === Qt.Key_Up || event.key === Qt.Key_Left) {
                    if (compactListView.currentIndex === 0) {
                        compactListView.currentIndex = compactListView.count - 1
                    } else {
                        compactListView.decrementCurrentIndex()
                    }
                } else if (event.key === Qt.Key_Down || event.key === Qt.Key_Right) {
                    if (compactListView.currentIndex === compactListView.count - 1) {
                        compactListView.currentIndex = 0
                    } else {
                        compactListView.incrementCurrentIndex()
                    }
                }
            }
        }

        onSceneGraphError: () => {
            // This slot is intentionally left blank, otherwise QtQuick may post a qFatal() message on a graphics reset.
        }
    }
}
