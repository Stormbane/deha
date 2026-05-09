# Firmware status — 2026-05-10

## Current state on the BOX-3

The flashed firmware on the BOX-3 (192.168.86.35) is **older than what
this repo currently defines.** The Python clients in `deha/src/deha/`
expect ESPHome services that the running firmware doesn't expose:

- `set_status`     (called by `deha.display.DisplayClient.set_status`)
- `set_thought`    (called by display + expression clients)
- `set_weather`    (called by `deha.expression.expression.ExpressionClient.set_weather`)

Symptom in heartbeat logs: `WARNING deha.display: Service not found:
set_status` (and similar for set_thought, set_weather). The clients
fail-soft — cognition continues — but the body stays mute.

## Reflash needed

Flash the canonical firmware definition at `firmware/narada-body.yaml`
(or `firmware/narada-unified.yaml`, whichever the next session decides
is right). The C++ headers in `firmware/include/` (compositor.h,
sandhi_player.h, state_machine.h, etc.) plus the YAML define what the
running firmware should be.

`secrets.yaml` is gitignored — copy from your local svapna checkout or
re-derive Wi-Fi credentials.

## Verification after flash

```bash
# Fast smoke test
python -m deha.cli ping        # should reach 192.168.86.35
python -m deha.cli status      # full body state
python -c "from deha.display import DisplayClient; DisplayClient().set_status('hello')"
# → text should appear on the BOX-3 display
```

If `Service not found` warnings are gone from the heartbeat log on the
next cycle, the firmware/client contract is back in sync.

## Background

This file landed when deha was extracted from svapna (Phase 3 of the
project decomposition, 2026-05-10). The mismatch was caught when the
post-extraction heartbeat verification cycle ran — body responded but
the services weren't there. Reflash was deferred so the voice-work
session in this repo could sequence it alongside related changes.

See `../docs/architecture.md` for the broader body architecture.
