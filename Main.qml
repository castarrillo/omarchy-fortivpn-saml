import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

Panel {
  id: root
  moduleName: "io.github.fbal98.fortivpn"
  ipcTarget: "io.github.fbal98.fortivpn"
  manageIpc: false
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  property var vpn: ({state: "unavailable"})
  property string requestError: ""
  property string openedLoginAttempt: ""
  readonly property string loginLauncher: Qt.resolvedUrl("scripts/open-saml-browser.py").toString().replace(/^file:\/\//, "")
  function validLogin(login, gateway, port) {
    var prefix = "https://" + gateway + ":" + String(port || "443") + "/remote/saml/start?redirect=1"
    return login.indexOf(prefix) === 0 && /^(?:&realm=[A-Za-z0-9%_.~-]*)?$/.test(login.slice(prefix.length))
  }
  function openLogin(login) {
    if (validLogin(login, root.vpn.gateway, root.vpn.port)) {
      console.info("FortiVPN: opening SSO browser using", root.loginLauncher)
      Quickshell.execDetached(["/usr/bin/python3", root.loginLauncher, login])
    }
  }
  readonly property string uiFont: "Adwaita Sans"
  property bool detailsOpen: false
  property var rxHistory: []
  property var txHistory: []
  readonly property color uploadColor: "#bb9af7"
  readonly property color stateColor: connected ? statusColor : (vpn.state === "error" ? Color.urgent : muted)
  readonly property string feedback: requestError || (vpn.state === "error" && vpn.message && vpn.message.indexOf("timed out") >= 0 ? "Connect again and enter a fresh authentication code." : (needsToken ? "" : vpn.message || (vpn.state === "unavailable" ? "Waiting for the VPN service…" : "")))
  function tint(alpha) { return Qt.rgba(ink.r, ink.g, ink.b, alpha) }

  readonly property bool connected: vpn.state === "connected"
  readonly property bool needsToken: vpn.state === "token"
  readonly property bool busy: ["connecting", "disconnecting", "token", "external"].indexOf(vpn.state) >= 0
  readonly property color ink: bar ? bar.foreground : Color.foreground
  readonly property color muted: Qt.rgba(ink.r, ink.g, ink.b, 0.67)
  readonly property color statusColor: connected ? "#9ece6a" : (vpn.state === "error" ? Color.urgent : ink)
  readonly property string statusText: ({connected: "Connected", disconnected: "Disconnected", connecting: "Connecting…", disconnecting: "Disconnecting…", token: "Authentication token", error: "Connection failed", external: "Terminal connection", unavailable: "Service unavailable"})[vpn.state] || "Checking…"

  function send(action, value) {
    if (action !== "status") requestError = ""
    if (!channel || !channel.connected) { requestError = "VPN service is unavailable."; return }
    var request = {action: action}
    if (action === "token") request.token = value
    channel.write(JSON.stringify(request) + "\n")
    channel.flush()
  }
  function submitToken() {
    if (!token.acceptableInput || token.text.length < 4) return
    send("token", token.text.trim())
    token.clear()
    primary.forceActiveFocus()
  }
  function bytes(value) {
    var n = Number(value || 0), units = ["B", "KiB", "MiB", "GiB"]
    var i = 0
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++ }
    return n.toFixed(i === 0 ? 0 : 1) + " " + units[i]
  }
  function duration(value) {
    var n = Number(value || 0), h = Math.floor(n / 3600), m = Math.floor(n / 60) % 60, s = n % 60
    return (h > 0 ? h + "h " : "") + m + "m " + s + "s"
  }
  onNeedsTokenChanged: {
    token.clear()
    if (needsToken && opened) Qt.callLater(function() { token.forceActiveFocus() })
  }
  onOpenedChanged: {
    if (!opened) token.clear()
    else if (needsToken) Qt.callLater(function() { token.forceActiveFocus() })
  }

  readonly property var channel: connectionLoader.item ? connectionLoader.item.socket : null
  Loader {
    id: connectionLoader
    sourceComponent: Item {
      property alias socket: connection
      Socket {
    id: connection
    path: "/run/fortivpn-panel/control.sock"
    connected: true
    onError: root.vpn = {state: "unavailable"}
    onConnectedChanged: {
      if (connected) root.send("status")
      else root.vpn = {state: "unavailable"}
    }
    parser: SplitParser {
      onRead: data => {
        try {
          var next = JSON.parse(data)
          if (next.error) root.requestError = next.error
          root.vpn = next
          var login = next.browserUrl || ""
          var attempt = next.attemptId || ""
          if (next.state === "connecting" && attempt !== "" && attempt !== root.openedLoginAttempt
              && root.validLogin(login, next.gateway, next.port)) {
            root.openedLoginAttempt = attempt
            root.openLogin(login)
          }
          if (next.state === "connected") {
            root.rxHistory = root.rxHistory.concat([next.rxRate || 0]).slice(-30)
            root.txHistory = root.txHistory.concat([next.txRate || 0]).slice(-30)
          } else { root.rxHistory = []; root.txHistory = [] }
          traffic.requestPaint()
        } catch (e) { root.requestError = "Could not read VPN status." }
      }
    }
  }
    }
  }
  Timer {
    interval: 1000
    running: true
    repeat: true
    onTriggered: {
      if (root.channel && root.channel.connected) root.send("status")
      else { connectionLoader.active = false; connectionLoader.active = true }
    }
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: "󰦝"
    foreground: root.connected ? root.statusColor : Qt.rgba(root.barForeground.r, root.barForeground.g, root.barForeground.b, 0.6)
    tooltipText: "FortiVPN · " + root.statusText
    onPressed: root.toggle()
    Rectangle {
      width: 5; height: 5; radius: 3
      anchors.right: parent.right
      anchors.bottom: parent.bottom
      anchors.margins: 3
      color: root.statusColor
      visible: root.connected || root.busy
    }
  }

  component Action : QQC.Button {
    id: control
    property bool prominent: false
    implicitHeight: Style.space(40)
    activeFocusOnTab: true
    font.family: root.uiFont
    font.pixelSize: Style.space(13)
    font.weight: Font.DemiBold
    opacity: enabled ? 1 : 0.4
    background: Rectangle {
      radius: Style.space(7)
      color: control.prominent ? (control.down ? Qt.darker(Color.accent, 1.12) : control.hovered ? Qt.lighter(Color.accent, 1.08) : Color.accent) : (control.hovered ? root.tint(0.10) : root.tint(0.04))
      border.width: control.activeFocus ? 2 : (control.prominent ? 0 : 1)
      border.color: control.activeFocus ? root.ink : root.tint(0.12)
    }
    contentItem: Text {
      text: control.text
      font: control.font
      color: control.prominent ? Color.background : root.ink
      horizontalAlignment: Text.AlignHCenter
      verticalAlignment: Text.AlignVCenter
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: root.needsToken ? token : primary
    padding: Style.space(20)
    borderSpec: Border.flat(root.tint(0.22), 1)
    contentWidth: panel.fittedContentWidth(Style.space(310))
    contentHeight: panel.fittedContentHeight(content.implicitHeight)

    ColumnLayout {
      id: content
      width: parent.width
      spacing: Style.space(18)
      Keys.onEscapePressed: root.close()

      Item {
        Layout.fillWidth: true
        implicitHeight: Style.space(44)
        Rectangle {
          id: emblem
          width: Style.space(42); height: width
          radius: Style.space(11)
          color: Qt.rgba(Color.accent.r, Color.accent.g, Color.accent.b, 0.10)
          Text {
            anchors.centerIn: parent
            text: "󰦝"
            color: root.connected ? root.statusColor : Color.accent
            font.family: Style.font.family
            font.pixelSize: Style.space(25)
          }
        }
        Column {
          anchors.left: emblem.right
          anchors.leftMargin: Style.space(12)
          anchors.verticalCenter: emblem.verticalCenter
          spacing: Style.space(4)
          Text { text: "FortiVPN"; color: root.ink; font.family: root.uiFont; font.pixelSize: Style.space(18); font.weight: Font.DemiBold }
          Row {
            spacing: Style.space(6)
            Rectangle { width: Style.space(6); height: width; radius: width/2; anchors.verticalCenter: parent.verticalCenter; color: root.stateColor }
            Text { text: root.statusText; color: root.stateColor; font.family: root.uiFont; font.pixelSize: Style.space(12) }
          }
        }
        QQC.Button {
          anchors.right: parent.right
          anchors.top: parent.top
          width: Style.space(26); height: width
          text: "×"
          activeFocusOnTab: true
          Accessible.name: "Close VPN panel"
          background: Rectangle { radius: 6; color: parent.hovered || parent.activeFocus ? root.tint(0.10) : "transparent" }
          contentItem: Text { text: "×"; color: root.muted; font.family: root.uiFont; font.pixelSize: Style.space(19); horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
          onClicked: root.close()
        }
      }

      RowLayout {
        Layout.fillWidth: true
        visible: !!root.vpn.gateway
        Text { text: "Gateway"; color: root.muted; font.family: root.uiFont; font.pixelSize: Style.space(12) }
        Text { Layout.fillWidth: true; text: (root.vpn.gateway || "") + ":" + (root.vpn.port || ""); color: root.ink; opacity: 0.8; horizontalAlignment: Text.AlignRight; font.family: "JetBrains Mono"; font.pixelSize: Style.space(11); textFormat: Text.PlainText }
      }

      Rectangle {
        Layout.fillWidth: true
        visible: root.feedback !== "" && !root.needsToken
        implicitHeight: feedbackColumn.implicitHeight + Style.space(24)
        radius: Style.space(8)
        color: root.vpn.state === "error" || root.requestError ? Qt.rgba(Color.urgent.r, Color.urgent.g, Color.urgent.b, 0.065) : root.tint(0.04)
        Column {
          id: feedbackColumn
          x: Style.space(12); y: Style.space(12)
          width: parent.width - Style.space(24)
          spacing: Style.space(5)
          Text {
            visible: root.vpn.state === "error"
            text: root.vpn.message && root.vpn.message.indexOf("timed out") >= 0 ? "Token request expired" : "Couldn’t connect"
            color: root.ink
            font.family: root.uiFont
            font.pixelSize: Style.space(13)
            font.weight: Font.DemiBold
          }
          Text {
            width: parent.width
            text: root.feedback
            wrapMode: Text.WordWrap
            color: root.muted
            font.family: root.uiFont
            font.pixelSize: Style.space(12)
            lineHeight: 1.18
            textFormat: Text.PlainText
          }
        }
      }

      Action {
        Layout.fillWidth: true
        visible: root.vpn.state === "connecting" && !!root.vpn.browserUrl
        text: "Open browser · SSO / MFA"
        onClicked: {
          var login = root.vpn.browserUrl || ""
          root.openLogin(login)
        }
      }

      ColumnLayout {
        Layout.fillWidth: true
        visible: root.needsToken
        spacing: Style.space(8)
        Text { text: "Authentication code"; color: root.ink; font.family: root.uiFont; font.pixelSize: Style.space(13); font.weight: Font.Medium }
        TextField {
          id: token
          Layout.fillWidth: true
          implicitHeight: Style.space(44)
          placeholderText: "Enter your token"
          password: true
          maximumLength: 128
          font.family: root.uiFont
          font.pixelSize: Style.space(15)
          horizontalPadding: Style.space(12)
          validator: RegularExpressionValidator { regularExpression: /[A-Za-z0-9-]{0,128}/ }
          inputMethodHints: Qt.ImhSensitiveData | Qt.ImhNoPredictiveText
          background: Rectangle {
            radius: Style.space(7)
            color: root.tint(0.04)
            border.width: 1
            border.color: token.activeFocus ? Color.accent : root.tint(0.2)
          }
          onAccepted: root.submitToken()
          Keys.onEscapePressed: { clear(); root.close() }
        }
        Text { text: "Use the current code from your authenticator."; color: root.muted; font.family: root.uiFont; font.pixelSize: Style.space(11) }
      }

      ColumnLayout {
        Layout.fillWidth: true
        visible: root.connected
        spacing: Style.space(14)
        RowLayout {
          Layout.fillWidth: true
          Repeater {
            model: [ {label: "Download", arrow: "↓", rate: root.vpn.rxRate, total: root.vpn.rx, color: Color.accent}, {label: "Upload", arrow: "↑", rate: root.vpn.txRate, total: root.vpn.tx, color: root.uploadColor} ]
            ColumnLayout {
              required property var modelData
              Layout.fillWidth: true
              spacing: Style.space(4)
              Text { text: modelData.arrow + "  " + modelData.label; color: modelData.color; font.family: root.uiFont; font.pixelSize: Style.space(12); font.weight: Font.Medium }
              Text { text: root.bytes(modelData.rate) + "/s"; color: root.ink; font.family: root.uiFont; font.pixelSize: Style.space(21); font.weight: Font.Medium }
              Text { text: root.bytes(modelData.total) + " total"; color: root.muted; font.family: root.uiFont; font.pixelSize: Style.space(11) }
            }
          }
        }
        Canvas {
          id: traffic
          Layout.fillWidth: true
          implicitHeight: Style.space(40)
          onPaint: {
            var ctx = getContext("2d")
            ctx.reset()
            var rx = root.rxHistory, tx = root.txHistory
            var max = Math.max(1024, ...rx, ...tx)
            ctx.lineWidth = 1
            ctx.strokeStyle = root.tint(0.08)
            ctx.beginPath(); ctx.moveTo(0, height - 1); ctx.lineTo(width, height - 1); ctx.stroke()
            function line(values, color) {
              if (values.length < 2) return
              ctx.strokeStyle = color; ctx.lineWidth = 1.5
              ctx.beginPath()
              for (var i = 0; i < values.length; i++) {
                var x = (30 - values.length + i) * width / 29
                var y = height - 2 - (values[i] / max) * (height - 5)
                if (i === 0) ctx.moveTo(x,y); else ctx.lineTo(x,y)
              }
              ctx.stroke()
            }
            line(tx, root.uploadColor); line(rx, Color.accent)
          }
        }
        Repeater {
          model: [["Connected for", root.duration(root.vpn.uptime)], ["VPN address", root.vpn.address || "—"]]
          RowLayout {
            required property var modelData
            Layout.fillWidth: true
            Text { text: modelData[0]; color: root.muted; font.family: root.uiFont; font.pixelSize: Style.space(12) }
            Text { Layout.fillWidth: true; text: modelData[1]; color: root.ink; horizontalAlignment: Text.AlignRight; font.family: root.uiFont; font.pixelSize: Style.space(12) }
          }
        }
        QQC.Button {
          Layout.fillWidth: true
          implicitHeight: Style.space(22)
          text: root.detailsOpen ? "Connection details  −" : "Connection details  +"
          activeFocusOnTab: true
          contentItem: Text { text: parent.text; color: parent.hovered || parent.activeFocus ? root.ink : root.muted; font.family: root.uiFont; font.pixelSize: Style.space(12); verticalAlignment: Text.AlignVCenter }
          background: Rectangle { color: "transparent" }
          onClicked: root.detailsOpen = !root.detailsOpen
        }
        ColumnLayout {
          Layout.fillWidth: true
          visible: root.detailsOpen
          spacing: Style.space(9)
          Repeater {
            model: [["Interface", root.vpn.interface || "—"], ["MTU / routes", (root.vpn.mtu || "—") + " / " + (root.vpn.routes || 0)], ["Errors / dropped", (root.vpn.errors || 0) + " / " + (root.vpn.drops || 0)], ["DNS", root.vpn.dns || "—"]]
            RowLayout {
              required property var modelData
              Layout.fillWidth: true
              Text { text: modelData[0]; color: root.muted; font.family: root.uiFont; font.pixelSize: Style.space(11) }
              Text { Layout.fillWidth: true; text: modelData[1]; horizontalAlignment: Text.AlignRight; wrapMode: Text.WrapAnywhere; color: root.ink; font.family: root.uiFont; font.pixelSize: Style.space(11); textFormat: Text.PlainText }
            }
          }
        }
      }
      RowLayout {
        Layout.fillWidth: true
        spacing: Style.space(8)
        Action {
          id: primary
          Layout.fillWidth: true
          prominent: !root.connected && !root.busy || root.needsToken
          text: root.needsToken ? "Verify token" : (root.connected || root.vpn.state === "external" ? "Disconnect" : (root.busy ? "Cancel connection" : "Connect"))
          enabled: root.channel && root.channel.connected && root.vpn.state !== "disconnecting" && (!root.needsToken || token.text.length >= 4)
          onClicked: {
            if (root.needsToken) root.submitToken()
            else root.send(root.connected || root.busy ? "disconnect" : "connect")
          }
        }
        Action {
          visible: root.needsToken
          Layout.preferredWidth: Style.space(76)
          text: "Cancel"
          onClicked: { token.clear(); root.send("disconnect") }
        }
      }
    }
  }
}
