#!/usr/bin/env bash
# Install only the fixed-profile controller. Never read or copy VPN credentials.
set -euo pipefail

usage() {
  echo 'Usage: sudo bash scripts/install-controller.sh --user LOCAL_ACCOUNT [--replace]'
  echo 'Installs the controller for one non-root account; --replace explicitly updates an existing controller.'
}
account=''
replace=false
while (( $# )); do
  case "$1" in
    --user) [[ $# -ge 2 ]] || { usage >&2; exit 2; }; account=$2; shift 2 ;;
    --replace) replace=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done
[[ $EUID -eq 0 ]] || { echo 'Run this installer with sudo.' >&2; exit 1; }
[[ -n $account && $account != -* ]] || { usage >&2; exit 2; }
account_uid=$(id -u -- "$account")
[[ $account_uid =~ ^[0-9]+$ && $account_uid -gt 0 ]] || { echo 'Choose a non-root local account.' >&2; exit 2; }

for dependency in /usr/bin/python3 /usr/bin/openfortivpn /usr/bin/pppd /usr/bin/ip /usr/bin/systemctl; do
  [[ -x $dependency ]] || { echo "Missing dependency: $dependency" >&2; exit 1; }
done

plugin_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
controller_dir=/usr/local/lib/fortivpn-panel
unit_path=/etc/systemd/system/fortivpn-panel.service
[[ ! -L $controller_dir && ! -L $unit_path ]] || { echo 'Refusing to replace a symlinked installation.' >&2; exit 1; }
if [[ -e $controller_dir || -e $unit_path ]] && [[ $replace != true ]]; then
  echo 'A controller already exists. Review it and use --replace to update it explicitly.' >&2
  exit 1
fi

umask 077
stage=$(mktemp -d /run/fortivpn-panel-install.XXXXXXXX)
trap 'rm -rf -- "$stage"' EXIT
install -m 644 -- "$plugin_dir/controller/service.py" "$stage/service.py"
sed "s/@UID@/$account_uid/g" "$plugin_dir/controller/fortivpn-panel.service" > "$stage/fortivpn-panel.service"
/usr/bin/python3 -I - "$stage/service.py" <<'PY'
import ast, pathlib, sys
ast.parse(pathlib.Path(sys.argv[1]).read_text())
PY
if [[ $replace == true ]]; then
  echo 'Updating the controller disconnects any tunnel it currently manages.'
  systemctl stop fortivpn-panel.service
fi
install -d -o root -g root -m 755 -- "$controller_dir"
install -o root -g root -m 644 -- "$stage/service.py" "$controller_dir/service.py"
install -o root -g root -m 644 -- "$stage/fortivpn-panel.service" "$unit_path"
systemctl daemon-reload
systemctl enable --now fortivpn-panel.service
systemctl is-active --quiet fortivpn-panel.service
echo "Controller installed for local UID $account_uid. The VPN connects only when requested."
echo 'The VPN configuration was not created, read, or changed by this installer.'
