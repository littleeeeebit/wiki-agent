# Mobile companion

The PC owns projects, conversations and agent execution. A paired phone opens
the same server in its browser, checks tasks and changes, and sends instructions
or answers approvals. It connects over mobile data or another Wi-Fi network
without a VPN or router port forwarding. Both networks must permit the relay
connection, and the PC and app must remain awake and online.

## Connect a phone

1. Update the app dependencies and build the screen, then restart wiki-agent.

   ```powershell
   python -m pip install -r requirements-chat.txt
   npm --prefix web ci
   npm --prefix web run build
   ```

   If the app uses `.venv`, use `.venv/Scripts/python.exe` on Windows or
   `.venv/bin/python` on macOS/Linux for the first command.

2. Install `cloudflared` if it is not already installed. On Windows:

   ```powershell
   winget install --id Cloudflare.cloudflared
   ```

   Restart the desktop app after installation. Other platforms follow the
   [Cloudflare installation guide](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/downloads/).

3. On the PC, open Settings → Mobile (`설정 → 휴대폰`) and press
   Enable external connection (`외부 연결 켜기`).
4. Press Create pairing link (`연결 링크 만들기`). Scan the displayed QR code
   with your phone camera and open the link within five minutes. Copy link
   remains available as a fallback. Each QR/link works once; creating another
   invalidates the previous unused link. Anyone who redeems it can control
   the PC's work, so keep it private.
5. Use Tasks, Conversation and Selected task in the bottom navigation.
   Selecting a task opens its agent and review. The native desktop terminal
   stays in the desktop window. Browser menus offer Add to Home Screen;
   the companion needs a live connection and does not cache conversations
   for offline use.
6. To disconnect every phone, use Disable connection and revoke devices
   (`연결 끄기 · 기기 해제`) on the PC.

Pairing lasts up to 30 days at the same hostname. The default is a temporary
Cloudflare URL: starting the tunnel again changes the address and requires a
new link. Cloudflare describes these tunnels as development services without
an uptime guarantee; a fixed named tunnel is the production option.
[Cloudflare Quick Tunnels](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)

The phone's network can change while the PC keeps executing. If the screen
loses its connection, press Reconnect (`다시 연결`) to reload saved records
and reattach to active turns. An instruction whose delivery is uncertain is
never automatically resubmitted.

## Screen modes

On the phone, open Settings → Mobile → Screen mode
(`설정 → 휴대폰 → 화면 모드`). Choose Portrait (`세로 모드`) for one pane at a
time and bottom navigation, or Landscape (`가로 모드`) for the desktop-style
task list, conversation and selected task side by side. Turn the phone sideways
when using Landscape. The native terminal remains desktop-only.

The default is Portrait. The choice applies immediately and is saved in this
browser at the current hostname; rotating or resizing never changes a paired
phone's selected mode. Switching modes keeps conversations and task panes
mounted. A local desktop window still adapts to its width. A new temporary
tunnel hostname has separate browser storage and starts in Portrait again.

## Fixed address

For an existing named Cloudflare Tunnel and domain, start the server with its
public HTTPS origin. The operator owns this named tunnel's lifecycle.

```powershell
python tool/main --port 8787 --mobile-origin https://wiki.example.com
```

Configure the hostname's tunnel service as `http://127.0.0.1:8787` and its
HTTP Host Header as `mobile.wiki-agent.invalid`. In a local tunnel config:

```yaml
ingress:
  - hostname: wiki.example.com
    service: http://127.0.0.1:8787
    originRequest:
      httpHostHeader: mobile.wiki-agent.invalid
  - service: http_status:404
```

Keep the tunnel's existing name and credentials configuration, then follow
Cloudflare's [named tunnel instructions](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/local-management/create-local-tunnel/).
The origin must match the phone's address exactly. Disabling mobile access
invalidates every pairing token even when the tunnel is managed separately.
Restart the server with the flag to enable that fixed origin again.

## Access and transport

The Python listener stays on localhost. The tunnel overwrites `Host` with a
dedicated value; forwarded requests cannot claim local desktop privileges.
Other hosts and cross-origin writes are rejected. Remote API requests require
a signed, expiring `Secure`, `HttpOnly`, `SameSite=Strict`, host-only cookie.
The signing key stays in the ignored `raw/chat/mobile-key`. Revocation rotates
it and closes active companion sockets.

The pairing secret travels in a link fragment, removed from browser history
before pairing. It is not part of an HTTP request URL or relay access log.
The QR code is rendered locally with `qrcode.react`; no third-party QR service
receives the pairing link. It uses the same expiry and single-use secret as
the copyable link and disappears when its displayed lifetime ends.
Authenticated application responses use `Cache-Control: no-store`. The relay
terminates HTTPS and can observe traffic; this is not end-to-end encryption
between phone and desktop.

Remote API traffic uses WebSocket binary frames because temporary Cloudflare
tunnels do not support SSE. The bridge invokes the existing HTTP routes and
middleware, preserving project ownership and event ordering. It never accepts
a client-selected Host, Origin or cookie header. Local desktop requests still
use HTTP. The added Python dependency is Uvicorn's `wsproto` implementation.

## Verification

```powershell
python -m pytest -q tool/test_mobile.py tool/test_main.py
python web/tests/mobile_browser.py
python web/tests/mobile_browser.py --live-tunnel
```

The browser script needs the installed Playwright package and Chromium. It
starts a separate synthetic fixture server; the live option publishes only
that fixture and stops its own tunnel afterwards. Checks cover 320px and
400px phones, both manual modes, saved preferences, rotation independence,
QR regeneration, desktop, touch targets, input sizes, pairing, reload
and streamed requests through a real HTTPS tunnel. API tests cover expired
or replayed links, forged cookies, origin and project guards, stream ordering
and revocation.

Physical-device acceptance remains necessary: open on iOS Safari and Android
Chrome, type with the keyboard visible, check landscape and safe areas, add to
the home screen, then change between Wi-Fi and mobile data and reconnect.
Browser emulation does not verify those platform behaviors.
