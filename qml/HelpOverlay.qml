// Full-window key reference: a flat gruvbox panel, one mono font, no
// animations (DESIGN.md, Components). Loaded on demand by Main.qml, same
// pattern as LoginView. The g-chord table is handed in by Main.qml so the
// dispatcher, the which-key popup and this page never drift
// (app/test_helpkeys.py guards it).
import QtQuick

Rectangle {
    id: help
    color: th.bg
    focus: true

    // [["t/T", "next/prev tab"], ...] -- root.gChords in Main.qml.
    property var chords: []
    signal closed()

    // Swallow every key while help is up: no app binding may fire behind
    // it. Close keys are POSITIONAL like the rest of the app (Main.qml,
    // posKey): 24 = physical q, 61 = physical / (so shift+/ is "?").
    Keys.onPressed: (event) => {
        const pos = event.nativeScanCode
        if (event.key === Qt.Key_Escape
                || event.key === Qt.Key_Q || pos === 24
                || event.key === Qt.Key_Question
                || (pos === 61 && (event.modifiers & Qt.ShiftModifier)))
            help.closed()
        event.accepted = true
    }

    component Bind: Row {
        id: bind
        property string k: ""
        property string d: ""
        spacing: 8
        Text {
            width: 88
            text: bind.k
            color: th.fg
            font.pixelSize: th.fontSizeSmall
        }
        Text {
            text: bind.d
            color: th.fgDim
            font.pixelSize: th.fontSizeSmall
        }
    }

    component Group: Column {
        id: group
        property string title: ""
        spacing: 2
        Text {
            text: group.title
            color: th.accent
            font.pixelSize: th.fontSize
            bottomPadding: 4
        }
    }

    Column {
        anchors.fill: parent

        Rectangle {
            width: parent.width
            height: th.barHeight
            color: th.bg1
            Row {
                anchors.fill: parent
                Rectangle {
                    width: helpTag.width + 16
                    height: parent.height
                    color: th.accent
                    Text {
                        id: helpTag
                        anchors.centerIn: parent
                        text: "HELP"
                        color: th.accentFg
                        font.pixelSize: th.fontSize
                    }
                }
                Rectangle { width: 1; height: parent.height; color: th.bg2 }
                Text {
                    height: parent.height
                    verticalAlignment: Text.AlignVCenter
                    leftPadding: 8
                    text: "keys are positional (physical QWERTY spots) · "
                          + "? · esc · q close"
                    color: th.fgDim
                    font.pixelSize: th.fontSizeSmall
                }
            }
        }

        Item {
            width: parent.width
            height: parent.height - th.barHeight

            Row {
                x: 16
                y: 14
                spacing: 40

                Column {
                    spacing: 18

                    Group {
                        title: "Global"
                        Bind { k: "?"; d: "this page" }
                        Bind { k: "n"; d: "toggle autoplay on queue end" }
                        Bind { k: "esc"; d: "browse ⇄ the playing video" }
                    }

                    Group {
                        title: "Browse"
                        Bind { k: "h j k l"; d: "move in the grid (arrows too)" }
                        Bind { k: "gg / G"; d: "first / last cell" }
                        Bind { k: "enter"; d: "play in the active tab" }
                        Bind { k: "t"; d: "play in a new tab" }
                        Bind { k: "p"; d: "play next" }
                        Bind { k: "a"; d: "add to the queue" }
                        Bind { k: "w"; d: "add to watch later" }
                        Bind { k: "d"; d: "remove from the viewed playlist" }
                        Bind { k: "S"; d: "subscribe to the channel" }
                        Bind { k: "/"; d: "search prompt" }
                        Bind { k: "q"; d: "quit" }
                    }

                    Group {
                        title: "Tabs"
                        Bind { k: "1-9"; d: "activate tab n" }
                        Bind { k: "gt / gT"; d: "next / previous tab" }
                        Bind { k: "x"; d: "close the active tab" }
                    }
                }

                Column {
                    spacing: 18

                    Group {
                        title: "Watch"
                        Bind { k: "space"; d: "pause / resume" }
                        Bind { k: "h l"; d: "seek ∓5s (arrows too)" }
                        Bind { k: "j k"; d: "volume ∓5 (arrows too)" }
                        Bind { k: "m"; d: "mute" }
                        Bind { k: "s"; d: "cycle subtitle track" }
                        Bind { k: "f"; d: "fullscreen" }
                        Bind { k: "r"; d: "related panel" }
                        Bind { k: "c"; d: "comments panel" }
                        Bind { k: "b"; d: "save-to-playlist panel" }
                        Bind { k: "u"; d: "queue panel" }
                        Bind { k: "L"; d: "like the video" }
                        Bind { k: "d"; d: "unlike the video" }
                        Bind { k: "C"; d: "write a comment" }
                        Bind { k: "S"; d: "subscribe to the channel" }
                        Bind { k: "gc"; d: "open the channel's feed" }
                    }
                }

                Column {
                    spacing: 18

                    Group {
                        title: "Watch panels (while one is open)"
                        Bind { k: "j k"; d: "move in the panel" }
                        Bind { k: "enter"
                               d: "play · replies · jump · save" }
                        Bind { k: "t a p"
                               d: "related: new tab · queue · play next" }
                        Bind { k: "J K"; d: "queue: move the item" }
                        Bind { k: "d"; d: "queue: remove the item" }
                        Bind { k: "L"; d: "comments: like" }
                        Bind { k: "r c b u"; d: "switch or close the panel" }
                        Bind { k: "esc"; d: "close the panel" }
                    }

                    Group {
                        title: "g-chords (press g, then)"
                        Repeater {
                            model: help.chords
                            Bind {
                                required property var modelData
                                // "t/T" -> "gt / gT"
                                k: "g" + modelData[0].split("/").join(" / g")
                                d: modelData[1]
                            }
                        }
                    }
                }
            }
        }
    }
}
