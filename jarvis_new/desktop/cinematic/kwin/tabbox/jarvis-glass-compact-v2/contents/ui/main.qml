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
    Component.onCompleted: console.info("Jarvis Compact Glass: compact list and electric accents ready")
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

            property int optimalWidth: 30 + textMetrics.width + Kirigami.Units.iconSizes.small + 2 * Kirigami.Units.smallSpacing + hoverItem.margins.right + hoverItem.margins.left
            property int optimalHeight: compactListView.rowHeight * (compactListView.count || 1)
            width: Math.min(Math.max(tabBox.screenGeometry.width * 0.2, optimalWidth), tabBox.screenGeometry.width * 0.8)
            height: Math.min(optimalHeight, tabBox.screenGeometry.height * 0.8)

            // A dark transmissive backing keeps labels legible over white windows.
            Rectangle {
                anchors.fill: parent
                anchors.margins: -4
                radius: 7
                border.color: "#aa020a17"
                border.width: 2
                gradient: Gradient {
                    GradientStop { position: 0; color: "#dc071629" }
                    GradientStop { position: 1; color: "#bb06234a" }
                }
            }
            Rectangle {
                anchors.fill: parent
                anchors.margins: -3
                radius: 6
                color: "transparent"
                border.color: "#996abaff"
            }
            Rectangle {
                x: parent.width - 32; y: -3; width: 24; height: 2
                color: "#bc163b"
            }

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
                property int rowHeight: Math.max(Kirigami.Units.iconSizes.small, textMetrics.height + hoverItem.margins.top + hoverItem.margins.bottom)

                anchors.fill: parent
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
                        Layout.preferredWidth: Kirigami.Units.iconSizes.small
                        Layout.preferredHeight: Kirigami.Units.iconSizes.small
                        Layout.leftMargin: hoverItem.margins.left
                        TapHandler { onTapped: { compactListView.currentIndex = index; activationTimer.start(); } }
                    }
                    PlasmaComponents3.Label {
                        id: captionItem
                        horizontalAlignment: Text.AlignLeft
                        verticalAlignment: Text.AlignBottom
                        text: itemCaption(caption, minimized)
                        textFormat: Text.PlainText
                        color: "#f0f7ff"
                        style: Text.Outline
                        styleColor: "#d9000815"
                        font.weight: index === compactListView.currentIndex ? Font.Bold : Font.Normal
                        elide: Text.ElideMiddle
                        Layout.fillWidth: true
                        Layout.rightMargin: hoverItem.margins.right
                        Layout.topMargin: hoverItem.margins.top
                        Layout.bottomMargin: hoverItem.margins.bottom
                        TapHandler { onTapped: { compactListView.currentIndex = index; activationTimer.start(); } }
                    }
                    PlasmaComponents3.ToolButton {
                        id: closeButton
                        Layout.preferredWidth: 26
                        Layout.preferredHeight: 24
                        Layout.rightMargin: 3
                        enabled: model.closeable
                        Accessible.name: "Close " + captionItem.text
                        text: "×"
                        font.pixelSize: 22
                        palette.buttonText: hovered ? "#fff1f3" : "#ff7892"
                        background: Rectangle {
                            radius: 4
                            color: closeButton.down ? "#ba980b2c" : closeButton.hovered ? "#885b0925" : "#282b0920"
                            border.color: closeButton.hovered ? "#ff5374" : "#66581732"
                            Behavior on color { ColorAnimation { duration: Kirigami.Units.shortDuration } }
                        }
                        onClicked: {
                            activationTimer.stop();
                            tabBox.model.close(index);
                            console.info("Jarvis Compact Glass: close requested for switcher row");
                        }
                    }
                }
                highlight: Rectangle {
                    color: "#123b8bd9"
                    radius: 4
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
                        Layout.preferredWidth: Kirigami.Units.iconSizes.small
                        Layout.preferredHeight: Kirigami.Units.iconSizes.small
                        Layout.leftMargin: hoverItem.margins.left
                    }
                    PlasmaComponents3.Label {
                        id: placeholderLabel
                        horizontalAlignment: Text.AlignLeft
                        verticalAlignment: Text.AlignBottom
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
                        if (!reported) {console.info("Jarvis Compact Glass selection pulse " + pulse.duration + " ms, row " + compactListView.rowHeight); reported = true;}
                    }
                }
                Rectangle {
                    x: 0
                    y: compactListView.currentItem ? compactListView.currentItem.y - compactListView.contentY : 0
                    Behavior on y { enabled: tabBox.visible && Kirigami.Units.shortDuration > 0; NumberAnimation { duration: Kirigami.Units.shortDuration; easing.type: Easing.OutCubic } }
                    width: selectionFx.width
                    height: compactListView.rowHeight
                    color: "#18337ac5"
                    border.color: "#77caff"
                    border.width: 1
                    radius: 5
                    opacity: tabBox.visible ? 0.24 + selectionFx.strength * 0.7 : 0
                    Repeater {
                        model: 4
                        Item {
                            x: index % 2 ? parent.width - 9 : 1
                            y: index < 2 ? 1 : parent.height - 8
                            width: 8; height: 7
                            opacity: 0.4 + selectionFx.strength * 0.6
                            Rectangle { width: 8; height: 1; y: index < 2 ? 0 : 6; color: "#c2efff" }
                            Rectangle { width: 1; height: 7; x: index % 2 ? 7 : 0; color: "#72bfff" }
                        }
                    }
                    Repeater {
                        model: 3
                        Rectangle {
                            x: parent.width * (0.12 + index * 0.27) + Math.floor(selectionFx.sweep * 5) * (index % 2 ? 3 : -3)
                            y: parent.height * (0.25 + index * 0.25)
                            width: 10 + index * 4; height: 1
                            color: "#95d6ff"
                            opacity: Math.floor(selectionFx.sweep * 7) % 2 ? selectionFx.strength * 0.55 : 0
                        }
                    }
                    Rectangle {
                        x: (parent.width - width) * selectionFx.sweep
                        y: parent.height - height
                        width: parent.width * 0.32
                        height: 2
                        color: "#d8f5ff"
                    }
                }
                NumberAnimation { id: pulse; target: selectionFx; property: "strength"; from: 0.95; to: 0; duration: Math.round(Kirigami.Units.longDuration * 1.6) }
                NumberAnimation { id: scan; target: selectionFx; property: "sweep"; from: 0; to: 1; duration: Math.round(Kirigami.Units.longDuration * 1.6) }
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
