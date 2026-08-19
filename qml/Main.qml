import QtQuick
import QtQuick.Controls.Basic
import MpvBridge

// TUI-style shell (DESIGN.md): flat gruvbox panels, one mono font, 28px bars,
// vim-grammar keyboard, zero animations. Tabs are browser-grade: a pool of
// live paused players (TabManager caps and freezes them), so switching is
// show/pause, not reload.
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

    // Bindings are POSITIONAL (physical QWERTY spots), independent of the
    // active keyboard layout — same policy as the user's Hyprland setup:
    // hjkl stay put under dvorak & friends. nativeScanCode is the evdev
    // keycode + 8 (the historic X offset, preserved by Qt on Wayland/X11);
    // unknown scancodes (Return, Esc, arrows, other platforms) fall back
    // to the layout keysym. Prompt TEXT entry is untouched: TextFields
    // never route through these handlers.
    readonly property var scanKey: ({
        10: Qt.Key_1, 11: Qt.Key_2, 12: Qt.Key_3, 13: Qt.Key_4,
        14: Qt.Key_5, 15: Qt.Key_6, 16: Qt.Key_7, 17: Qt.Key_8,
        18: Qt.Key_9, 19: Qt.Key_0,
        24: Qt.Key_Q, 25: Qt.Key_W, 26: Qt.Key_E, 27: Qt.Key_R,
        28: Qt.Key_T, 29: Qt.Key_Y, 30: Qt.Key_U, 31: Qt.Key_I,
        32: Qt.Key_O, 33: Qt.Key_P,
        38: Qt.Key_A, 39: Qt.Key_S, 40: Qt.Key_D, 41: Qt.Key_F,
        42: Qt.Key_G, 43: Qt.Key_H, 44: Qt.Key_J, 45: Qt.Key_K,
        46: Qt.Key_L,
        52: Qt.Key_Z, 53: Qt.Key_X, 54: Qt.Key_C, 55: Qt.Key_V,
        56: Qt.Key_B, 57: Qt.Key_N, 58: Qt.Key_M, 61: Qt.Key_Slash,
    })
    function posKey(event) {
        const k = scanKey[event.nativeScanCode]
        return k !== undefined ? k : event.key
    }

    // Vim g-prefix dispatcher, shared by browse and watch modes.
    // Returns true when the key was consumed.
    function gKey(event) {
        const key = posKey(event)
        if (root.pending === "g") {
            root.pending = ""
            switch (key) {
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
            case Qt.Key_H:
                if (auth.loggedIn) feed.loadHome()
                else root.notify("login required (gl)")
                root.watching = false; return true
            case Qt.Key_Y:
                if (auth.loggedIn) feed.loadHistory()
                else root.notify("login required (gl)")
                root.watching = false; return true
            case Qt.Key_P:
                if (auth.loggedIn) feed.loadPlaylists()
                else root.notify("login required (gl)")
                root.watching = false; return true
            case Qt.Key_A:
                if (!auth.loggedIn) { root.notify("login required (gl)"); return true }
                if (event.modifiers & Qt.ShiftModifier) auth.cycleAccount()
                else auth.cycleChannel()
                return true
            case Qt.Key_L:
                if (!authAvailable) { root.notify("webengine missing"); return true }
                if (event.modifiers & Qt.ShiftModifier) {
                    // gL: add another Google account (login while logged in)
                    root.promptKind = "login"; root.prompting = true
                } else if (auth.loggedIn) {
                    auth.logout()
                    root.notify(auth.loggedIn
                        ? "signed out → " + auth.accountEmail : "signed out")
                } else { root.promptKind = "login"; root.prompting = true }
                return true
            case Qt.Key_C: {
                const cid = root.watching ? related.channelId
                    : (grid.currentItem ? grid.currentItem.channelId : "")
                const cname = root.watching ? related.channelName
                    : (grid.currentItem ? grid.currentItem.channel : "")
                if (cid !== "") {
                    feed.loadChannel(cid, cname)
                    root.watching = false
                } else {
                    root.notify("channel unknown")
                }
                return true
            }
            }
            return true  // unknown g-sequence: swallow, reset
        }
        if (key === Qt.Key_G && !(event.modifiers & Qt.ShiftModifier)) {
            root.pending = "g"
            return true
        }
        return false
    }

    function tabKey(event) {
        const key = posKey(event)
        if (key >= Qt.Key_1 && key <= Qt.Key_9) {
            tabs.activate(key - Qt.Key_1)
            return true
        }
        if (key === Qt.Key_X) {
            if (tabs.activeIndex >= 0) tabs.closeTab(tabs.activeIndex)
            if (tabs.activeIndex < 0) root.watching = false
            return true
        }
        return false
    }

    property string currentVideoId: ""

    Connections {
        target: tabs
        function onVideoStarted() { root.watching = true; root.refocus() }
        function onCurrentVideoChanged(videoId) { root.currentVideoId = videoId }
    }
    Connections {
        target: picker
        function onMessage(msg) { root.notify(msg) }
    }
    Connections {
        target: comments
        function onMessage(msg) { root.notify(msg) }
    }
    Connections {
        target: feed
        function onMessage(msg) { root.notify(msg) }
    }
    Connections {
        target: auth
        function onChannelChanged(name) { root.notify("channel: " + name) }
        function onAccountChanged(email) { root.notify("account: " + email) }
        function onLoginError(msg) { root.notify(msg) }
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
                    text: root.promptKind === "login" ? "login:"
                        : root.promptKind === "comment" ? "comment:" : "/"
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
                        if (root.promptKind === "login")
                            auth.startLogin(text)
                        else if (root.promptKind === "comment")
                            feed.commentVideo(root.currentVideoId, text)
                        else
                            feed.search(text)
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
                    switch (root.posKey(event)) {
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
                        if (cur) cur.open(); break
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
                    case Qt.Key_D:  // remove from the playlist being viewed
                        if (!cur) break
                        if (feed.contextPlaylistId !== "")
                            feed.removeFromPlaylist(cur.videoId)
                        else root.notify("not a playlist of yours")
                        break
                    case Qt.Key_Slash:
                        root.promptKind = "search"; root.prompting = true; break
                    case Qt.Key_N:
                        tabs.toggleAutoplay()
                        root.notify("autoplay " + (tabs.autoplay ? "on" : "off"))
                        break
                    case Qt.Key_S:  // shift+s: subscribe (cell's channel,
                                    // else the channel feed being viewed)
                        if (!(event.modifiers & Qt.ShiftModifier))
                            return
                        feed.subscribeChannel(
                            cur && cur.channelId !== "" ? cur.channelId
                                                        : feed.contextChannelId)
                        break
                    case Qt.Key_Escape:  // back to the playing video
                        if (tabs.activeIndex >= 0) root.watching = true
                        break
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

                    // Search pagination only (GUIDELINE.org): near the
                    // bottom, ask for the next page. feed no-ops outside
                    // a search context (no continuation token held).
                    onContentYChanged: {
                        if (contentY + height >= contentHeight - cellHeight * 2)
                            feed.loadMoreSearchResults()
                    }

                    delegate: Item {
                        id: cell
                        required property int index
                        required property string videoId
                        required property string title
                        required property string channel
                        required property string duration
                        required property string thumb
                        required property string channelId
                        required property string meta
                        required property string playlistId

                        function open() {
                            if (playlistId !== "")
                                feed.loadPlaylist(playlistId)
                            else
                                tabs.playVideo(videoId, title)
                        }

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
                                    text: [cell.channel, cell.duration, cell.meta]
                                          .filter(s => s !== "").join(" · ")
                                    color: th.fgDim
                                    font.pixelSize: th.fontSizeSmall
                                    elide: Text.ElideRight
                                }
                            }
                        }
                        TapHandler {
                            acceptedButtons: Qt.LeftButton
                            onTapped: {
                                grid.currentIndex = cell.index
                                cell.open()
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

            // Player pool host: one live (paused) player per recent tab,
            // created/destroyed by TabManager's pool signals.
            Item {
                id: playerView
                anchors.fill: parent
                visible: root.watching

                property int activeTabId: -1
                property var activePlayer: null
                property string panelMode: ""  // "" | related|comments|playlist|queue

                function togglePanel(mode) {
                    if (panelMode === mode) {
                        panelMode = ""
                        return
                    }
                    if (mode === "comments")
                        comments.loadCurrent()
                    else if (mode === "playlist")
                        picker.load(root.currentVideoId)
                    panelMode = mode
                }

                function refreshActivePlayer() {
                    for (let i = 0; i < playersRepeater.count; i++) {
                        const item = playersRepeater.itemAt(i)
                        if (item && item.tabId === activeTabId) {
                            activePlayer = item
                            return
                        }
                    }
                    activePlayer = null
                }
                onActiveTabIdChanged: refreshActivePlayer()

                function fmt(s) {
                    s = Math.max(0, Math.floor(s))
                    const h = Math.floor(s / 3600)
                    const m = Math.floor(s % 3600 / 60)
                    const sec = String(s % 60).padStart(2, "0")
                    return h > 0
                        ? `${h}:${String(m).padStart(2, "0")}:${sec}`
                        : `${m}:${sec}`
                }

                ListModel { id: playersModel }

                Connections {
                    target: tabs
                    function onCreatePlayer(tabId) {
                        playersModel.append({ tabId: tabId })
                    }
                    function onDestroyPlayer(tabId) {
                        for (let i = 0; i < playersModel.count; i++)
                            if (playersModel.get(i).tabId === tabId) {
                                playersModel.remove(i)
                                break
                            }
                        playerView.refreshActivePlayer()
                    }
                    function onSetActivePlayer(tabId) {
                        playerView.activeTabId = tabId
                    }
                }

                Item {
                    id: poolArea
                    anchors.fill: parent
                    // The video shrinks when a side panel is open.
                    anchors.rightMargin: playerView.panelMode !== ""
                        ? sidePanel.width : 0

                Repeater {
                    id: playersRepeater
                    model: playersModel
                    onItemAdded: playerView.refreshActivePlayer()

                    delegate: Item {
                        id: wrap
                        required property int tabId
                        anchors.fill: parent

                        readonly property bool isActive:
                            tabId === playerView.activeTabId
                        visible: isActive

                        property real position: 0
                        property real duration: 0
                        property bool paused: false
                        property bool userPaused: false
                        property bool loading: true
                        property string decode: ""
                        property int volume: 100
                        property int vHeight: 0
                        property real vFps: 0
                        property string vFormat: ""
                        property string subLang: ""
                        readonly property string quality: vHeight > 0
                            ? vHeight + "p" + (vFps > 0 ? Math.round(vFps) : "")
                              + (vFormat !== "" ? " " + vFormat : "")
                            : ""

                        // Background tabs pause; foreground restores the
                        // user's intent (browser semantics).
                        onIsActiveChanged: p.command(
                            ["set", "pause",
                             isActive ? (userPaused ? "yes" : "no") : "yes"])

                        function cmd(c) { p.command(c) }
                        function togglePause() {
                            userPaused = !paused
                            p.command(["cycle", "pause"])
                        }

                        Connections {
                            target: tabs
                            function onMpvCommandFor(tabId, c) {
                                if (tabId !== wrap.tabId)
                                    return
                                p.command(c)
                                if (c[0] === "loadfile" && c[2] === "replace") {
                                    wrap.loading = true
                                    wrap.userPaused = false
                                    wrap.vHeight = 0
                                    wrap.vFps = 0
                                    wrap.vFormat = ""
                                }
                            }
                        }

                        MpvObject {
                            id: p
                            anchors.fill: parent

                            Component.onCompleted: {
                                // AV1-first; VP9 fallback. The iHD teardown
                                // race is held off by TabManager's stop+drain
                                // (re-verify with --stress after driver bumps).
                                p.setProperty("ytdl-format",
                                    "bv*[vcodec^=av01][height<=?4320]+ba/bv*[vcodec^=vp9]+ba/b")
                                // Subtitle tracks must be requested or
                                // ytdl_hook adds none.
                                p.setProperty("ytdl-raw-options",
                                    'sub-langs="es.*,en.*",write-subs=')
                                // Runtime UI state via async observers only —
                                // synchronous getProperty during load deadlocks.
                                p.observe("stream-open-filename")
                                p.observe("volume")
                                p.observe("video-params")
                                p.observe("container-fps")
                                p.observe("video-format")
                                p.observe("current-tracks/sub/lang")
                            }

                            onPlaybackTimeChanged: (secs) => {
                                tabs.playbackTime(wrap.tabId, secs)
                                wrap.position = secs
                                wrap.loading = false
                            }
                            onPlaylistPosChanged: (pos) =>
                                tabs.playlistPos(wrap.tabId, pos)
                            onDurationChanged: (secs) => wrap.duration = secs
                            onPauseChanged: (paused) => wrap.paused = paused
                            onEndFile: (error) => {
                                if (error) {
                                    tabs.loadFailed(wrap.tabId)
                                    if (wrap.isActive)
                                        root.notify("load failed — retrying")
                                }
                            }
                            onPropertyChanged: (name, value) => {
                                switch (name) {
                                case "stream-open-filename":
                                    if (value) tabs.resolvedUrl(wrap.tabId, value)
                                    break
                                case "volume":
                                    if (value !== undefined && value !== null)
                                        wrap.volume = Math.round(value)
                                    break
                                case "video-params":
                                    wrap.vHeight = value && value.h ? value.h : 0
                                    break
                                case "container-fps":
                                    wrap.vFps = value ?? 0; break
                                case "video-format":
                                    wrap.vFormat = value ?? ""; break
                                case "current-tracks/sub/lang":
                                    wrap.subLang = value ?? ""; break
                                }
                            }
                            onLogMessage: (prefix, level, text) => {
                                // vd info is low-volume and carries the hw/sw
                                // decode decision — always worth surfacing.
                                if (level === "error" || level === "warn"
                                        || prefix === "vd")
                                    console.log(`[${prefix}] ${level}: ${text}`)
                                if (prefix === "vd"
                                        && text.indexOf("hardware decoding") >= 0)
                                    wrap.decode = (text.match(/\((.+)\)/) ?? [, "hw"])[1]
                                else if (prefix === "vd"
                                        && text.indexOf("software decoding") >= 0)
                                    wrap.decode = "sw"
                            }
                        }
                    }
                }

                }  // poolArea

                // Related panel: text-only list, TUI style.
                Rectangle {
                    id: sidePanel
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.bottom: parent.bottom
                    // Wide enough to read comments: 40% of the view, capped.
                    width: Math.min(560, Math.round(playerView.width * 0.4))
                    visible: playerView.panelMode !== ""
                    color: th.bg
                    border.color: th.bg2
                    border.width: 1

                    Column {
                        anchors.fill: parent
                        anchors.margins: 1

                        Rectangle {
                            width: parent.width
                            height: th.barHeight
                            color: th.bg1
                            Text {
                                anchors.fill: parent
                                anchors.leftMargin: 8
                                verticalAlignment: Text.AlignVCenter
                                text: playerView.panelMode === "queue"
                                    ? "queue (" + queueList.count + ")"
                                    : playerView.panelMode === "playlist"
                                    ? (picker.loading ? "save to — loading…"
                                       : "save to playlist")
                                    : playerView.panelMode === "comments"
                                    ? (comments.loading ? "comments — loading…"
                                       : "comments (" + commList.count
                                         + (comments.hasMore ? "+" : "") + ")")
                                    : related.loading ? "related — loading…"
                                    : "related" + (related.channelName !== ""
                                        ? " — " + related.channelName : "")
                                color: th.fg
                                font.pixelSize: th.fontSizeSmall
                                elide: Text.ElideRight
                            }
                        }

                        ListView {
                            id: plList
                            width: parent.width
                            height: parent.height - th.barHeight
                            visible: playerView.panelMode === "playlist"
                            clip: true
                            model: picker.items
                            currentIndex: 0
                            boundsBehavior: Flickable.StopAtBounds
                            highlightMoveDuration: 0
                            highlightMoveVelocity: -1

                            delegate: Rectangle {
                                id: plCell
                                required property var modelData
                                required property int index
                                readonly property bool sel:
                                    ListView.isCurrentItem
                                width: plList.width
                                height: 36
                                color: sel ? th.accent : "transparent"

                                Text {
                                    anchors.fill: parent
                                    anchors.leftMargin: 8
                                    anchors.rightMargin: 8
                                    verticalAlignment: Text.AlignVCenter
                                    text: (plCell.modelData.contains ? "✓ " : "  ")
                                          + plCell.modelData.title
                                    color: plCell.sel ? th.accentFg : th.fg
                                    font.pixelSize: th.fontSize
                                    elide: Text.ElideRight
                                }
                                TapHandler {
                                    onTapped: {
                                        plList.currentIndex = plCell.index
                                        picker.save(plCell.index)
                                        playerView.panelMode = ""
                                    }
                                }
                            }
                        }

                        ListView {
                            id: queueList
                            width: parent.width
                            height: parent.height - th.barHeight
                            visible: playerView.panelMode === "queue"
                            clip: true
                            model: tabs.queueModel
                            currentIndex: 0
                            boundsBehavior: Flickable.StopAtBounds
                            highlightMoveDuration: 0
                            highlightMoveVelocity: -1

                            delegate: Rectangle {
                                id: qCell
                                required property string title
                                required property bool current
                                required property int index
                                readonly property bool sel:
                                    ListView.isCurrentItem
                                width: queueList.width
                                height: 36
                                color: sel ? th.accent : "transparent"

                                Text {
                                    anchors.fill: parent
                                    anchors.leftMargin: 8
                                    anchors.rightMargin: 8
                                    verticalAlignment: Text.AlignVCenter
                                    text: (qCell.current ? "▶ " : "  ")
                                          + qCell.title
                                    color: qCell.sel ? th.accentFg
                                        : qCell.current ? th.fg : th.fgDim
                                    font.pixelSize: th.fontSize
                                    elide: Text.ElideRight
                                }
                                TapHandler {
                                    onTapped: {
                                        queueList.currentIndex = qCell.index
                                        tabs.jumpToQueueItem(qCell.index)
                                    }
                                }
                            }
                        }

                        ListView {
                            id: commList
                            width: parent.width
                            height: parent.height - th.barHeight
                            visible: playerView.panelMode === "comments"
                            clip: true
                            model: comments
                            currentIndex: 0
                            spacing: 1
                            // Anti-jump package for variable-height rows:
                            // pre-lay rows outside the viewport, no flick
                            // overshoot, no animated ensure-visible (DESIGN:
                            // no animations — and it wrecks j/k anyway).
                            cacheBuffer: 800
                            boundsBehavior: Flickable.StopAtBounds
                            highlightMoveDuration: 0
                            highlightMoveVelocity: -1
                            highlightResizeDuration: 0

                            delegate: Rectangle {
                                id: commCell
                                required property int index
                                required property string author
                                required property string text
                                required property string likes
                                required property string published
                                required property string replies
                                required property int depth
                                required property bool hasReplies
                                required property bool expanded
                                required property string avatar
                                required property bool liked
                                required property bool isMore
                                readonly property bool sel:
                                    ListView.isCurrentItem
                                width: commList.width
                                height: isMore ? 30 : commCol.implicitHeight + 12
                                color: sel ? th.bg1 : "transparent"

                                // Synthetic row: loads the next replies page.
                                Text {
                                    x: 10 + commCell.depth * 18
                                    anchors.verticalCenter: parent.verticalCenter
                                    visible: commCell.isMore
                                    text: "▸ more replies (enter)"
                                    color: th.fgDim
                                    font.pixelSize: th.fontSizeSmall
                                }

                                // Long text blocks mark selection with an
                                // accent edge instead of a full accent fill.
                                Rectangle {
                                    width: 2
                                    height: parent.height
                                    color: th.accent
                                    visible: commCell.sel
                                }
                                Column {
                                    id: commCol
                                    visible: !commCell.isMore
                                    // Explicit geometry from the ListView's
                                    // stable width — anchoring to the parent
                                    // (whose height depends on this column's
                                    // implicitHeight) needs several layout
                                    // passes and the transient heights make
                                    // scrolling jump.
                                    x: 10 + commCell.depth * 18
                                    y: 6
                                    width: commList.width - x - 6
                                    spacing: 3

                                    Row {
                                        spacing: 6
                                        Rectangle {
                                            width: 26
                                            height: 26
                                            color: th.bg1
                                            visible: commCell.avatar !== ""
                                            Image {
                                                anchors.fill: parent
                                                asynchronous: true
                                                sourceSize.width: 26
                                                source: commCell.avatar
                                            }
                                        }
                                        Text {
                                            text: commCell.author
                                            color: th.fg
                                            font.pixelSize: th.fontSizeSmall
                                            anchors.verticalCenter: parent.verticalCenter
                                        }
                                        Text {
                                            text: commCell.published
                                            color: th.emptyDim
                                            font.pixelSize: th.fontSizeSmall
                                            anchors.verticalCenter: parent.verticalCenter
                                        }
                                    }
                                    Text {
                                        width: parent.width
                                        text: commCell.text
                                        color: th.fg
                                        font.pixelSize: th.fontSize
                                        wrapMode: Text.Wrap
                                    }
                                    Row {
                                        spacing: 8
                                        Text {
                                            text: (commCell.liked ? "♥ " : "")
                                                  + (commCell.likes !== ""
                                                     ? commCell.likes + " likes"
                                                     : (commCell.liked ? "liked" : ""))
                                            color: commCell.liked
                                                ? th.accent : th.emptyDim
                                            font.pixelSize: th.fontSizeSmall
                                            visible: text !== ""
                                        }
                                        Text {
                                            text: (commCell.expanded ? "▾" : "▸")
                                                  + " " + commCell.replies
                                                  + " replies (enter)"
                                            color: th.fgDim
                                            font.pixelSize: th.fontSizeSmall
                                            visible: commCell.hasReplies
                                                     && commCell.depth === 0
                                        }
                                    }
                                }
                            }
                        }

                        ListView {
                            id: relList
                            width: parent.width
                            height: parent.height - th.barHeight
                            visible: playerView.panelMode === "related"
                            clip: true
                            model: related.items
                            currentIndex: 0
                            cacheBuffer: 400
                            boundsBehavior: Flickable.StopAtBounds
                            highlightMoveDuration: 0
                            highlightMoveVelocity: -1

                            delegate: Rectangle {
                                id: relCell
                                required property var modelData
                                required property int index
                                readonly property bool sel:
                                    ListView.isCurrentItem
                                width: relList.width
                                height: 66
                                color: sel ? th.accent : "transparent"

                                Row {
                                    anchors.fill: parent
                                    anchors.margins: 6
                                    spacing: 8

                                    Rectangle {
                                        width: 96
                                        height: 54
                                        color: th.bg1
                                        Image {
                                            anchors.fill: parent
                                            asynchronous: true
                                            sourceSize.width: 96
                                            fillMode: Image.PreserveAspectCrop
                                            source: relCell.modelData.thumb
                                        }
                                    }
                                    Column {
                                        width: parent.width - 96 - 8
                                        spacing: 2
                                        Text {
                                            width: parent.width
                                            text: relCell.modelData.title
                                            color: relCell.sel ? th.accentFg : th.fg
                                            font.pixelSize: th.fontSizeSmall
                                            elide: Text.ElideRight
                                            maximumLineCount: 2
                                            wrapMode: Text.WordWrap
                                        }
                                        Text {
                                            width: parent.width
                                            text: relCell.modelData.channel
                                                  + (relCell.modelData.duration
                                                     ? " · " + relCell.modelData.duration : "")
                                            color: relCell.sel ? th.accentFg : th.fgDim
                                            font.pixelSize: th.fontSizeSmall
                                            elide: Text.ElideRight
                                        }
                                        Text {
                                            width: parent.width
                                            text: relCell.modelData.meta
                                            color: relCell.sel ? th.accentFg : th.fgDim
                                            font.pixelSize: th.fontSizeSmall
                                            elide: Text.ElideRight
                                            visible: relCell.modelData.meta !== ""
                                        }
                                    }
                                }
                                TapHandler {
                                    onTapped: {
                                        relList.currentIndex = index
                                        tabs.playVideo(modelData.videoId,
                                                       modelData.title)
                                    }
                                }
                            }
                        }
                    }
                }

                Keys.onPressed: (event) => {
                    if (root.gKey(event) || root.tabKey(event)) {
                        event.accepted = true
                        return
                    }
                    const key = root.posKey(event)
                    // Panel navigation captures j/k/enter while open.
                    if (playerView.panelMode !== "") {
                        const mode = playerView.panelMode
                        const isRel = mode === "related"
                        const list = isRel ? relList
                            : mode === "comments" ? commList
                            : mode === "queue" ? queueList : plList
                        const it = isRel && list.currentIndex >= 0
                            && list.currentIndex < related.items.length
                            ? related.items[list.currentIndex] : null
                        switch (key) {
                        case Qt.Key_J: case Qt.Key_Down:
                            // Shift+J in the queue panel: move item down.
                            if (mode === "queue"
                                    && (event.modifiers & Qt.ShiftModifier)) {
                                if (tabs.moveQueueItem(list.currentIndex, 1))
                                    list.currentIndex += 1
                                event.accepted = true; return
                            }
                            list.currentIndex = Math.min(
                                list.count - 1, list.currentIndex + 1)
                            if (mode === "comments" && comments.hasMore
                                    && list.currentIndex === list.count - 1)
                                comments.loadMore()
                            event.accepted = true; return
                        case Qt.Key_K: case Qt.Key_Up:
                            if (mode === "queue"
                                    && (event.modifiers & Qt.ShiftModifier)) {
                                if (tabs.moveQueueItem(list.currentIndex, -1))
                                    list.currentIndex -= 1
                                event.accepted = true; return
                            }
                            list.currentIndex = Math.max(
                                0, list.currentIndex - 1)
                            event.accepted = true; return
                        case Qt.Key_D:
                            if (mode === "queue")
                                tabs.removeQueueItem(list.currentIndex)
                            event.accepted = true; return
                        case Qt.Key_Return: case Qt.Key_Enter:
                            if (isRel) {
                                if (it) tabs.playVideo(it.videoId, it.title)
                            } else if (mode === "comments") {
                                comments.toggleReplies(commList.currentIndex)
                            } else if (mode === "queue") {
                                tabs.jumpToQueueItem(list.currentIndex)
                            } else {
                                picker.save(plList.currentIndex)
                                playerView.panelMode = ""
                            }
                            event.accepted = true; return
                        case Qt.Key_T:
                            if (it) { tabs.openInNewTab(it.videoId, it.title)
                                      root.notify("opened in new tab") }
                            event.accepted = true; return
                        case Qt.Key_A:
                            if (it) { tabs.enqueue(it.videoId, it.title)
                                      root.notify("queued") }
                            event.accepted = true; return
                        case Qt.Key_P:
                            if (it) { tabs.playNext(it.videoId, it.title)
                                      root.notify("playing next") }
                            event.accepted = true; return
                        case Qt.Key_R:
                            playerView.togglePanel("related")
                            event.accepted = true; return
                        case Qt.Key_C:
                            if (event.modifiers & Qt.ShiftModifier) {
                                root.promptKind = "comment"
                                root.prompting = true
                            } else {
                                playerView.togglePanel("comments")
                            }
                            event.accepted = true; return
                        case Qt.Key_B:
                            playerView.togglePanel("playlist")
                            event.accepted = true; return
                        case Qt.Key_U:
                            playerView.togglePanel("queue")
                            event.accepted = true; return
                        case Qt.Key_L:
                            if (mode === "comments"
                                    && (event.modifiers & Qt.ShiftModifier)) {
                                comments.likeComment(commList.currentIndex)
                                event.accepted = true; return
                            }
                            break
                        case Qt.Key_Escape:
                            playerView.panelMode = ""
                            event.accepted = true; return
                        }
                    }
                    // Keys that must work even with no live player (a black
                    // watch view must never trap the user).
                    switch (key) {
                    case Qt.Key_Escape:
                        root.watching = false
                        event.accepted = true
                        return
                    case Qt.Key_F:
                        root.visibility = root.visibility === Window.FullScreen
                            ? Window.Windowed : Window.FullScreen
                        event.accepted = true
                        return
                    case Qt.Key_N:
                        tabs.toggleAutoplay()
                        root.notify("autoplay " + (tabs.autoplay ? "on" : "off"))
                        event.accepted = true
                        return
                    }
                    const ap = playerView.activePlayer
                    if (!ap) return
                    switch (key) {
                    case Qt.Key_R:
                        playerView.togglePanel("related"); break
                    case Qt.Key_C:
                        if (event.modifiers & Qt.ShiftModifier) {
                            root.promptKind = "comment"
                            root.prompting = true
                            break
                        }
                        playerView.togglePanel("comments"); break
                    case Qt.Key_B:
                        playerView.togglePanel("playlist"); break
                    case Qt.Key_U:
                        playerView.togglePanel("queue"); break
                    case Qt.Key_Space: ap.togglePause(); break
                    case Qt.Key_H: case Qt.Key_Left:
                        ap.cmd(["seek", -5]); break
                    case Qt.Key_L:
                        if (event.modifiers & Qt.ShiftModifier) {
                            feed.likeVideo(root.currentVideoId)
                            break
                        }
                        ap.cmd(["seek", 5]); break
                    case Qt.Key_Right:
                        ap.cmd(["seek", 5]); break
                    case Qt.Key_K: case Qt.Key_Up:
                        ap.cmd(["add", "volume", 5]); break
                    case Qt.Key_J: case Qt.Key_Down:
                        ap.cmd(["add", "volume", -5]); break
                    case Qt.Key_M: ap.cmd(["cycle", "mute"]); break
                    case Qt.Key_S:
                        if (event.modifiers & Qt.ShiftModifier) {
                            if (related.channelId !== "")
                                feed.subscribeChannel(related.channelId)
                            else
                                root.notify("channel unknown")
                        } else {
                            ap.cmd(["cycle", "sub"])
                        }
                        break
                    default:
                        return
                    }
                    event.accepted = true
                }

                // Mouse never gates: click toggles pause.
                TapHandler {
                    onTapped: playerView.activePlayer?.togglePause()
                }
            }
        }

        // Statusline: always visible, the TUI way. Mode | title | segments.
        Rectangle {
            id: statusline
            width: parent.width
            height: th.barHeight
            color: th.bg1

            readonly property var ap: playerView.activePlayer

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

                // Active Google account, when more than one is registered.
                Text {
                    id: accountTag
                    width: auth.accountCount > 1 ? implicitWidth + 16 : 0
                    height: parent.height
                    visible: width > 0
                    verticalAlignment: Text.AlignVCenter
                    horizontalAlignment: Text.AlignHCenter
                    text: auth.accountEmail
                    color: th.fgDim
                    font.pixelSize: th.fontSizeSmall
                }
                Rectangle {
                    width: accountTag.visible ? 1 : 0
                    height: parent.height
                    color: th.bg2
                }

                // Acting-as channel (brand account), when one is selected.
                Text {
                    id: channelTag
                    width: auth.channelName !== "" ? implicitWidth + 16 : 0
                    height: parent.height
                    visible: width > 0
                    verticalAlignment: Text.AlignVCenter
                    horizontalAlignment: Text.AlignHCenter
                    text: "as " + auth.channelName
                    color: th.fgDim
                    font.pixelSize: th.fontSizeSmall
                }
                Rectangle {
                    width: channelTag.visible ? 1 : 0
                    height: parent.height
                    color: th.bg2
                }

                // Autoplay indicator (session-only, per-app -- n toggles).
                Text {
                    id: autoplayTag
                    width: tabs.autoplay ? implicitWidth + 16 : 0
                    height: parent.height
                    visible: width > 0
                    verticalAlignment: Text.AlignVCenter
                    horizontalAlignment: Text.AlignHCenter
                    text: "AP"
                    color: th.green
                    font.pixelSize: th.fontSizeSmall
                }
                Rectangle {
                    width: autoplayTag.visible ? 1 : 0
                    height: parent.height
                    color: th.bg2
                }

                // Feed context (what the grid is showing) while browsing.
                Text {
                    id: contextTag
                    // No paddings here: implicitWidth includes them and the
                    // width binding would loop. Center within the +16.
                    width: !root.watching && feed.contextLabel !== ""
                        ? implicitWidth + 16 : 0
                    height: parent.height
                    visible: width > 0
                    verticalAlignment: Text.AlignVCenter
                    horizontalAlignment: Text.AlignHCenter
                    text: feed.contextLabel
                    color: th.fg
                    font.pixelSize: th.fontSizeSmall
                }
                Rectangle {
                    width: contextTag.visible ? 1 : 0
                    height: parent.height
                    color: th.bg2
                }

                Text {
                    width: parent.width - x - rightSegments.width
                    height: parent.height
                    verticalAlignment: Text.AlignVCenter
                    leftPadding: 8
                    text: root.statusMsg !== "" ? root.statusMsg
                        : root.watching && statusline.ap && statusline.ap.loading
                        ? "loading…"
                        : root.watching
                        ? "space pause · h/l seek · j/k vol · r related · c comments · C comment · b playlist · L like · S subscribe · gc channel · m mute · f full · n autoplay · gt/1-9 tab · esc back"
                        : "hjkl move · enter play · / search · gh home · gs subs · gy history · gp lists · gw later · gc channel · ga channel-as · t/a/p/w/S act · n autoplay · esc video · q quit"
                    color: root.statusMsg !== ""
                           || (root.watching && statusline.ap && statusline.ap.loading)
                        ? th.fg : th.fgDim
                    font.pixelSize: th.fontSizeSmall
                    elide: Text.ElideRight
                }

                Row {
                    id: rightSegments
                    height: parent.height
                    visible: root.watching && statusline.ap !== null

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
                                width: statusline.ap && statusline.ap.duration > 0
                                    ? parent.width * Math.min(1,
                                        statusline.ap.position / statusline.ap.duration)
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
                        text: statusline.ap
                            ? playerView.fmt(statusline.ap.position) + " / "
                              + playerView.fmt(statusline.ap.duration)
                            : ""
                        color: th.fg
                        font.pixelSize: th.fontSizeSmall
                    }
                    Rectangle { width: 1; height: parent.height; color: th.bg2 }
                    Text {
                        height: parent.height
                        verticalAlignment: Text.AlignVCenter
                        leftPadding: 8
                        rightPadding: 8
                        text: statusline.ap
                            ? (statusline.ap.paused ? "||" : "|>")
                              + `  vol ${statusline.ap.volume}`
                            : ""
                        color: th.fgDim
                        font.pixelSize: th.fontSizeSmall
                    }
                    Rectangle { width: 1; height: parent.height; color: th.bg2 }
                    Text {
                        height: parent.height
                        verticalAlignment: Text.AlignVCenter
                        leftPadding: 8
                        rightPadding: 8
                        text: statusline.ap ? "sub " + statusline.ap.subLang : ""
                        color: th.fgDim
                        font.pixelSize: th.fontSizeSmall
                        visible: statusline.ap !== null && statusline.ap.subLang !== ""
                    }
                    Rectangle { width: 1; height: parent.height; color: th.bg2 }
                    Text {
                        height: parent.height
                        verticalAlignment: Text.AlignVCenter
                        leftPadding: 8
                        rightPadding: 8
                        text: statusline.ap ? statusline.ap.quality : ""
                        color: th.fgDim
                        font.pixelSize: th.fontSizeSmall
                        visible: statusline.ap !== null && statusline.ap.quality !== ""
                    }
                    Rectangle { width: 1; height: parent.height; color: th.bg2 }
                    Text {
                        height: parent.height
                        verticalAlignment: Text.AlignVCenter
                        leftPadding: 8
                        rightPadding: 8
                        text: statusline.ap ? statusline.ap.decode : ""
                        color: statusline.ap && statusline.ap.decode === "sw"
                            ? th.red : th.green
                        font.pixelSize: th.fontSizeSmall
                        visible: statusline.ap !== null && statusline.ap.decode !== ""
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
