#!/usr/bin/python3
"""Fixed-profile VPN controller. No credentials or raw VPN output are persisted."""
import argparse, json, os, pathlib, pwd, re, signal, socket, socketserver, stat, struct, subprocess, threading, time

CONFIG = '/etc/openfortivpn/config'
SOCKET = '/run/fortivpn-panel/control.sock'
ALLOWED_UID = None
COMMAND = ['/usr/bin/openfortivpn', '-c', CONFIG, '--pppd-ifname=fortivpn0']


def matches_profile(args):
    """Only manage the fixed profile, without command-line endpoint overrides."""
    remaining = list(args)
    while remaining:
        arg = remaining.pop(0)
        if arg in ('-c', '--config'):
            if not remaining or remaining.pop(0) != CONFIG:
                return False
        elif arg not in ('-v', '-q', '--config=' + CONFIG, '--pppd-ifname=fortivpn0'):
            return False
    return True


def processes():
    found = []
    for path in pathlib.Path('/proc').glob('[0-9]*/comm'):
        try:
            if path.read_text().strip() != 'openfortivpn' or path.stat().st_uid != 0:
                continue
            if (path.parent / 'exe').resolve() != pathlib.Path('/usr/bin/openfortivpn').resolve():
                continue
            args = (path.parent / 'cmdline').read_bytes().rstrip(b'\0').split(b'\0')
            if matches_profile([arg.decode() for arg in args[1:]]):
                found.append(int(path.parent.name))
        except (OSError, UnicodeError):
            pass
    return found


def ip_json(*args):
    try:
        return json.loads(subprocess.check_output(['/usr/bin/ip', '-j', *args], timeout=2))
    except (OSError, subprocess.SubprocessError, ValueError):
        return []


def profile():
    """Read only public endpoint fields; never expose credentials over the socket."""
    result = {}
    path = pathlib.Path(CONFIG)
    try:
        mode = path.stat()
        if not stat.S_ISREG(mode.st_mode) or mode.st_uid != 0 or mode.st_mode & 0o077:
            return {}
        for line in path.read_text().splitlines():
            key, sep, value = line.partition('=')
            if sep and key.strip() in ('host', 'port'):
                result[key.strip()] = value.strip()
    except OSError:
        return {}
    return result


def classify_output(text):
    low = text.lower()
    if re.search(r'(two.factor|one.time|otp|token|verification code).*[:?]\s*$', low):
        return 'token', 'Enter your current authentication token.'
    if re.search(r'password\s*:\s*$', low):
        return 'error', 'The saved VPN password is missing. Update the VPN configuration.'
    if 'certificate validation failed' in low or 'certificate digest mismatch' in low:
        return 'error', 'VPN certificate verification failed. Check the trusted certificate.'
    if any(s in low for s in ('could not authenticate', 'authentication failed', 'permission denied', 'invalid otp')):
        return 'error', 'Authentication failed. Connect again with a fresh token.'
    if any(s in low for s in ('could not connect', 'connection timed out', 'name or service not known', 'network is unreachable')):
        return 'error', 'Cannot reach the VPN gateway. Check your network and retry.'
    return None


class Controller:
    def __init__(self):
        self.lock = threading.RLock()
        self.proc = None
        self.state = 'disconnected'
        self.message = ''
        self.started = 0
        self.connected_at = 0
        self.interface = ''
        self.previous = None
        self.stop_requested = False
        self.stop_at = 0
        self.stop_error = ''
        self.ready = False
        self.snapshot = {'state': 'disconnected', 'message': ''}
        self.gateway = profile()

    def connect(self):
        with self.lock:
            if (self.proc and self.proc.poll() is None) or processes():
                return
            self.gateway = profile()
            if not self.gateway.get('host'):
                self.state, self.message = 'error', 'Configure a root-owned VPN profile with mode 600 at /etc/openfortivpn/config first.'
                return
            if any(i.get('ifname') == 'fortivpn0' for i in ip_json('link', 'show')):
                self.state, self.message = 'error', 'The fortivpn0 interface is already in use. Disconnect that tunnel first.'
                return
            self.state, self.message = 'connecting', 'Contacting VPN gateway…'
            self.started = time.monotonic()
            self.stop_requested = False
            self.stop_error = ''
            self.ready = False
            self.connected_at = 0
            self.previous = None
            self.proc = subprocess.Popen(COMMAND, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, bufsize=0, start_new_session=True,
                env={'PATH': '/usr/bin:/usr/sbin', 'LANG': 'C', 'HOME': '/root'})
            threading.Thread(target=self.read_output, args=(self.proc,), daemon=True).start()

    def read_output(self, proc):
        buffer = ''
        while True:
            chunk = os.read(proc.stdout.fileno(), 4096)
            if not chunk:
                break
            buffer = (buffer + chunk.decode(errors='replace'))[-8192:]
            with self.lock:
                if 'Tunnel is up and running' in buffer:
                    self.ready = True
                result = classify_output(buffer)
                if result and not self.stop_requested and self.state != 'connected':
                    self.state, self.message = result
                    if self.state == 'error':
                        try: proc.send_signal(signal.SIGINT)
                        except ProcessLookupError: pass
                    buffer = ''
            # Retain the unfinished prompt, not the whole stream.
            if '\n' in buffer:
                buffer = buffer.rsplit('\n', 1)[-1]
        proc.wait()
        proc.stdout.close()
        proc.stdin.close()
        with self.lock:
            if self.proc is proc:
                self.ready = False
                if self.stop_requested:
                    self.state, self.message = ('error', self.stop_error) if self.stop_error else ('disconnected', '')
                elif self.state != 'error':
                    self.state, self.message = 'error', 'VPN connection ended. Connect again to retry.'

    def token(self, value):
        if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9-]{4,128}', value):
            raise ValueError('Enter a valid authentication token.')
        with self.lock:
            if self.state != 'token' or not self.proc or self.proc.poll() is not None:
                raise ValueError('Token request expired. Connect again.')
            self.proc.stdin.write((value + '\n').encode())
            self.proc.stdin.flush()
            self.state, self.message = 'connecting', 'Verifying token…'
            self.started = time.monotonic()

    def disconnect(self):
        with self.lock:
            self.stop_requested = True
            self.stop_at = time.monotonic()
            self.state, self.message = 'disconnecting', 'Disconnecting…'
            for pid in processes():
                try:
                    os.kill(pid, signal.SIGINT)
                except ProcessLookupError:
                    pass

    def refresh(self):
        pids = processes()
        addresses = ip_json('address', 'show')
        routes = ip_json('route', 'show')
        with self.lock:
            own = bool(self.proc and self.proc.poll() is None)
            interfaces = [i for i in addresses if i.get('link_type') == 'ppp' and 'UP' in i.get('flags', [])
                and any(a.get('family') == 'inet' for a in i.get('addr_info', []))]
            # The managed connection has an explicit interface name. For the
            # legacy connection, match its peer to the configured gateway.
            candidates = [i for i in interfaces if i['ifname'] == 'fortivpn0'] if own else [i for i in interfaces
                if any(a.get('address') == self.gateway.get('host') for a in i.get('addr_info', []))]
            iface = candidates[0] if pids and len(candidates) == 1 else None
            if iface and (not own or self.ready) and not self.stop_requested:
                if not self.connected_at or self.interface != iface['ifname']:
                    self.connected_at = time.monotonic()
                    if not own:
                        # Estimate existing tunnel age from its pppd process.
                        try:
                            child = pathlib.Path(f'/proc/{pids[0]}/task/{pids[0]}/children').read_text().split()[0]
                            ticks = float(pathlib.Path(f'/proc/{child}/stat').read_text().split()[21])
                            self.connected_at = ticks / os.sysconf('SC_CLK_TCK')
                        except (OSError, ValueError, IndexError):
                            pass
                self.state, self.message = 'connected', ''
                self.interface = iface['ifname']
            elif not pids:
                if self.stop_requested or self.state == 'connected':
                    self.state, self.message = (('error', self.stop_error) if self.stop_error else ('disconnected', '')) if self.stop_requested else ('error', 'VPN connection dropped. Connect again to retry.')
                self.stop_requested = False
                self.previous = None
                self.connected_at = 0
            elif self.state in ('connecting', 'token') and time.monotonic() - self.started > 120:
                self.disconnect()
                self.stop_error = self.message = 'Connection timed out. Connect again with a fresh token.'
            elif self.stop_requested and time.monotonic() - self.stop_at > 15:
                self.message = 'VPN is taking longer to disconnect. Waiting for network cleanup…'
            elif not own and self.state == 'disconnected':
                self.state, self.message = 'external', 'A terminal VPN is running. Finish authentication there, or disconnect it here.'

            data = {'state': self.state, 'message': self.message, 'gateway': self.gateway.get('host', ''),
                'port': self.gateway.get('port', '443'), 'managed': own, 'uptime': 0,
                'rxRate': 0, 'txRate': 0, 'rx': 0, 'tx': 0, 'errors': 0, 'drops': 0,
                'interface': '', 'address': '', 'mtu': 0, 'routes': 0, 'dns': ''}
            if iface:
                name = iface['ifname']
                stats = pathlib.Path('/sys/class/net') / name / 'statistics'
                def counter(key):
                    try: return int((stats / key).read_text())
                    except (OSError, ValueError): return 0
                rx, tx, now = counter('rx_bytes'), counter('tx_bytes'), time.monotonic()
                if self.previous and self.previous[0] == iface['ifindex']:
                    _, t, oldrx, oldtx = self.previous
                    elapsed = max(0.1, now-t)
                    data.update(rxRate=max(0, rx-oldrx)/elapsed, txRate=max(0, tx-oldtx)/elapsed)
                self.previous = (iface['ifindex'], now, rx, tx)
                addr = next(a for a in iface['addr_info'] if a['family'] == 'inet')
                data.update(interface=name, address=addr['local'], mtu=iface['mtu'], rx=rx, tx=tx,
                    uptime=max(0, int(now-self.connected_at)) if self.connected_at else 0,
                    routes=sum(r.get('dev') == name for r in routes),
                    errors=counter('rx_errors')+counter('tx_errors'),
                    drops=counter('rx_dropped')+counter('tx_dropped'))
                try:
                    data['dns'] = ', '.join(line.split()[1] for line in pathlib.Path('/etc/resolv.conf').read_text().splitlines()
                        if line.startswith('nameserver '))
                except OSError: pass
            self.snapshot = data

    def handle(self, request):
        action = request.get('action')
        if action == 'connect': self.connect()
        elif action == 'disconnect': self.disconnect()
        elif action == 'token': self.token(request.get('token'))
        elif action != 'status': raise ValueError('Unknown action.')
        with self.lock:
            return dict(self.snapshot, state=self.state, message=self.message)


def serve():
    if ALLOWED_UID is None:
        raise ValueError('A local account UID is required.')
    controller = Controller()
    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            _, uid, _ = struct.unpack('3i', self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if uid not in (0, ALLOWED_UID): return
            self.request.settimeout(15)
            while True:
                try:
                    raw = self.rfile.readline(4097)
                    if not raw or len(raw) > 4096: return
                    request = json.loads(raw)
                    if not isinstance(request, dict): return
                    result = controller.handle(request)
                except (ValueError, BrokenPipeError):
                    result = dict(controller.snapshot, error='Request failed or token expired. Please retry.')
                except (OSError, subprocess.SubprocessError):
                    return
                try: self.wfile.write((json.dumps(result)+'\n').encode())
                except OSError: return
    class Server(socketserver.ThreadingUnixStreamServer):
        daemon_threads = True
    def monitor():
        while True:
            try: controller.refresh()
            except (OSError, ValueError, KeyError): pass
            time.sleep(1)
    os.umask(0o077)
    pathlib.Path(SOCKET).parent.mkdir(mode=0o755, exist_ok=True)
    pathlib.Path(SOCKET).unlink(missing_ok=True)
    server = Server(SOCKET, Handler)
    os.chown(SOCKET, ALLOWED_UID, -1)
    os.chmod(SOCKET, 0o600)
    controller.refresh()
    threading.Thread(target=monitor, daemon=True).start()
    server.serve_forever()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--uid', type=int, required=True, help='Local account permitted to control the fixed VPN profile')
    args = parser.parse_args()
    if args.uid <= 0:
        parser.error('Choose a non-root local account.')
    try:
        pwd.getpwuid(args.uid)
    except KeyError:
        parser.error('The account UID does not exist.')
    ALLOWED_UID = args.uid
    serve()
