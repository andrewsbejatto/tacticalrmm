# Tactical RMM – community fork (`trmm-custom`)

This is an **unofficial fork** of [**Tactical RMM**](https://github.com/amidaware/tacticalrmm)
by AmidaWare LLC. It is not affiliated with, endorsed or supported by AmidaWare.
For the original project, its documentation and support, go to the
[upstream repository](https://github.com/amidaware/tacticalrmm) and
[docs.tacticalrmm.com](https://docs.tacticalrmm.com).

The `trmm-custom` branch is the **official Tactical RMM v1.6.1** plus the
features below. They were built by an MSP for its own daily use and are shared
here for the community. The dashboard changes live in the matching fork
[andrewsbejatto/tacticalrmm-web](https://github.com/andrewsbejatto/tacticalrmm-web/tree/trmm-custom)
(branch `trmm-custom`). The desktop app is
[andrewsbejatto/rustdesk](https://github.com/andrewsbejatto/rustdesk/tree/trmm-desktop)
(branch `trmm-desktop`).

> Some of these features were also proposed upstream as separate pull requests
> (per-user language, server power settings, Brazilian Portuguese translation).

## What is new

### Quick Support (remote help for computers without an agent)
- A small Windows executable (C#, .NET Framework 4, no install, no admin
  rights) that the customer downloads and runs. It shows a **9-digit code**.
  Source and build script in [`quicksupport-client/`](quicksupport-client).
- The technician types the code in the dashboard (or the desktop app) and opens
  MeshCentral **remote desktop, terminal and files**. The session runs in a
  dedicated MeshCentral device group and is matched by a secret per-session
  tag. The device is removed when the session ends.
- The window follows the session:
  - "please wait" while the agent is downloaded;
  - "ready" only once the technician can really connect;
  - who is connected, including reconnects from the MeshCentral window.
- **Install permanent access:** the customer asks for it and a technician
  approves it, choosing client, site and type. The Tactical agent is then
  installed with a one-time, 2-hour installer token. The elevated process
  checks the script hash before running it.
- Company name, welcome message, privacy bar and Windows notification
  (both off by default), and a timeout for sessions without contact.
- New role permission **Use Quick Support**. Every action goes to the audit
  log. Rate limits per IP, and restricted technicians only see their own
  sessions.

### Remote access with TRMM Desktop (RustDesk engine)
New `remoteaccess` app for the [TRMM Desktop](https://github.com/andrewsbejatto/rustdesk/tree/trmm-desktop)
app, a RustDesk fork with the Tactical RMM screens:
- **Host installation through the Tactical agent:**
  - Windows and Linux;
  - one install, repair or password change, or a **queued bulk install /
    update** for a site, a client or everything;
  - 3 computers at a time, so Celery is never flooded.
- A strong fixed **password per computer**, stored encrypted. The ID and
  password are only handed to the TRMM Desktop app, with an audit log entry.
- **Combined installer** (Tactical agent + remote access). The remote access
  is registered even if the agent install fails. Enrollment uses **signed,
  per-site tickets**, so a leaked installer can't hijack other computers.
- **TRMM Desktop quick support** executable. The server signs its embedded
  configuration with its own **Ed25519 key**, and the app only accepts the
  keys compiled into its build.
- [`rustdesk/install-rustdesk-server.sh`](rustdesk/install-rustdesk-server.sh):
  installs hbbs/hbbr (official packages, pinned SHA-256, encrypted connections
  only) on the Tactical server and saves the address and key.
- New role permission **Use Remote Access**.
- Management commands `remoteaccess_diag`, `remoteaccess_setup` and
  `remoteaccess_signing_key`.

### Session recordings to Amazon S3 (or any S3-compatible storage)
New `recordings` app:
- **What is recorded:**
  - MeshCentral desktop sessions (`.mcrec`, recorded by MeshCentral);
  - TRMM Desktop sessions (`.webm`). RustDesk is end-to-end encrypted, so
    the remote computer records and streams the video to Tactical **during**
    the session.
- Files go to S3 (AWS, Wasabi, Backblaze B2, MinIO...). The local copy is
  deleted after a verified upload, and retention is the bucket's lifecycle
  rule.
- List, filters and playback in the dashboard and in the app through
  short-lived presigned links. `.mcrec` files play in the ported MeshCentral
  player.
- New role permission **View session recordings**, filtered by the
  clients and sites the user can see. Every playback is in the audit log.
- Commands `recordings_setup_mesh` (turns recording on in MeshCentral's
  `config.json`, with a backup) and `recordings_diag`.

### Other changes
- **Dashboard language per user** (`User.language`), used by the translated
  dashboard (English + Brazilian Portuguese).
- **Server agents never sleep:** any agent installed as *Server* disables
  sleep/hibernate, as if the option was ticked. This covers the dashboard,
  deployments and the combined installers.
- **`CUSTOM_WEBTAR_URL`** in `local_settings.py` (https only): makes
  `update.sh` install a self-built dashboard instead of the official one.
- The new database columns have **database defaults**, so the official code
  run by `update.sh` can still create users and roles before this fork is
  applied again.

## Using it

**Requirements:**
- The branch is based on **Tactical RMM v1.6.1**, standard install
  (`install.sh`). Docker is not covered.
- MeshCentral quick support is only fast with the MeshAgent fix from
  [Ylianst/MeshAgent#476](https://github.com/Ylianst/MeshAgent/pull/476): the
  screen is captured in a separate process in `connect` mode.
- Session recordings need `boto3` (already in `requirements.txt`).

**To apply it on a v1.6.1 server:**
- get the changes with `git diff v1.6.1 trmm-custom`;
- run `manage.py migrate`;
- install the dashboard from the web fork;
- copy the quick support / TRMM Desktop executables to `/opt` (see
  `QUICKSUPPORT_CLIENT_EXE` and `REMOTEACCESS_DIR`).

The official `update.sh` resets `/rmm`, so the changes have to be applied
again after it. **Test on a non-production server first and take a backup.**

## License

Tactical RMM is licensed under the **Tactical RMM License Version 1.0** (see
[LICENSE.md](LICENSE.md)), which is not an open-source license. It restricts
commercial / for-profit services built on the software. This fork follows the
same license, and its full source is published here as the license requires
for derivative works.

    Copyright © 2022 AmidaWare LLC.
    Licensed under the Tactical RMM License Version 1.0 (the “License”).
    You may only use the Licensed Software in accordance with the License.
    A copy of the License is available at: https://license.tacticalrmm.com

"Tactical RMM" is a trade mark of AmidaWare LLC.

---

### Português (resumo)

Fork não oficial do [Tactical RMM](https://github.com/amidaware/tacticalrmm)
v1.6.1, feito por um MSP brasileiro. Traz:
- Suporte Rápido por código para computadores sem agente;
- acesso remoto com o app TRMM Desktop (motor RustDesk), com instalação em
  massa;
- gravação das sessões no S3;
- painel em português do Brasil;
- servidores sem suspensão;
- permissões novas por Função.

Não tem relação com a AmidaWare. Use por sua conta e teste antes em um
servidor de homologação.
