#!/usr/bin/env bash
# Remove only files installed by this plugin. Preserve the VPN profile.
set -euo pipefail
if [[ ${1:-} == --help || ${1:-} == -h ]]; then
  echo 'Usage: sudo bash scripts/uninstall-controller.sh'
  echo 'Stops the managed VPN and removes the controller; preserves /etc/openfortivpn/config.'
  exit 0
fi
[[ $# -eq 0 ]] || { echo 'Unexpected argument.' >&2; exit 2; }
[[ $EUID -eq 0 ]] || { echo 'Run this uninstaller with sudo.' >&2; exit 1; }
if [[ -e /etc/systemd/system/fortivpn-panel.service ]]; then
  systemctl disable --now fortivpn-panel.service
  rm -f -- /etc/systemd/system/fortivpn-panel.service
fi
rm -f -- /usr/local/lib/fortivpn-panel/service.py
if [[ -d /usr/local/lib/fortivpn-panel ]]; then
  rmdir -- /usr/local/lib/fortivpn-panel || echo 'Additional files remain in the controller directory; preserved them.'
fi
systemctl daemon-reload
echo 'Controller removed. The VPN profile has been preserved.'
