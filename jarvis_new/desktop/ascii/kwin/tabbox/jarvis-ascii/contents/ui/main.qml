/*
 Jarvis ASCII window switcher.
 SPDX-License-Identifier: GPL-2.0-or-later
 Selected window: live thumbnail that resolves out of a cyan glyph mosaic,
 caption that decodes from scrambled characters. Rows are plain terminal text.
 */
import QtQuick
import QtQuick.Layouts
import org.kde.plasma.core as PlasmaCore
import org.kde.kirigami as Kirigami
import org.kde.kwin as KWin

KWin.TabBoxSwitcher {
    id: tabBox
    currentIndex: list.currentIndex

    readonly property color cyan: "#5fe3ff"
    readonly property color ice: "#d8f8ff"
    readonly property color amber: "#ffb347"
    readonly property color dim: "#12546a"
    readonly property color ink: "#e6020a10"
    readonly property string glyphs: "▓▒░#%&@$01<>/\\=+*:."
    readonly property string mono: "monospace"
    readonly property bool motion: Kirigami.Units.shortDuration > 0

    function scramble(target, p) {
        var n = Math.floor(target.length * p), out = target.substring(0, n);
        for (var i = n; i < target.length; ++i)
            out += target[i] === " " ? " " : glyphs[Math.floor(Math.random() * glyphs.length)];
        return out;
    }
    function pad(n) { return (n < 10 ? "0" : "") + n; }

    Timer {
        id: activationTimer
        interval: 90
        onTriggered: tabBox.model.activate(list.currentIndex)
    }

    PlasmaCore.Dialog {
        id: dialog
        location: PlasmaCore.Types.Floating
        visible: tabBox.visible
        flags: Qt.Popup | Qt.X11BypassWindowManagerHint
        backgroundHints: PlasmaCore.Types.NoBackground
        x: tabBox.screenGeometry.x + (tabBox.screenGeometry.width - root.width) / 2
        y: tabBox.screenGeometry.y + (tabBox.screenGeometry.height - root.height) / 2

        mainItem: Item {
            id: root
            width: Math.min(tabBox.screenGeometry.width * 0.62, 980)
            readonly property int stageH: list.count > 0 ? Math.min(tabBox.screenGeometry.height * 0.36, 360) : 0
            readonly property int listH: Math.max(1, Math.min(list.count, 8)) * 26
            height: Math.min(tabBox.screenGeometry.height * 0.8, 32 + 24 + stageH + listH + 18 + 3 * 8)

            property real boot: 0
            property real sel: 1
            Component.onCompleted: if (tabBox.visible) { bootAnim.restart(); selAnim.restart(); }
            NumberAnimation { id: bootAnim; target: root; property: "boot"; from: 0; to: 1; duration: tabBox.motion ? 180 : 0; easing.type: Easing.OutCubic }
            NumberAnimation { id: selAnim; target: root; property: "sel"; from: 0; to: 1; duration: tabBox.motion ? 200 : 0; easing.type: Easing.OutCubic }
            Connections {
                target: tabBox
                function onVisibleChanged() { if (tabBox.visible) { bootAnim.restart(); selAnim.restart(); } }
                function onCurrentIndexChanged() { list.currentIndex = tabBox.currentIndex; selAnim.restart(); }
            }

            // ink panel with a scanline and ascii frame; grows open on boot
            Rectangle {
                anchors.centerIn: parent
                width: parent.width
                height: parent.height * (0.12 + 0.88 * root.boot)
                color: tabBox.ink
                border.color: tabBox.dim
                border.width: 1
                clip: true
                Rectangle {
                    width: parent.width; height: 1
                    y: (parent.height - 1) * root.sel
                    color: tabBox.cyan
                    opacity: (1 - root.sel) * 0.9
                }
            }
            Repeater {
                model: 4
                Text {
                    text: ["╭──", "──╮", "╰──", "──╯"][index]
                    color: tabBox.cyan
                    font.family: tabBox.mono
                    font.pixelSize: 14
                    x: index % 2 ? root.width - width + 1 : -1
                    y: index < 2 ? -6 : root.height - height + 6
                    opacity: root.boot
                }
            }

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 16
                spacing: 8
                opacity: root.boot

                RowLayout {
                    id: header
                    Layout.fillWidth: true
                    Text { text: "◤ J.A.R.V.I.S"; color: tabBox.ice; font.family: tabBox.mono; font.pixelSize: 15; font.bold: true }
                    Text { text: "// WINDOW SWITCH"; color: tabBox.amber; font.family: tabBox.mono; font.pixelSize: 15 }
                    Item { Layout.fillWidth: true }
                    Text {
                        text: "[" + tabBox.pad(list.currentIndex + 1) + "/" + tabBox.pad(list.count) + "] ◥"
                        color: tabBox.cyan; font.family: tabBox.mono; font.pixelSize: 15
                    }
                }

                // live thumbnail of the selection, resolving from glyphs
                Item {
                    id: stage
                    Layout.fillWidth: true
                    Layout.preferredHeight: root.stageH
                    visible: list.count > 0

                    KWin.WindowThumbnail {
                        id: thumb
                        anchors.fill: parent
                        anchors.margins: 6
                        wId: list.currentItem ? list.currentItem.wid : ""
                        opacity: 0.25 + 0.75 * root.sel
                    }
                    Canvas {
                        id: mosaic
                        anchors.fill: thumb
                        renderStrategy: Canvas.Cooperative
                        property real p: root.sel
                        onPChanged: requestPaint()
                        onPaint: {
                            var ctx = getContext("2d");
                            ctx.clearRect(0, 0, width, height);
                            if (p >= 0.999) return;
                            var cw = 10, ch = 17, cols = Math.ceil(width / cw), rows = Math.ceil(height / ch);
                            ctx.font = "13px monospace";
                            ctx.textBaseline = "top";
                            for (var y = 0; y < rows; ++y) {
                                for (var x = 0; x < cols; ++x) {
                                    var h = Math.abs(Math.sin(x * 12.9898 + y * 78.233) * 43758.5453) % 1;
                                    var order = (y / rows) * 0.7 + h * 0.3;
                                    var lp = Math.max(0, Math.min(1, (p * 1.35 - order) / 0.35));
                                    if (lp >= 1) continue;
                                    ctx.fillStyle = "rgba(2,9,15," + (0.85 * (1 - lp)).toFixed(3) + ")";
                                    ctx.fillRect(x * cw, y * ch, cw, ch);
                                    var flash = lp > 0 ? 1 - lp : 0.35;
                                    ctx.fillStyle = "rgba(" + Math.round(95 + 155 * flash) + "," + Math.round(227 + 25 * flash) + ",255," + (1 - lp).toFixed(3) + ")";
                                    ctx.fillText(tabBox.glyphs[Math.floor(Math.random() * tabBox.glyphs.length)], x * cw + 1, y * ch + 1);
                                }
                            }
                        }
                    }
                    Repeater {
                        model: 4
                        Text {
                            text: ["┌", "┐", "└", "┘"][index]
                            color: tabBox.amber; font.family: tabBox.mono; font.pixelSize: 18
                            x: index % 2 ? stage.width - width : 0
                            y: index < 2 ? 0 : stage.height - height
                        }
                    }
                    Text {
                        anchors.horizontalCenter: parent.horizontalCenter
                        anchors.bottom: parent.bottom
                        anchors.bottomMargin: -4
                        text: list.currentItem ? tabBox.scramble(list.currentItem.label, root.sel) : ""
                        color: tabBox.ice
                        style: Text.Outline; styleColor: "#02090f"
                        font.family: tabBox.mono; font.pixelSize: 16; font.bold: true
                        width: Math.min(implicitWidth, stage.width - 40)
                        elide: Text.ElideMiddle
                    }
                }

                ListView {
                    id: list
                    Layout.fillWidth: true
                    Layout.preferredHeight: root.listH
                    clip: true
                    focus: true
                    model: tabBox.model
                    highlightMoveDuration: 0
                    boundsBehavior: Flickable.StopAtBounds
                    delegate: Item {
                        id: row
                        required property int index
                        required property var model
                        readonly property string label: (model.minimized ? "(" + model.caption + ")" : model.caption) || ""
                        readonly property var wid: model.windowId
                        readonly property bool current: index === list.currentIndex
                        width: list.width
                        height: 26
                        Rectangle {
                            anchors.fill: parent
                            color: row.current ? "#1c5fe3ff" : "transparent"
                            border.color: row.current ? tabBox.cyan : "transparent"
                            border.width: 1
                        }
                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 8
                            anchors.rightMargin: 8
                            spacing: 8
                            Text {
                                text: row.current ? "▸" : "·"
                                color: row.current ? tabBox.amber : tabBox.dim
                                font.family: tabBox.mono; font.pixelSize: 14
                            }
                            Text {
                                text: tabBox.pad(row.index + 1)
                                color: tabBox.amber; font.family: tabBox.mono; font.pixelSize: 13
                            }
                            Kirigami.Icon {
                                source: row.model.icon
                                Layout.preferredWidth: 16; Layout.preferredHeight: 16
                                opacity: row.model.minimized ? 0.55 : 1
                            }
                            Text {
                                Layout.fillWidth: true
                                text: row.current ? tabBox.scramble(row.label, root.sel) : row.label
                                color: row.current ? tabBox.ice : (row.model.minimized ? "#7fa6b8" : "#a9d8e6")
                                font.family: tabBox.mono; font.pixelSize: 14; font.bold: row.current
                                elide: Text.ElideMiddle
                            }
                            Text {
                                text: "[×]"
                                visible: row.model.closeable
                                color: closeTap.hovered ? "#ff4d5e" : tabBox.dim
                                font.family: tabBox.mono; font.pixelSize: 13
                                HoverHandler { id: closeTap }
                                TapHandler { onTapped: { activationTimer.stop(); tabBox.model.close(row.index); } }
                            }
                        }
                        TapHandler { onTapped: { list.currentIndex = row.index; activationTimer.start(); } }
                    }
                    Text {
                        visible: list.count === 0
                        anchors.centerIn: parent
                        text: "NO OPEN WINDOWS, SIR."
                        color: tabBox.cyan; font.family: tabBox.mono; font.pixelSize: 14
                    }
                }

                Text {
                    id: footer
                    Layout.alignment: Qt.AlignHCenter
                    text: "TAB ▸ NEXT    SHIFT+TAB ◂ PREV    RELEASE ▸ ENGAGE"
                    color: "#7fa6b8"; font.family: tabBox.mono; font.pixelSize: 12
                }
            }

            Keys.onPressed: event => {
                if (event.key === Qt.Key_Up || event.key === Qt.Key_Left) {
                    list.currentIndex = list.currentIndex === 0 ? list.count - 1 : list.currentIndex - 1;
                } else if (event.key === Qt.Key_Down || event.key === Qt.Key_Right) {
                    list.currentIndex = list.currentIndex === list.count - 1 ? 0 : list.currentIndex + 1;
                }
            }
        }

        onSceneGraphError: () => {}
    }
}
