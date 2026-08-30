# ADP2230 Signal Service

Initial single-frequency GUI proof of concept for a Digilent Analog Discovery
Pro ADP2230. It drives Wavegen channel 1 with a 1 Vpp, 10 kHz sine wave and
measures Scope channel 1 once at 100 kS/s (10x the generator frequency).

The next branch can add the 15 kHz and 20 kHz stages after this reading is
validated. Set both probes physically to 1x; probe attenuation is not changed
by this program.

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

On Windows, the application automatically searches for the SDK at
`C:\Program Files\Digilent\WaveFormsSDK`. If it is installed elsewhere, set
the root explicitly before running:

```powershell
$env:WAVEFORMS_SDK_ROOT="C:\ProgramsFiles\Digilent\WaveFormsSDK"
python app.py
```

The WaveForms SDK sample is used as the hardware adapter seed. The requested
1 Vpp output is configured as 0.5 V peak amplitude.
