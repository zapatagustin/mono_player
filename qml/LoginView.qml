// The ONE place QtWebEngine is allowed: the login screen, loaded on demand
// by a Loader and torn down afterwards (GUIDELINE.org, Login). Only the
// frame is themed — the page content is Google's (DESIGN.md, Components).
import QtQuick
import QtWebEngine

Rectangle {
    color: th.bg
    focus: true
    Keys.onEscapePressed: auth.cancelLogin()

    Column {
        anchors.fill: parent

        Rectangle {
            width: parent.width
            height: th.barHeight
            color: th.bg1
            Row {
                anchors.fill: parent
                Rectangle {
                    width: loginTag.width + 16
                    height: parent.height
                    color: th.accent
                    Text {
                        id: loginTag
                        anchors.centerIn: parent
                        text: "LOGIN"
                        color: th.accentFg
                        font.pixelSize: th.fontSize
                    }
                }
                Rectangle { width: 1; height: parent.height; color: th.bg2 }
                Text {
                    height: parent.height
                    verticalAlignment: Text.AlignVCenter
                    leftPadding: 8
                    text: "sign in with your google account · esc cancel"
                    color: th.fgDim
                    font.pixelSize: th.fontSizeSmall
                }
            }
        }

        WebEngineView {
            width: parent.width
            height: parent.height - th.barHeight
            profile: loginProfile
            url: "https://accounts.google.com/EmbeddedSetup"
            onLoadingChanged: (info) =>
                console.log("login web:", info.status, info.url)
        }
    }
}
