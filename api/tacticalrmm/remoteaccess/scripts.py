"""
Scripts that install / configure the TRMM Desktop host on managed computers.

The host is the TRMM Desktop build (RustDesk based) installed as a service;
the id/relay server and key of this TRMM are applied with --config. Scripts print markers that
the backend parses:

    TRMMRA-ID:<rustdesk id>
    TRMMRA-OK:<message>      or      TRMMRA-ERR:<message>

Placeholders are replaced with str.replace (no format braces to escape).
"""

import re

# names that come from the TRMM Desktop build (APP_NAME / binary name)
WIN_SERVICE = "TRMMDesktop"
WIN_EXE = r"C:\Program Files\TRMMDesktop\trmmdesktop.exe"
LINUX_BIN = "/usr/bin/trmmdesktop"
LINUX_SERVICE = "trmmdesktop"

_WIN_HOST = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $exe = '__EXE__'
    # work folder only SYSTEM/Administrators can write (ProgramData lets users
    # create files: they could swap the setup or plant a DLL next to it)
    $work = Join-Path $env:SystemRoot ('Temp\trmmdesktop-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Force $work | Out-Null
    & icacls.exe $work /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
    if ('__INSTALL__' -eq '1') {
        $setup = Join-Path $work 'trmmdesktop-setup.exe'
        Invoke-WebRequest -UseBasicParsing -Uri '__DOWNLOAD__' -OutFile $setup
        if ((Get-FileHash $setup -Algorithm SHA256).Hash.ToLower() -ne '__SHA256__') {
            throw 'installer checksum mismatch'
        }
        Start-Process -FilePath $setup -ArgumentList '--silent-install' -Wait
        Remove-Item $setup -Force -ErrorAction SilentlyContinue
    }
    for ($i = 0; ($i -lt 60) -and -not (Test-Path $exe); $i++) { Start-Sleep 2 }
    if (-not (Test-Path $exe)) { throw 'TRMM Desktop is not installed' }
    if ('__CONFIG__') { & $exe --config '__CONFIG__' | Out-Null }
    # managed computer: the app hides the TRMM screens (flutter trmm_native.dart)
    $flagDir = Join-Path $env:ProgramData 'TRMMDesktop'
    New-Item -ItemType Directory -Force $flagDir | Out-Null
    Set-Content -Path (Join-Path $flagDir 'host') -Value 'managed by Tactical RMM'
    # no desktop shortcut on managed computers (older builds created one)
    Remove-Item (Join-Path $env:PUBLIC 'Desktop\TRMMDesktop.lnk') -Force -ErrorAction SilentlyContinue
    $svc = Get-Service -Name '__SERVICE__' -ErrorAction SilentlyContinue
    if ($svc -and $svc.Status -ne 'Running') { Start-Service '__SERVICE__' }
    # security settings of a managed computer, explicit (not only defaults)
    foreach ($opt in @(@('approve-mode', 'password'), @('verification-method', 'use-permanent-password'), @('allow-hide-cm', 'Y'))) {
        & $exe --option $opt[0] $opt[1] | Out-Null
    }
    # the password goes to the running server over IPC, retry while it starts.
    # It is read from a protected file (never visible in the process list)
    $done = $false
    for ($i = 0; ($i -lt 15) -and -not $done; $i++) {
        Start-Sleep 3
        $pwFile = Join-Path $work 'pw.txt'
        Set-Content -Path $pwFile -Value '__PASSWORD__' -NoNewline -Encoding ASCII
        $out = (& $exe --password-file $pwFile | Out-String).Trim()
        Remove-Item $pwFile -Force -ErrorAction SilentlyContinue
        if ($out -match 'Done') { $done = $true }
    }
    if (-not $done) { throw "unable to set the password: $out" }
    # session recording (recordings app): upload url + token of this device,
    # empty file = off. Older builds ignore the option.
    if ('__RECORD_SET__' -eq '1') {
        $recFile = Join-Path $work 'rec.json'
        Set-Content -Path $recFile -Value '__RECORD__' -NoNewline -Encoding ASCII
        & $exe --record-config $recFile | Out-Null
        Remove-Item $recFile -Force -ErrorAction SilentlyContinue
    }
    $id = ''
    for ($i = 0; ($i -lt 10) -and -not $id; $i++) {
        $id = (& $exe --get-id | Out-String).Trim()
        if (-not $id) { Start-Sleep 2 }
    }
    if (-not $id) { throw 'unable to read the id' }
    Write-Output "TRMMRA-ID:$id"
    Write-Output 'TRMMRA-OK:ok'
} catch {
    Write-Output ("TRMMRA-ERR:" + $_.Exception.Message)
} finally {
    if ($work) { Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue }
}
"""

_LINUX_HOST = r"""
set -u
err() { echo "TRMMRA-ERR:$1"; exit 0; }
BIN='__BIN__'
if [ '__INSTALL__' = '1' ]; then
    TMP=$(mktemp -d)
    curl -fsSL -o "$TMP/trmmdesktop.deb" '__DOWNLOAD__' || err 'download failed'
    echo "__SHA256__  $TMP/trmmdesktop.deb" | sha256sum -c - >/dev/null 2>&1 || err 'installer checksum mismatch'
    if command -v apt-get >/dev/null 2>&1; then
        DEBIAN_FRONTEND=noninteractive apt-get install -y "$TMP/trmmdesktop.deb" >/dev/null 2>&1 || err 'apt install failed'
    else
        err 'only debian/ubuntu (apt) is supported'
    fi
    rm -rf "$TMP"
fi
[ -x "$BIN" ] || err 'TRMM Desktop is not installed'
systemctl enable --now '__SERVICE__' >/dev/null 2>&1
if [ -n '__CONFIG__' ]; then sleep 3; "$BIN" --config '__CONFIG__' >/dev/null 2>&1; fi
# managed computer: the app hides the TRMM screens
mkdir -p /etc/trmmdesktop && echo 'managed by Tactical RMM' > /etc/trmmdesktop/host
# security settings of a managed computer, explicit (not only defaults)
"$BIN" --option approve-mode password >/dev/null 2>&1
"$BIN" --option verification-method use-permanent-password >/dev/null 2>&1
"$BIN" --option allow-hide-cm Y >/dev/null 2>&1
# the password is read from a root-only file: never in `ps`/proc cmdline
OK=0
for i in $(seq 1 15); do
    sleep 3
    PWF=$(mktemp)
    chmod 600 "$PWF"
    printf '%s' '__PASSWORD__' > "$PWF"
    if "$BIN" --password-file "$PWF" 2>/dev/null | grep -q Done; then OK=1; fi
    rm -f "$PWF"
    [ "$OK" = 1 ] && break
done
[ "$OK" = 1 ] || err 'unable to set the password'
# session recording (recordings app): upload url + token, empty file = off
if [ '__RECORD_SET__' = '1' ]; then
    RECF=$(mktemp)
    chmod 600 "$RECF"
    printf '%s' '__RECORD__' > "$RECF"
    "$BIN" --record-config "$RECF" >/dev/null 2>&1
    rm -f "$RECF"
fi
ID=$("$BIN" --get-id 2>/dev/null | tr -d '[:space:]')
[ -n "$ID" ] || err 'unable to read the id'
echo "TRMMRA-ID:$ID"
echo 'TRMMRA-OK:ok'
"""


_HOST_RE = re.compile(r"^[A-Za-z0-9.\-]+(:[0-9]{1,5})?$")
_KEY_RE = re.compile(r"^[A-Za-z0-9+/=]*$")
# {"url":"https://...","token":"..."}: nothing that breaks a quoted string
_RECORD_RE = re.compile(r'^[A-Za-z0-9{}":,./_\-]*$')


def server_config(id_server: str, relay_server: str, key: str) -> str:
    """"host=...,key=...,relay=..." for `trmmdesktop --config` (RustDesk custom_server.rs)."""
    id_server, relay_server, key = id_server.strip(), relay_server.strip(), key.strip()
    if not id_server:
        return ""
    if not _HOST_RE.match(id_server) or (relay_server and not _HOST_RE.match(relay_server)):
        raise ValueError("Invalid remote access server address")
    if not _KEY_RE.match(key):
        raise ValueError("Invalid remote access public key")
    parts = [f"host={id_server}", f"key={key}"]
    if relay_server:
        parts.append(f"relay={relay_server}")
    return ",".join(parts)


def embedded_config(
    mode: str, id_server: str, relay_server: str, key: str, extra: dict | None = None
) -> str:
    """
    Block appended to the end of the TRMM Desktop executable, read by the
    portable packer and the app (src/common.rs read_trmm_embedded):
    #TRMMCFG2#<mode>#<base64 json {host,key,relay,...}>#<base64 ed25519 signature>#TRMMEND#
    The signature covers "<mode>#<base64 json>" and is checked by the app with the
    public keys built into it (crypto.signing_public_key).
    mode: qs | host. extra: more keys for the app (quick support: trmm_api, trmm_token)
    """
    from .crypto import sign
    import base64
    import json

    if mode not in ("qs", "host"):
        raise ValueError("Invalid mode")
    server_config(id_server, relay_server, key)  # validates
    data = {"host": id_server.strip(), "key": key.strip(), "relay": relay_server.strip()}
    data.update(extra or {})
    b64 = base64.b64encode(json.dumps(data, separators=(",", ":")).encode()).decode()
    payload = mode + "#" + b64
    return "\r\n#TRMMCFG2#" + payload + "#" + sign(payload.encode()) + "#TRMMEND#"


def _fill(template: str, values: dict[str, str]) -> str:
    for key, value in values.items():
        template = template.replace(f"__{key}__", value)
    return template


def host_script(
    *,
    plat: str,
    password: str,
    download_url: str = "",
    sha256: str = "",
    install: bool,
    config: str = "",
    record: str | None = None,
) -> str:
    """
    Install (optional) + set password + print id, for the agent's platform.
    record: JSON for --record-config ("" = recording off), None = don't touch
    (the combined installer applies it after the enroll).
    """
    if record and not _RECORD_RE.match(record):
        raise ValueError("Invalid recording config")
    values = {
        "INSTALL": "1" if install else "0",
        "DOWNLOAD": download_url,
        "SHA256": sha256.lower(),
        "CONFIG": config,
        "RECORD_SET": "0" if record is None else "1",
        # user values last: their text is never scanned for placeholders
        "RECORD": record or "",
        "PASSWORD": password,
    }
    if plat == "windows":
        values.update({"EXE": WIN_EXE, "SERVICE": WIN_SERVICE})
        return _fill(_WIN_HOST, values)
    if plat == "linux":
        values.update({"BIN": LINUX_BIN, "SERVICE": LINUX_SERVICE})
        return _fill(_LINUX_HOST, values)
    raise ValueError(f"Unsupported platform: {plat}")


def parse_result(output: str) -> tuple[bool, str, str]:
    """Returns (ok, rustdesk_id, message) from a script output."""
    output = output or ""
    remote_id = ""
    m = re.search(r"TRMMRA-ID:\s*(\S+)", output)
    if m:
        remote_id = m.group(1).strip()
    err = re.search(r"TRMMRA-ERR:(.*)", output)
    if err:
        return False, remote_id, err.group(1).strip()[:1000]
    if "TRMMRA-OK" in output and remote_id:
        return True, remote_id, "ok"
    return False, remote_id, (output.strip()[-1000:] or "no output from the agent")
