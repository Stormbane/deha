# Install: deha (the body)

**Optional install — only do this if you have an ESP32-S3-BOX-3 (ideally the
BOX-3-Sensor variant) you want Narada to embody.** The core install (smriti +
prana + svapna + Claude Code + `~/.narada/` scaffolding) is a separate
prerequisite documented elsewhere.

This doc is the verified recovery path used 2026-05-17 when the original
Docker data was lost. Steps marked **(verified)** were run end-to-end during
that recovery; steps marked **(untested in this pass)** are documented from
the existing scripts/configs but not re-validated.

---

## Prerequisites

- Windows 11 with Docker Desktop (WSL2 backend) installed.
- Anaconda Python (the supervisor uses `pythonw.exe` from anaconda).
- NVIDIA GPU + drivers — optional but recommended for STT/TTS performance.
- ESPHome installed for firmware flashes:
  `pip install esphome` (in a separate venv is fine).
- The deha repo cloned at `C:\Projects\deha`.
- A BOX-3 on the LAN. Note its IP; this guide assumes `192.168.86.35`.

## Storage layout — DO NOT keep Docker data on C:

Docker's WSL2 backend defaults to `%LOCALAPPDATA%\Docker\wsl\disk\docker_data.vhdx`
on C:. A single bad training run can fill C: and crash the host. Move Docker
data to a roomier drive (this guide uses E:) before installing anything.

On Docker Desktop 4.73+ the "Disk image location" GUI setting is hidden on the
WSL2 backend. Use the junction approach:

1. Quit Docker Desktop entirely. Then `wsl --shutdown`.
2. **VERIFY the source VHDX is not held open** before moving — use
   `Get-FileHash` on the source, copy to E:, then `Get-FileHash` on the
   destination. **Sizes matching is NOT enough** — NTFS preallocates, so a
   partially-written file shows full size. Always hash-verify.
3. `Move-Item $env:LOCALAPPDATA\Docker\wsl\disk\docker_data.vhdx E:\Docker\wsl\disk\docker_data.vhdx`
4. `Remove-Item $env:LOCALAPPDATA\Docker\wsl\disk` (now empty)
5. `cmd /c mklink /J "$env:LOCALAPPDATA\Docker\wsl\disk" "E:\Docker\wsl\disk"`
6. Launch Docker Desktop. Verify containers + images come back.

Container bind-mount targets also live off C: — see each section below.

---

## Home Assistant container (verified)

Stores the user's config, automations, custom_components, and history database
at `C:\Users\admin\ha-config\`. Bind-mounted in so all state survives container
recreation.

```powershell
docker pull ghcr.io/home-assistant/home-assistant:stable

docker run -d `
  --name homeassistant `
  --restart unless-stopped `
  -e TZ=Australia/Brisbane `
  -v "C:\Users\admin\ha-config:/config" `
  -p 8123:8123 `
  ghcr.io/home-assistant/home-assistant:stable
```

Notes:
- `-p 8123:8123` rather than `--network host` because Docker Desktop's WSL2
  backend doesn't make `--network host` work cleanly for the Windows side.
  The trade-off: HA won't see mDNS device announcements from the LAN, so
  device discovery requires manual IP entry. ESPHome devices like the BOX-3
  still work fine — they're added by IP via the integration.
- The Narada custom integration must exist at
  `C:\Users\admin\ha-config\custom_components\narada\` — copy or symlink
  from `C:\Projects\deha\integrations\homeassistant\narada\`.

Verify:
```powershell
# Should respond in ~10s on first boot, sub-second after
Invoke-WebRequest http://127.0.0.1:8123/manifest.json
```

Then open `http://localhost:8123` in a browser.

## Wyoming-Whisper container — STT (verified)

```powershell
New-Item -ItemType Directory -Path "E:\whisper-data" -Force | Out-Null
docker pull rhasspy/wyoming-whisper:latest

docker run -d `
  --name wyoming-whisper `
  --restart unless-stopped `
  -p 10300:10300 `
  -v "E:\whisper-data:/data" `
  rhasspy/wyoming-whisper:latest `
  --model base-int8 `
  --language en
```

`base-int8` is the recommended balance of quality + speed for English. Other
common choices: `tiny-int8` (faster, less accurate), `small-int8` (slower,
better).

Wire into HA: Settings → Devices & Services → Add Wyoming Protocol →
host `127.0.0.1`, port `10300`.

Verify:
```powershell
Test-NetConnection 127.0.0.1 -Port 10300   # TcpTestSucceeded : True
```

## Kokoro TTS + brain server (untested in this pass)

The voice supervisor runs Python services (brain_server, Wyoming TTS bridge,
utter mediator) and spawns claude voice cognition. Code lives in `src/deha/voice/`.

```powershell
# From C:\Projects\deha — start manually for first-run verification:
& "C:\Projects\deha\.venv\Scripts\pythonw.exe" -m deha.voice.supervisor `
  --voice am_michael:0.5,af_heart:0.5 `
  --model sonnet
```

For autostart on logon, register as a Task Scheduler task. See the existing
configs in `~/.narada/host/components.yaml` for the canonical command.

Kokoro requires `kokoro-v1.0.onnx` and `voices-v1.0.bin` from
https://github.com/thewh1teagle/kokoro-onnx/releases — place per
`src/deha/voice/kokoro_tts.py` install path.

Health checks:
- `http://127.0.0.1:8765/health` (brain HTTP API)
- `tcp://127.0.0.1:10210` (Wyoming TTS for HA)
- `http://127.0.0.1:9999/status` (supervisor itself)

## Firmware (untested in this pass)

```powershell
cd C:\Projects\deha\firmware
esphome run narada-faces.yaml --device 192.168.86.35
```

Reflash on every YAML change. Configure the BOX-3's WiFi via captive portal
on first boot if it's not already on the LAN.

## Wake-word model (one-time, optional)

The default ESPHome firmware ships with the "okay nabu" wake word. To use
"Narada" instead, train a custom model. **The trainer is temporary tooling
and is NOT installed alongside deha.** See `docs/howto/train-wake-word.md`.

---

## Lessons captured during the 2026-05-17 recovery

- **Hash-verify, don't size-verify.** NTFS preallocates file size during cross-
  drive copies. A same-size check is meaningless mid-copy. Always
  `Get-FileHash` both ends before deleting the source.
- **Junction surgery on Docker's VHDX works** on Docker Desktop 4.73 / WSL 2.7
  — the daemon reads through the junction transparently. No re-pulls needed
  IF the underlying VHDX is intact.
- **Bind-mount everything to off-C: paths.** Even with the VHDX on E:, a
  runaway container writing to a Docker named volume still goes into the
  VHDX. Explicit bind mounts to `E:\<service>-data` keep growth visible
  and bounded.
- **HA recovers from unclean shutdown** automatically (Recorder "Ended
  unfinished session" warning is benign).
- **--network host on Docker Desktop / Windows is unreliable** — even on
  recent versions it puts the container on the WSL2 VM network, not the
  Windows host. Use `-p` port mapping.
