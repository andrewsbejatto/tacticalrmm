#!/usr/bin/env bash
#
# Installs the RustDesk server (hbbs + hbbr) on the Tactical RMM server, for the
# TRMM Desktop remote access, and saves the address + key in Tactical RMM.
#
#   sudo ./install-rustdesk-server.sh <public host or ip of this server> [version]
#
# Ports to open in the firewall / router (forward to this server):
#   21115/tcp  21116/tcp  21116/udp  21117/tcp
# (21118/tcp and 21119/tcp only for web clients, not used by TRMM Desktop)

set -euo pipefail

HOST="${1:-}"
VERSION="${2:-1.1.16}"
if [ -z "$HOST" ]; then
    echo "usage: sudo $0 <public host or ip> [rustdesk-server version]"
    exit 1
fi
# host name or ip only (it goes into the systemd unit and the TRMM settings)
if ! printf '%s' "$HOST" | grep -Eq '^[A-Za-z0-9.-]+$'; then
    echo "invalid host: $HOST"
    exit 1
fi
if [ "$(id -u)" -ne 0 ]; then
    echo "run as root (sudo)"
    exit 1
fi

ARCH="$(dpkg --print-architecture)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
BASE="https://github.com/rustdesk/rustdesk-server/releases/download/${VERSION}"

# SHA256 of the official 1.1.16 packages (other versions: set
# TRMM_SKIP_HASH=1 after checking them yourself)
expected_sha256() {
    case "$1" in
        rustdesk-server-hbbs_1.1.16_amd64.deb) echo 7d007f6cc9392b94d3b2d545b288299c1cabced9aeee20e1319cf703fc4a2904 ;;
        rustdesk-server-hbbr_1.1.16_amd64.deb) echo e829f6ed12a5c3d2cede949bffff1821593f7da4df4f32fb483ea4c7440ed360 ;;
        rustdesk-server-utils_1.1.16_amd64.deb) echo ac4d76c44abe7dd699a7337e6335031b0a835751a33a436ed561b95a14d02f54 ;;
        rustdesk-server-hbbs_1.1.16_arm64.deb) echo ba1e38e48533a9381e4974d08b5ea9e032663205667171d7eea4c489daf6ac84 ;;
        rustdesk-server-hbbr_1.1.16_arm64.deb) echo b416c1bff182b6ae41a170ded92fbe9970d6ebc72439787ff324a381bd2701ed ;;
        rustdesk-server-utils_1.1.16_arm64.deb) echo f77cca20a51f0f1df8879708bff769d244b865e6b4fcd063b5db2d66169794a9 ;;
        *) echo "" ;;
    esac
}

echo "== downloading rustdesk-server ${VERSION} (${ARCH})"
for pkg in hbbs hbbr utils; do
    name="rustdesk-server-${pkg}_${VERSION}_${ARCH}.deb"
    curl -fsSL -o "$TMP/rustdesk-server-${pkg}.deb" "${BASE}/${name}"
    want="$(expected_sha256 "$name")"
    if [ -n "$want" ]; then
        got="$(sha256sum "$TMP/rustdesk-server-${pkg}.deb" | cut -d' ' -f1)"
        if [ "$got" != "$want" ]; then
            echo "checksum mismatch for $name"
            exit 1
        fi
    elif [ "${TRMM_SKIP_HASH:-0}" != "1" ]; then
        echo "no pinned checksum for $name (set TRMM_SKIP_HASH=1 to install anyway)"
        exit 1
    fi
done

echo "== installing"
DEBIAN_FRONTEND=noninteractive apt-get install -y \
    "$TMP/rustdesk-server-hbbs.deb" "$TMP/rustdesk-server-hbbr.deb" "$TMP/rustdesk-server-utils.deb"

# encrypted connections only (-k _) and the public relay address for clients
mkdir -p /etc/systemd/system/rustdesk-hbbs.service.d /etc/systemd/system/rustdesk-hbbr.service.d
cat > /etc/systemd/system/rustdesk-hbbs.service.d/trmm.conf <<EOF
[Service]
ExecStart=
ExecStart=/usr/bin/hbbs -r ${HOST}:21117 -k _
EOF
cat > /etc/systemd/system/rustdesk-hbbr.service.d/trmm.conf <<EOF
[Service]
ExecStart=
ExecStart=/usr/bin/hbbr -k _
EOF

systemctl daemon-reload
systemctl enable rustdesk-hbbs rustdesk-hbbr >/dev/null
systemctl restart rustdesk-hbbs rustdesk-hbbr

KEYFILE=/var/lib/rustdesk-server/id_ed25519.pub
for _ in $(seq 1 20); do
    [ -s "$KEYFILE" ] && break
    sleep 1
done
if [ ! -s "$KEYFILE" ]; then
    echo "hbbs did not create $KEYFILE, check: journalctl -u rustdesk-hbbs"
    exit 1
fi
KEY="$(cat "$KEYFILE")"

if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then
    echo "== opening ports in ufw"
    ufw allow 21115:21117/tcp >/dev/null
    ufw allow 21116/udp >/dev/null
fi

# save in tactical rmm (standard install)
if [ -x /rmm/api/env/bin/python ]; then
    echo "== saving in Tactical RMM"
    TRMM_USER="$(stat -c '%U' /rmm)"
    sudo -u "$TRMM_USER" /rmm/api/env/bin/python /rmm/api/tacticalrmm/manage.py \
        remoteaccess_setup --id-server "$HOST" --key "$KEY"
    # TRMM Desktop builds served to the managed computers (REMOTEACCESS_DIR)
    mkdir -p /opt/trmm-desktop
    chown "$TRMM_USER":"$TRMM_USER" /opt/trmm-desktop
fi

echo
echo "RustDesk server installed."
echo "  id server : ${HOST}"
echo "  relay     : ${HOST}:21117"
echo "  key       : ${KEY}"
echo
echo "Open/forward to this server: 21115/tcp 21116/tcp 21116/udp 21117/tcp"
