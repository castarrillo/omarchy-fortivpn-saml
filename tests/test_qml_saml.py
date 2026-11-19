"""Verify the real widget parser opens SSO once per connection attempt."""
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading

main = (Path(__file__).resolve().parents[1] / 'Main.qml').read_text()
blocks = main[main.index('  readonly property var channel:'):main.index('  BarIconButton {')]
with tempfile.TemporaryDirectory() as tmp:
    path = tmp + '/control.sock'
    qml = '''import QtQuick
import Quickshell
import Quickshell.Io
ShellRoot {
 id: root
 FloatingWindow { visible: true; implicitWidth: 1; implicitHeight: 1 }
 property var vpn: ({state: "unavailable"})
 property string requestError: ""
 property string openedLoginAttempt: ""
 property var rxHistory: []
 property var txHistory: []
 property int launches: 0
 QtObject { id: traffic; function requestPaint() {} }
 function openLogin(url) { launches++; console.log("LOGIN_OPEN", launches) }
 function validLogin(login, gateway, port) {
   var prefix = "https://" + gateway + ":" + String(port || "443") + "/remote/saml/start?redirect=1"
   return login.indexOf(prefix) === 0 && /^(?:&realm=[A-Za-z0-9%_.~-]*)?$/.test(login.slice(prefix.length))
 }
 function send(action) { if (!channel || !channel.connected) return; channel.write(JSON.stringify({action:action}) + "\\n"); channel.flush() }
 onVpnChanged: if (vpn.state === "test-done") {
   console.log(launches === 2 ? "SAML_RECONNECT_OK" : "SAML_RECONNECT_FAILED")
   Qt.quit()
 }
''' + blocks.replace('/run/fortivpn-panel/control.sock', path) + '\n}\n'
    Path(tmp + '/shell.qml').write_text(qml)
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(path)
        server.listen()
        server.settimeout(5)
        proc = subprocess.Popen(
            ['/usr/bin/quickshell', '-p', tmp + '/shell.qml'],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            env=dict(os.environ, QT_QPA_PLATFORM='offscreen'))
        try:
            conn, _ = server.accept()
            with conn:
                conn.settimeout(4)
                stream = conn.makefile('rb')
                login = 'https://vpn.example.org:10443/remote/saml/start?redirect=1'
                # Duplicate status frames must not launch more windows. A new
                # attempt using the same URL must open the browser again.
                frames = [
                    {'state': 'connecting', 'attemptId': 'one', 'browserUrl': login},
                    {'state': 'connecting', 'attemptId': 'one', 'browserUrl': login},
                    {'state': 'disconnected', 'attemptId': 'one', 'browserUrl': ''},
                    {'state': 'connecting', 'attemptId': 'two', 'browserUrl': ''},
                    {'state': 'connecting', 'attemptId': 'two', 'browserUrl': login},
                    {'state': 'connecting', 'attemptId': 'two', 'browserUrl': login},
                    {'state': 'test-done'},
                ]
                for frame in frames:
                    frame.update(gateway='vpn.example.org', port='10443')
                    assert stream.readline()
                    conn.sendall((json.dumps(frame) + '\n').encode())
            output = proc.communicate(timeout=5)[0]
            assert 'SAML_RECONNECT_OK' in output, output
            print('PASS: SSO opens exactly once for each connection attempt')
        finally:
            if proc.poll() is None:
                proc.terminate()
                proc.communicate(timeout=3)
