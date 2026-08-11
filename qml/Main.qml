import QtQuick
import QtQuick.Controls.Basic
import MpvBridge

Window {
    id: root
    width: 1280
    height: 720
    visible: true
    color: "#111111"
    title: "mono_player"

    property bool watching: false

    Connections {
        target: tabs
        function onMpvCommand(cmd) { player.command(cmd) }
        function onVideoStarted() {
            root.watching = true
            playerView.forceActiveFocus()
        }
    }

    Column {
        anchors.fill: parent

        // Tab strip: browser-style, always visible while tabs exist.
        ListView {
            id: strip
            width: parent.width
            height: count > 0 ? 32 : 0
            orientation: ListView.Horizontal
            model: tabs
            clip: true

            delegate: Rectangle {
                id: tabCell
                required property int index
                required property string title
                required property bool active

                width: 180
                height: 32
                color: active ? "#333333" : "#1a1a1a"
                border.color: "#444444"

                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.left: parent.left
                    anchors.leftMargin: 8
                    anchors.right: closeButton.left
                    text: tabCell.title
                    color: tabCell.active ? "white" : "#aaaaaa"
                    font.pixelSize: 12
                    elide: Text.ElideRight
                }
                TapHandler {
                    onTapped: {
                        tabs.activate(tabCell.index)
                        root.watching = true
                    }
                }
                Text {
                    id: closeButton
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    width: 24
                    text: "×"
                    color: "#888888"
                    font.pixelSize: 14
                    horizontalAlignment: Text.AlignHCenter
                    TapHandler {
                        onTapped: {
                            tabs.closeTab(tabCell.index)
                            if (tabs.activeIndex < 0)
                                root.watching = false
                        }
                    }
                }
            }
        }

        Item {
            width: parent.width
            height: parent.height - strip.height

            // Browse: one global view outside tabs (GUIDELINE.org, UI model).
            Column {
                anchors.fill: parent
                visible: !root.watching

                Row {
                    id: topBar
                    width: parent.width
                    height: 36

                    TextField {
                        id: searchField
                        width: parent.width - accountBar.width
                        height: parent.height
                        placeholderText: "Search YouTube"
                        color: "white"
                        background: Rectangle { color: "#222222" }
                        onAccepted: feed.search(text)
                    }

                    // Account: optional by design -- everything except sync
                    // works logged out (GUIDELINE.org, SECURITY).
                    Row {
                        id: accountBar
                        height: parent.height
                        visible: authAvailable

                        TextField {
                            id: emailField
                            width: 180
                            height: parent.height
                            visible: !auth.loggedIn
                            placeholderText: "Google email"
                            color: "white"
                            background: Rectangle { color: "#1a1a1a" }
                        }
                        Button {
                            height: parent.height
                            visible: !auth.loggedIn
                            text: "Sign in"
                            onClicked: auth.startLogin(emailField.text)
                        }
                        Button {
                            height: parent.height
                            visible: auth.loggedIn
                            text: "Subscriptions"
                            onClicked: feed.loadSubscriptions()
                        }
                        Button {
                            height: parent.height
                            visible: auth.loggedIn
                            text: "Watch later"
                            onClicked: feed.loadWatchLater()
                        }
                        Button {
                            height: parent.height
                            visible: auth.loggedIn
                            text: "Sign out"
                            onClicked: auth.logout()
                        }
                    }
                }

                GridView {
                    id: grid
                    width: parent.width
                    height: parent.height - topBar.height
                    cellWidth: 336
                    cellHeight: 264
                    cacheBuffer: 600
                    clip: true
                    model: feed

                    delegate: Item {
                        id: cell
                        required property string videoId
                        required property string title
                        required property string channel
                        required property string duration
                        required property string thumb

                        width: grid.cellWidth
                        height: grid.cellHeight

                        Component.onCompleted: feed.requestThumb(videoId)

                        Column {
                            width: 320
                            anchors.horizontalCenter: parent.horizontalCenter
                            spacing: 4

                            Rectangle {
                                width: 320
                                height: 180
                                color: "#222222"
                                Image {
                                    anchors.fill: parent
                                    asynchronous: true
                                    sourceSize.width: 320
                                    fillMode: Image.PreserveAspectCrop
                                    source: cell.thumb
                                }
                            }
                            Text {
                                width: 320
                                text: cell.title
                                color: "white"
                                font.pixelSize: 14
                                elide: Text.ElideRight
                                maximumLineCount: 2
                                wrapMode: Text.WordWrap
                            }
                            Text {
                                width: 320
                                text: cell.channel + (cell.duration ? "  ·  " + cell.duration : "")
                                color: "#aaaaaa"
                                font.pixelSize: 12
                                elide: Text.ElideRight
                            }
                        }
                        TapHandler {
                            acceptedButtons: Qt.LeftButton
                            onTapped: tabs.playVideo(cell.videoId, cell.title)
                        }
                        TapHandler {
                            acceptedButtons: Qt.RightButton
                            onTapped: cellMenu.popup()
                        }
                        Menu {
                            id: cellMenu
                            MenuItem {
                                text: "Open in new tab"
                                onTriggered: tabs.openInNewTab(cell.videoId, cell.title)
                            }
                            MenuItem {
                                text: "Enqueue"
                                onTriggered: tabs.enqueue(cell.videoId, cell.title)
                            }
                            MenuItem {
                                text: "Play next"
                                onTriggered: tabs.playNext(cell.videoId, cell.title)
                            }
                            MenuItem {
                                text: "Watch later"
                                enabled: authAvailable && auth.loggedIn
                                onTriggered: feed.addToWatchLater(cell.videoId)
                            }
                        }
                    }
                }
            }

            Item {
                id: playerView
                anchors.fill: parent
                visible: root.watching

                property real position: 0
                property real duration: 0
                property bool paused: false

                function fmt(s) {
                    s = Math.max(0, Math.floor(s))
                    const h = Math.floor(s / 3600)
                    const m = Math.floor(s % 3600 / 60)
                    const sec = String(s % 60).padStart(2, "0")
                    return h > 0
                        ? `${h}:${String(m).padStart(2, "0")}:${sec}`
                        : `${m}:${sec}`
                }

                MpvObject {
                    id: player
                    anchors.fill: parent

                    Component.onCompleted: {
                        // ecomono: hardcoded AV1 format string, valid for this
                        // machine's verified VA-API caps; the format-policy
                        // milestone probes at startup instead.
                        player.setProperty("ytdl-format",
                            "bv*[vcodec^=av01][height<=?4320]+ba/bv*[vcodec^=vp9]+ba/b")
                    }

                    onPlaybackTimeChanged: (secs) => {
                        tabs.playbackTime(secs)
                        playerView.position = secs
                    }
                    onPlaylistPosChanged: (pos) => tabs.playlistPos(pos)
                    onDurationChanged: (secs) => playerView.duration = secs
                    onPauseChanged: (p) => playerView.paused = p
                    onLogMessage: (prefix, level, text) => {
                        // vd info is low-volume and carries the hw/sw
                        // decode decision — always worth surfacing.
                        if (level === "error" || level === "warn" || prefix === "vd")
                            console.log(`[${prefix}] ${level}: ${text}`)
                    }
                }

                // Controls overlay: shown on mouse movement, fades out after
                // a moment of stillness.
                HoverHandler {
                    onPointChanged: controls.poke()
                }

                Rectangle {
                    id: controls
                    anchors.bottom: parent.bottom
                    width: parent.width
                    height: 48
                    color: "#cc111111"
                    visible: opacity > 0
                    opacity: 0

                    function poke() {
                        opacity = 1
                        hideTimer.restart()
                    }
                    Behavior on opacity { NumberAnimation { duration: 200 } }
                    Timer {
                        id: hideTimer
                        interval: 2500
                        onTriggered: if (!seekBar.pressed) controls.opacity = 0
                    }

                    Row {
                        anchors.fill: parent
                        anchors.leftMargin: 12
                        anchors.rightMargin: 12
                        spacing: 12

                        Text {
                            width: 24
                            anchors.verticalCenter: parent.verticalCenter
                            text: playerView.paused ? "▶" : "⏸"
                            color: "white"
                            font.pixelSize: 18
                            TapHandler {
                                onTapped: player.command(["cycle", "pause"])
                            }
                        }
                        Slider {
                            id: seekBar
                            width: parent.width - 24 - 110 - 2 * parent.spacing
                            anchors.verticalCenter: parent.verticalCenter
                            from: 0
                            to: Math.max(playerView.duration, 0.1)
                            value: pressed ? value : playerView.position
                            onMoved: player.command(["seek", value, "absolute"])
                        }
                        Text {
                            width: 110
                            anchors.verticalCenter: parent.verticalCenter
                            text: playerView.fmt(playerView.position) + " / "
                                  + playerView.fmt(playerView.duration)
                            color: "#cccccc"
                            font.pixelSize: 12
                            horizontalAlignment: Text.AlignRight
                        }
                    }
                }

                Keys.onSpacePressed: player.command(["cycle", "pause"])
                Keys.onLeftPressed: player.command(["seek", -5])
                Keys.onRightPressed: player.command(["seek", 5])
                Keys.onUpPressed: player.command(["add", "volume", 5])
                Keys.onDownPressed: player.command(["add", "volume", -5])
                Keys.onPressed: (event) => {
                    if (event.key === Qt.Key_M) {
                        player.command(["cycle", "mute"])
                        event.accepted = true
                    } else if (event.key === Qt.Key_F) {
                        root.visibility = root.visibility === Window.FullScreen
                            ? Window.Windowed : Window.FullScreen
                        event.accepted = true
                    }
                }
                // Back to browse; the tab keeps playing, browser-style.
                Keys.onEscapePressed: root.watching = false
            }
        }
    }

    // Login webview: exists only while auth.showLogin (loaded on demand,
    // torn down after -- Chromium never runs during browse/playback).
    Loader {
        anchors.fill: parent
        z: 10
        active: authAvailable && auth.showLogin
        source: "LoginView.qml"
    }
}
