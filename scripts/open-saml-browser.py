#!/usr/bin/python3
"""Open a fresh SSO window and focus that window, not an older browser."""
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
from urllib.parse import urlsplit


def clients():
    try:
        result = subprocess.run(['/usr/bin/hyprctl', 'clients', '-j'],
                                capture_output=True, text=True, timeout=3, check=True)
        return json.loads(result.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return []


def main():
    if len(sys.argv) != 2:
        return 2
    try:
        url = urlsplit(sys.argv[1])
        valid = (url.scheme == 'https' and url.hostname and not url.username
                 and not url.password and not url.fragment and url.port
                 and url.path == '/remote/saml/start'
                 and re.fullmatch(r'redirect=1(?:&realm=[A-Za-z0-9%_.~-]*)?', url.query))
    except ValueError:
        return 2
    if not valid:
        return 2
    before = {c.get('address') for c in clients()}
    # Launch the actual browser, avoiding singleton handoff optimizations that
    # can leave the page in an old window on another workspace.
    desktop = subprocess.check_output(['/usr/bin/omarchy-cmd-default-browser'],
                                      text=True, timeout=5).strip()
    if not re.fullmatch(r'[A-Za-z0-9_.-]+\.desktop', desktop):
        return 2
    directories = [os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share'))]
    directories += os.environ.get('XDG_DATA_DIRS', '/usr/local/share:/usr/share').split(':')
    command = []
    for directory in directories:
        entry = Path(directory) / 'applications' / desktop
        if entry.is_file():
            for line in entry.read_text().splitlines():
                if line.startswith('Exec='):
                    command = [arg for arg in shlex.split(line[5:]) if not re.fullmatch(r'%[fFuUiIcCkK]', arg)]
                    break
            break
    if not command:
        return 2
    subprocess.Popen(command + ['--new-window', sys.argv[1]],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        fresh = [c for c in clients() if c.get('address') not in before
                 and re.search(r'brave|chromium|chrome|firefox|edge', c.get('class', ''), re.I)]
        if fresh:
            address = fresh[-1]['address']
            if re.fullmatch(r'0x[0-9a-fA-F]+', address):
                subprocess.run(['/usr/bin/hyprctl', 'dispatch',
                                'hl.dsp.focus({ window = "address:' + address + '" })'],
                               timeout=3, capture_output=True)
            return 0
        time.sleep(0.1)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
