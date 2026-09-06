# Narada wake-word model

Drop the trainer output here when ready:

- `narada.tflite` — quantized INT8 model
- `narada.json` — manifest (points at the .tflite, sets probability_cutoff etc.)

Exact filenames may differ from what TaterTotterson's trainer outputs;
rename them to `narada.tflite` and `narada.json` after download to keep
the YAML reference stable.

## Activation

After dropping the files in, the `micro_wake_word:` block in
`firmware/narada-faces.yaml` will load this model. Reflash via:

```
cd firmware
esphome run narada-faces.yaml --device 192.168.86.35
```

## Tuning if needed

If false positives are too common (Narada wakes during random chat),
raise `probability_cutoff` in `narada.json` from 0.97 → 0.985 → 0.99.

If Narada doesn't wake often enough, lower it to 0.95 or 0.93.

Iterate. The trade-off knob is steep — most people land between 0.95
and 0.98 after a couple cycles.

## Real-voice fine-tune

If the synthetic-only model misses your actual pronunciation too
often, capture 20-30 recordings of you saying "naarowhdah" (the
training phrase) through your phone or the BOX-3 itself, upload them
to the trainer's "Samples" section, and retrain. Real samples are
weighted higher than synthetic, so a small set goes a long way.
