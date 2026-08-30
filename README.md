# ADP2230 Signal Service

Initial GUI proof of concept for a Digilent Analog Discovery Pro ADP2230.
It drives Wavegen channel 1 with a 1 Vpp sine wave and measures Scope channel 1
through a 10 kHz → 15 kHz → 20 kHz sweep, five seconds per stage.

## Run simulation

```bash
python3 app.py --simulate
```

## Run with hardware

Install the Digilent WaveForms application/SDK and its `WF_SDK` Python package,
connect the generator output to Scope channel 1, connect grounds, and run:

```bash
python3 app.py
```

The WaveForms SDK sample is used as the hardware adapter seed. The requested
1 Vpp output is configured as 0.5 V peak amplitude.
