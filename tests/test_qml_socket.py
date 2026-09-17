"""Exercise the widget's actual Socket and retry timer against a delayed server."""
import pathlib, subprocess, socket, tempfile, time, os
main=(pathlib.Path(__file__).resolve().parents[1] / 'Main.qml').read_text()
blocks=main[main.index('  readonly property var channel:'):main.index('  BarIconButton {')]
with tempfile.TemporaryDirectory() as tmp:
    path=tmp+'/control.sock'
    qml='''import QtQuick
import Quickshell
import Quickshell.Io
ShellRoot {
 id: root
 FloatingWindow { visible: true; implicitWidth: 1; implicitHeight: 1 }
 property var vpn: ({state: "unavailable"})
 property string requestError: ""
 property var rxHistory: []
 property var txHistory: []
 QtObject { id: traffic; function requestPaint() {} }
 function send(action) { if (!channel || !channel.connected) return; channel.write(JSON.stringify({action:action}) + "\\n"); channel.flush() }
 onVpnChanged: if (vpn.state === "test-connected") { console.log("SOCKET_RECOVERY_OK"); Qt.quit() }
'''+blocks.replace('/run/fortivpn-panel/control.sock',path)+'\n}\n'
    pathlib.Path(tmp+'/shell.qml').write_text(qml)
    proc=subprocess.Popen(['/usr/bin/quickshell','-p',tmp+'/shell.qml'],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,env=dict(os.environ, QT_QPA_PLATFORM='offscreen'))
    try:
        time.sleep(1.5)
        with socket.socket(socket.AF_UNIX) as server:
            server.bind(path); server.listen(); server.settimeout(6)
            conn,_=server.accept()
            with conn:
                conn.settimeout(2)
                assert b'status' in conn.recv(2048)
                conn.sendall(b'{"state":"test-connected"}\n')
        output=proc.communicate(timeout=4)[0]
        assert 'SOCKET_RECOVERY_OK' in output, output
        print('PASS: actual QML socket recovers when service appears after startup')
    finally:
        if proc.poll() is None:
            proc.terminate(); print(proc.communicate(timeout=3)[0])
        elif proc.stdout and not proc.stdout.closed: print(proc.stdout.read())
