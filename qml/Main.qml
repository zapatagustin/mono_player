import QtQuick
import QtQuick.Controls.Basic
import MpvBridge

// TUI-style shell (DESIGN.md): flat gruvbox panels, one mono font, 28px bars,
// vim-grammar keyboard, zero animations. Mouse works but never gates anything.
Window {
    id: root
    width: 1280
    height: 720
    visible: true
    color: th.bg
    title: "mono_player"

    property bool watching: false
    property bool prompting: false
    property string promptKind: "search"  // or "login"
    property string pending: ""           // vim pending key ("g")
    property string statusMsg: ""
    readonly property string mode: prompting ? "PROMPT"
                                 : watching ? "WATCH" : "BROWSE"

    readonly property var tabGlyphs:
        ["一", "二", "三", "四", "五", "六", "七", "八", "九"]

    function notify(msg) {
        statusMsg = msg
        statusClear.restart()
    }
    Timer { id: statusClear; interval: 3000; onTriggered: root.statusMsg = "" }

    function refocus() {
        if (root.prompting) promptField.forceActiveFocus()
        else if (root.watching) playerView.forceActiveFocus()
        else browseKeys.forceActiveFocus()
    }
    onWatchingChanged: refocus()
    onPromptingChanged: refocus()
    Component.onCompleted: refocus()

    // Vim g-prefix dispatcher, shared by browse and watch modes.
    // Returns true when the key was consumed.
    function gKey(event) {
        if (root.pending === "g") {
            root.pending = ""
            switch (event.key) {
            case Qt.Key_G: grid.currentIndex = 0; return true
            case Qt.Key_T: tabs.activate(event.modifiers & Qt.ShiftModifier
                ? (tabs.activeIndex - 1 + strip.count) % Math.max(1, strip.count)
                : (tabs.activeIndex + 1) % Math.max(1, strip.count)); return true
            case Qt.Key_S:
                if (auth.loggedIn) feed.loadSubscriptions()
                else root.notify("login required (gl)")
                root.watching = false; return true
            case Qt.Key_W:
                if (auth.loggedIn) feed.loadWatchLater()
                else root.notify("login required (gl)")
                root.watching = false; return true
            case Qt.Key_L:
                if (!authAvailable) { root.notify("webengine missing"); return true }
                if (auth.loggedIn) { auth.logout(); root.notify("signed out") }
                else { root.promptKind = "login"; root.prompting = true }
                return true
            }
            return true  // unknown g-sequence: swallow, reset
        }
        if (event.key === Qt.Key_G && !(event.modifiers & Qt.ShiftModifier)) {
            root.pending = "g"
            return true
        }
        return false
    }

    function tabKey(event) {
        if (event.key >= Qt.Key_1 && event.key <= Qt.Key_9) {
            tabs.activate(event.key - Qt.Key_1)
            return true
        }
        if (event.key === Qt.Key_X) {
            if (tabs.activeIndex >= 0) tabs.closeTab(tabs.activeIndex)
            if (tabs.activeIndex < 0) root.watching = false
            return true
        }
        return false
    }

    Connections {
        target: tabs
        function onMpvCommand(cmd) { player.command(cmd) }
        function onVideoStarted() {
            root.watching = true
            playerView.loading = true
            playerView.vHeight = 0
            playerView.vFps = 0
            playerView.vFormat = ""
            root.refocus()
        }
    }

    Column {
        anchors.fill: parent

        // Tab bar: DWM-style strip, workspace-glyph indices.
        ListView {
            id: strip
            width: parent.width
            height: count > 0 ? th.barHeight : 0
            orientation: ListView.Horizontal
            model: tabs
            clip: true

            delegate: Rectangle {
                id: tabCell
                required property int index
                required property string title
                required property bool active

                width: 180
                height: th.barHeight
                color: active ? th.accent : th.bg1
                border.color: th.bg2
                border.width: 1

                Text {
                    anchors.fill: parent
                    anchors.leftMargin: 6
                    anchors.rightMargin: 6
                    verticalAlignment: Text.AlignVCenter
                    text: `[${root.tabGlyphs[tabCell.index] ?? tabCell.index + 1}] ${tabCell.title}`
                    color: tabCell.active ? th.accentFg : th.fgDim
                    font.pixelSize: th.fontSize
                    elide: Text.ElideRight
                }
                TapHandler {
                    acceptedButtons: Qt.LeftButton
                    onTapped: { tabs.activate(tabCell.index); root.watching = true }
                }
                TapHandler {
                    acceptedButtons: Qt.MiddleButton
                    onTapped: tabs.closeTab(tabCell.index)
                }
            }
        }

        // Prompt strip: dmenu-style, only present while prompting.
        Rectangle {
            width: parent.width
            height: root.prompting ? th.barHeight : 0
            visible: root.prompting
            color: th.bg
            border.color: th.bg2
            border.width: 1

            Row {
                anchors.fill: parent
                anchors.leftMargin: 8
                spacing: 8
                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    text: root.promptKind === "login" ? "login:" : "/"
                    color: th.accent
                    font.pixelSize: th.fontSize
                }
                TextField {
                    id: promptField
                    width: parent.width - 60
                    anchors.verticalCenter: parent.verticalCenter
                    color: th.fg
                    font.pixelSize: th.fontSize
                    placeholderText: root.promptKind === "login"
                        ? "google account email" : ""
                    placeholderTextColor: th.emptyDim
                    background: null
                    onAccepted: {
                        if (root.promptKind === "login") auth.startLogin(text)
                        else feed.search(text)
                        text = ""
                        root.prompting = false
                    }
                    Keys.onEscapePressed: { text = ""; root.prompting = false }
                }
            }
        }

        Item {
            width: parent.width
            height: parent.height - strip.height
                    - (root.prompting ? th.barHeight : 0) - statusline.height

            // Browse: one global view outside tabs (GUIDELINE.org, UI model).
            Item {
                id: browseKeys
                anchors.fill: parent
                visible: !root.watching

                Keys.onPressed: (event) => {
                    if (root.gKey(event) || root.tabKey(event)) {
                        event.accepted = true
                        return
                    }
                    const cols = Math.max(1, Math.floor(grid.width / grid.cellWidth))
                    const cur = grid.currentItem
                    switch (event.key) {
                    case Qt.Key_H: case Qt.Key_Left:
                        grid.currentIndex = Math.max(0, grid.currentIndex - 1); break
                    case Qt.Key_L: case Qt.Key_Right:
                        grid.currentIndex = Math.min(grid.count - 1, grid.currentIndex + 1); break
                    case Qt.Key_J: case Qt.Key_Down:
                        grid.currentIndex = Math.min(grid.count - 1, grid.currentIndex + cols); break
                    case Qt.Key_K: case Qt.Key_Up:
                        grid.currentIndex = Math.max(0, grid.currentIndex - cols); break
                    case Qt.Key_G:  // shift+g (plain g handled by gKey)
                        grid.currentIndex = grid.count - 1; break
                    case Qt.Key_Return: case Qt.Key_Enter:
                        if (cur) tabs.playVideo(cur.videoId, cur.title); break
                    case Qt.Key_T:
                        if (cur) { tabs.openInNewTab(cur.videoId, cur.title)
                                   root.notify("opened in new tab") } break
                    case Qt.Key_P:
                        if (cur) { tabs.playNext(cur.videoId, cur.title)
                                   root.notify("playing next") } break
                    case Qt.Key_A:
                        if (cur) { tabs.enqueue(cur.videoId, cur.title)
                                   root.notify("queued") } break
                    case Qt.Key_W:
                        if (!cur) break
                        if (auth.loggedIn) { feed.addToWatchLater(cur.videoId)
                                             root.notify("added to watch later") }
                        else root.notify("login required (gl)")
                        break
                    case Qt.Key_Slash:
                        root.promptKind = "search"; root.prompting = true; break
                    case Qt.Key_Q:
                        Qt.quit(); break
                    default:
                        return  // not ours: leave event.accepted false
                    }
                    event.accepted = true
                }

                GridView {
                    id: grid
                    anchors.fill: parent
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

                        // Selection frame: the system-wide active mark.
                        // ecomono: Rectangle gradients are H/V only; the
                        // config's 45deg needs a ShaderEffect - not worth it.
                        Rectangle {
                            anchors.fill: parent
                            anchors.margins: 3
                            visible: cell.GridView.isCurrentItem
                            gradient: Gradient {
                                orientation: Gradient.Horizontal
                                GradientStop { position: 0; color: th.accent }
                                GradientStop { position: 1; color: th.accent2 }
                            }
                        }
                        Rectangle {
                            anchors.fill: parent
                            anchors.margins: cell.GridView.isCurrentItem ? 4 : 3
                            color: th.bg
                            border.color: cell.GridView.isCurrentItem
                                ? "transparent" : th.bg2
                            border.width: 1

                            Column {
                                anchors.fill: parent
                                anchors.margins: 4
                                spacing: 3

                                Rectangle {
                                    width: parent.width
                                    height: 180
                                    color: th.bg1
                                    Image {
                                        anchors.fill: parent
                                        asynchronous: true
                                        sourceSize.width: 320
                                        fillMode: Image.PreserveAspectCrop
                                        source: cell.thumb
                                    }
                                }
                                Text {
                                    width: parent.width
                                    text: cell.title
                                    color: th.fg
                                    font.pixelSize: th.fontSize
                                    elide: Text.ElideRight
                                    maximumLineCount: 2
                                    wrapMode: Text.WordWrap
                                }
                                Text {
                                    width: parent.width
                                    text: cell.channel
                                          + (cell.duration ? " · " + cell.duration : "")
                                    color: th.fgDim
                                    font.pixelSize: th.fontSizeSmall
                                    elide: Text.ElideRight
                                }
                            }
                        }
                        required property int index
                        TapHandler {
                            acceptedButtons: Qt.LeftButton
                            onTapped: {
                                grid.currentIndex = cell.index
                                tabs.playVideo(cell.videoId, cell.title)
                            }
                        }
                        TapHandler {
                            acceptedButtons: Qt.RightButton
                            onTapped: { grid.currentIndex = cell.index; cellMenu.popup() }
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
                property string decode: ""
                property bool loading: false
                property int volume: 100
                property int vHeight: 0
                property real vFps: 0
                property string vFormat: ""
                property string subLang: ""
                readonly property string quality: vHeight > 0
                    ? vHeight + "p" + (vFps > 0 ? Math.round(vFps) : "")
                      + (vFormat !== "" ? " " + vFormat : "")
                    : ""

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
                        // ecomono: VP9-first. AV1 + zero-copy segfaults in the
                        // iHD driver's Av1Pipeline on decoder teardown (repro:
                        // main.py --stress). Re-prefer AV1 when a driver
                        // update survives that stress run.
                        player.setProperty("ytdl-format",
                            "bv*[vcodec^=vp9][height<=?4320]+ba/bv*+ba/b")
                        // Ask yt-dlp for subtitle tracks, else ytdl_hook adds
                        // none and the subs key has nothing to cycle.
                        player.setProperty("ytdl-raw-options",
                            'sub-langs="es.*,en.*",write-subs=')
                        // Runtime UI state arrives via async observers only —
                        // synchronous getProperty during load deadlocks.
                        player.observe("stream-open-filename")
                        player.observe("volume")
                        player.observe("video-params")
                        player.observe("container-fps")
                        player.observe("video-format")
                        player.observe("current-tracks/sub/lang")
                    }

                    onEndFile: (error) => {
                        if (error) {
                            root.notify("load failed — retrying")
                            tabs.loadFailed()
                        }
                    }

                    onPropertyChanged: (name, value) => {
                        switch (name) {
                        case "stream-open-filename":
                            if (value) tabs.resolvedUrl(value)
                            break
                        case "volume":
                            if (value !== undefined && value !== null)
                                playerView.volume = Math.round(value)
                            break
                        case "video-params":
                            playerView.vHeight = value && value.h ? value.h : 0
                            break
                        case "container-fps":
                            playerView.vFps = value ?? 0; break
                        case "video-format":
                            playerView.vFormat = value ?? ""; break
                        case "current-tracks/sub/lang":
                            playerView.subLang = value ?? ""; break
                        }
                    }

                    onPlaybackTimeChanged: (secs) => {
                        tabs.playbackTime(secs)
                        playerView.position = secs
                        playerView.loading = false
                    }
                    onPlaylistPosChanged: (pos) => tabs.playlistPos(pos)
                    onDurationChanged: (secs) => playerView.duration = secs
                    onPauseChanged: (p) => playerView.paused = p
                    onLogMessage: (prefix, level, text) => {
                        // vd info is low-volume and carries the hw/sw
                        // decode decision — always worth surfacing.
                        if (level === "error" || level === "warn" || prefix === "vd")
                            console.log(`[${prefix}] ${level}: ${text}`)
                        if (prefix === "vd" && text.indexOf("hardware decoding") >= 0)
                            playerView.decode = (text.match(/\((.+)\)/) ?? [,"hw"])[1]
                        else if (prefix === "vd" && text.indexOf("software decoding") >= 0)
                            playerView.decode = "sw"
                    }
                }

                function bumpVolume(delta) {
                    player.command(["add", "volume", delta])
                    // display updates via the volume observer
                }

                Keys.onPressed: (event) => {
                    if (root.gKey(event) || root.tabKey(event)) {
                        event.accepted = true
                        return
                    }
                    switch (event.key) {
                    case Qt.Key_Space: player.command(["cycle", "pause"]); break
                    case Qt.Key_H: case Qt.Key_Left:
                        player.command(["seek", -5]); break
                    case Qt.Key_L: case Qt.Key_Right:
                        player.command(["seek", 5]); break
                    case Qt.Key_K: case Qt.Key_Up: bumpVolume(5); break
                    case Qt.Key_J: case Qt.Key_Down: bumpVolume(-5); break
                    case Qt.Key_M: player.command(["cycle", "mute"]); break
                    case Qt.Key_S:
                        player.command(["cycle", "sub"])
                        break
                    case Qt.Key_F:
                        root.visibility = root.visibility === Window.FullScreen
                            ? Window.Windowed : Window.FullScreen
                        break
                    case Qt.Key_Escape: root.watching = false; break
                    default:
                        return
                    }
                    event.accepted = true
                }

                // Mouse never gates: click toggles pause, wheel seeks.
                TapHandler {
                    onTapped: player.command(["cycle", "pause"])
                }
            }
        }

        // Statusline: always visible, the TUI way. Mode | title | segments.
        Rectangle {
            id: statusline
            width: parent.width
            height: th.barHeight
            color: th.bg1

            Row {
                anchors.fill: parent
                spacing: 0

                Rectangle {
                    width: modeTag.width + 16
                    height: parent.height
                    color: th.accent
                    Text {
                        id: modeTag
                        anchors.centerIn: parent
                        text: root.mode
                        color: th.accentFg
                        font.pixelSize: th.fontSize
                    }
                }
                Rectangle { width: 1; height: parent.height; color: th.bg2 }

                Text {
                    width: parent.width - x - rightSegments.width
                    height: parent.height
                    verticalAlignment: Text.AlignVCenter
                    leftPadding: 8
                    text: root.statusMsg !== "" ? root.statusMsg
                        : root.watching && playerView.loading ? "loading…"
                        : root.watching
                        ? "space pause · h/l seek · j/k vol · s subs · m mute · f full · gt/1-9 tab · x close · esc back"
                        : "hjkl move · enter play · t tab · a queue · p next · w later · / search · gt/1-9 tab · gs subs · gw later · gl login · q quit"
                    color: root.statusMsg !== "" || (root.watching && playerView.loading)
                        ? th.fg : th.fgDim
                    font.pixelSize: th.fontSizeSmall
                    elide: Text.ElideRight
                }

                Row {
                    id: rightSegments
                    height: parent.height
                    visible: root.watching

                    Rectangle { width: 1; height: parent.height; color: th.bg2 }
                    Item {
                        width: 158
                        height: parent.height
                        Rectangle {
                            anchors.verticalCenter: parent.verticalCenter
                            x: 4
                            width: 150
                            height: 6
                            color: th.bg2
                            Rectangle {
                                width: playerView.duration > 0
                                    ? parent.width * Math.min(1, playerView.position / playerView.duration)
                                    : 0
                                height: parent.height
                                color: th.accent
                            }
                        }
                    }
                    Rectangle { width: 1; height: parent.height; color: th.bg2 }
                    Text {
                        height: parent.height
                        verticalAlignment: Text.AlignVCenter
                        leftPadding: 8
                        rightPadding: 8
                        text: playerView.fmt(playerView.position) + " / "
                              + playerView.fmt(playerView.duration)
                        color: th.fg
                        font.pixelSize: th.fontSizeSmall
                    }
                    Rectangle { width: 1; height: parent.height; color: th.bg2 }
                    Text {
                        height: parent.height
                        verticalAlignment: Text.AlignVCenter
                        leftPadding: 8
                        rightPadding: 8
                        text: (playerView.paused ? "||" : "|>")
                              + `  vol ${playerView.volume}`
                        color: th.fgDim
                        font.pixelSize: th.fontSizeSmall
                    }
                    Rectangle { width: 1; height: parent.height; color: th.bg2 }
                    Text {
                        height: parent.height
                        verticalAlignment: Text.AlignVCenter
                        leftPadding: 8
                        rightPadding: 8
                        text: playerView.quality
                        color: th.fgDim
                        font.pixelSize: th.fontSizeSmall
                        visible: playerView.quality !== ""
                    }
                    Rectangle { width: 1; height: parent.height; color: th.bg2 }
                    Text {
                        height: parent.height
                        verticalAlignment: Text.AlignVCenter
                        leftPadding: 8
                        rightPadding: 8
                        text: "sub " + playerView.subLang
                        color: th.fgDim
                        font.pixelSize: th.fontSizeSmall
                        visible: playerView.subLang !== ""
                    }
                    Rectangle { width: 1; height: parent.height; color: th.bg2 }
                    Text {
                        height: parent.height
                        verticalAlignment: Text.AlignVCenter
                        leftPadding: 8
                        rightPadding: 8
                        text: playerView.decode
                        color: playerView.decode === "sw" ? th.red : th.green
                        font.pixelSize: th.fontSizeSmall
                        visible: playerView.decode !== ""
                    }
                }
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
