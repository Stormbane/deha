# How to: train a Narada wake-word model

The microWakeWord trainer is **temporary tooling** — install it when you want
to train or retrain a wake-word model, then leave it stopped (or remove it
entirely) once you have a `.tflite` you're happy with. It is NOT part of the
standing deha install.

## Prerequisites

- Docker Desktop running with data on a non-C: drive (see `INSTALL-body.md`).
- NVIDIA GPU recommended but not required — see the GPU note below.

## Setup (one-time per machine)

```powershell
cd E:\Projects
git clone https://github.com/TaterTotterson/microWakeWord-Trainer-Nvidia-Docker
New-Item -ItemType Directory -Path "E:\microwakeword-data" -Force | Out-Null

docker pull ghcr.io/tatertotterson/microwakeword:latest

docker run -d `
  --name microwakeword-trainer `
  --gpus all `
  -p 8789:8789 `
  -e REC_PORT=8789 `
  -v "E:\microwakeword-data:/data" `
  ghcr.io/tatertotterson/microwakeword:latest
```

Notes:
- **Bind-mount to E:**, never let `/data` live inside the Docker VHDX.
  Sample sets and downloaded models can easily exceed 30 GB.
- `--restart` is intentionally NOT set — this is temp tooling.
- The upstream README recommends `--network host`. **Do not use it on
  Docker Desktop / Windows** — it puts the container on the WSL2 VM
  network rather than the Windows host, so `http://localhost:8789` never
  becomes reachable. Use explicit `-p 8789:8789` instead. Trade-off: the
  Firmware tab's mDNS device discovery won't see ESPHome devices on the
  LAN, so flashes need a manual IP (e.g. `192.168.86.35`).

## First-run wait

Open `http://localhost:8789` — it WILL fail for the first 10–15 minutes
while the container's startup script provisions its Python venv:

- TensorFlow 2.20 stack
- torch + torchaudio
- Clones `micro-wake-word` and `piper-sample-generator`
- Downloads `en_US-libritts_r-medium.pt` Piper voice (~75 MB)

All of this is cached for subsequent runs. Watch progress in the trainer's
"Training Console" once the UI is up.

## GPU note

Even with `--gpus all` and a working `nvidia-smi` inside the container, the
trainer's setup script installs **CPU-only** TensorFlow and torch wheels
(it does not request the `tensorflow[and-cuda]` extra or the CUDA-tagged
torch index). Training will run on CPU regardless of GPU presence —
~30–60 min for a single wake-word vs ~10–15 min on GPU.

For a one-shot training job, CPU is fine. If you plan to retrain many
times, patch the container's `cli/setup_python_venv` script to use the
GPU wheels (or file an upstream PR).

## Training workflow

1. Open `http://localhost:8789`. The Trainer tab is the main view.
2. Enter the wake phrase. Pronunciation matters more than spelling — for
   "Narada", `naarowhdah` produces more reliable synthetic samples than the
   literal `narada`.
3. (Optional) Click "Record" and capture ~20–30 of your own utterances
   for fine-tuning. Real samples are weighted higher than synthetic.
4. Click "Train". The trainer:
   - Synthesizes ~2000 positive samples via Piper TTS in many voices
   - Mixes negative samples from its bundled corpus
   - Trains a TFLite model (~5 min GPU / ~30–60 min CPU)
5. Download `narada.tflite` + `narada.json` from the UI when training
   completes.

## Installing the trained model in firmware

Drop the two files into `C:\Projects\deha\firmware\wake_words\narada\`,
renamed to `narada.tflite` and `narada.json` so the YAML reference stays
stable. Then:

```powershell
cd C:\Projects\deha\firmware
esphome run narada-faces.yaml --device 192.168.86.35
```

See `firmware/wake_words/narada/README.md` for the tuning knobs
(`probability_cutoff` etc.) once the model is deployed.

## Tearing the trainer back down

```powershell
docker stop microwakeword-trainer
docker rm microwakeword-trainer
# Keep E:\microwakeword-data around if you want to retrain later with
# the cached venv + Piper voices. Remove it to reclaim ~5-10 GB.
```
