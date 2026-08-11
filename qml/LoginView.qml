// The ONE place QtWebEngine is allowed: the login screen, loaded on demand
// by a Loader and torn down afterwards (GUIDELINE.org, Login).
import QtQuick
import QtQuick.Controls.Basic
import QtWebEngine

Rectangle {
    color: "#111111"

    Column {
        anchors.fill: parent

        Row {
            width: parent.width
            height: 36
            Button {
                text: "Cancel"
                height: parent.height
                onClicked: auth.cancelLogin()
            }
            Text {
                text: "Sign in with your Google account"
                color: "#aaaaaa"
                anchors.verticalCenter: parent.verticalCenter
                leftPadding: 12
            }
        }

        WebEngineView {
            width: parent.width
            height: parent.height - 36
            profile: loginProfile
            url: "https://accounts.google.com/EmbeddedSetup"
            onLoadingChanged: (info) =>
                console.log("login web:", info.status, info.url)
        }
    }
}
