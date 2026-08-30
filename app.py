"""ADP2230 sine sweep GUI.

Run with ``python3 app.py --simulate`` without WaveForms installed.
For hardware, install Digilent WaveForms SDK and its WF_SDK Python package,
then run ``python3 app.py``.
"""
from __future__ import annotations

import argparse
import math
import threading
import time
import tkinter as tk
from tkinter import ttk


def measure(samples: list[float], sample_rate: float) -> dict[str, float]:
    if not samples:
        raise ValueError("No samples acquired")
    dc = sum(samples) / len(samples)
    ac = [v - dc for v in samples]
    rms = math.sqrt(sum(v * v for v in ac) / len(ac))
    p2p = max(samples) - min(samples)
    # Zero-crossing interpolation is sufficient for the first service version.
    crossings = []
    for a, b in zip(ac, ac[1:]):
        if a <= 0 < b:
            crossings.append(a / (a - b))
    frequency = 0.0
    if len(crossings) >= 2:
        periods = [((i + crossings[i + 1]) - (i + crossings[i])) for i in range(len(crossings) - 1)]
        frequency = sample_rate / (sum(periods) / len(periods))
    return {
        "ac_rms_v": rms,
        "peak_amplitude_v": max(abs(v) for v in ac),
        "peak_to_peak_v": p2p,
        "frequency_hz": frequency,
        "dc_v": dc,
    }


class SimulatedADP2230:
    def configure(self, frequency_hz: float, amplitude_v: float) -> None:
        self.frequency_hz, self.amplitude_v = frequency_hz, amplitude_v

    def acquire(self, duration_s: float = 0.1, sample_rate: float = 1_000_000) -> tuple[list[float], float]:
        count = min(int(duration_s * sample_rate), 100_000)
        actual_rate = count / duration_s
        samples = [self.amplitude_v * math.sin(2 * math.pi * self.frequency_hz * i / actual_rate)
                   for i in range(count)]
        return samples, actual_rate

    def close(self) -> None:
        pass


class WaveFormsADP2230:
    def __init__(self) -> None:
        from WF_SDK import device, scope, wavegen
        self.device, self.scope, self.wavegen = device, scope, wavegen
        self.data = device.open("Analog Discovery Pro 3X50")
        scope.open(self.data)
        scope.trigger(self.data, enable=True, source=scope.trigger_source.analog, channel=1, level=0)

    def configure(self, frequency_hz: float, amplitude_v: float) -> None:
        self.wavegen.generate(self.data, channel=1, function=self.wavegen.function.sine,
                              offset=0, frequency=frequency_hz, amplitude=amplitude_v)

    def acquire(self, duration_s: float = 0.1, sample_rate: float = 1_000_000) -> tuple[list[float], float]:
        # scope.record uses the SDK's configured acquisition settings.
        samples = self.scope.record(self.data, channel=1)
        return list(samples), self.scope.data.sampling_frequency

    def close(self) -> None:
        self.scope.close(self.data)
        self.wavegen.close(self.data)
        self.device.close(self.data)


class App:
    stages = [(10_000, 5), (15_000, 5), (20_000, 5)]

    def __init__(self, root: tk.Tk, simulate: bool) -> None:
        self.root, self.simulate = root, simulate
        root.title("ADP2230 Signal Measurement")
        self.start = ttk.Button(root, text="Start measurement", command=self.start_measurement)
        self.start.pack(padx=16, pady=12)
        self.status = ttk.Label(root, text="Ready")
        self.status.pack(padx=16)
        self.table = ttk.Treeview(root, columns=("set", "rms", "peak", "p2p", "freq"), show="headings")
        for col, title in zip(self.table["columns"], ("Set Hz", "AC RMS V", "Peak V", "Vpp", "Measured Hz")):
            self.table.heading(col, text=title)
        self.table.pack(padx=16, pady=12)

    def start_measurement(self) -> None:
        self.start.config(state="disabled")
        threading.Thread(target=self.run, daemon=True).start()

    def run(self) -> None:
        instrument = SimulatedADP2230() if self.simulate else WaveFormsADP2230()
        try:
            for frequency, seconds in self.stages:
                self.root.after(0, self.status.config, {"text": f"Measuring {frequency / 1000:g} kHz"})
                instrument.configure(frequency, 0.5)  # 1 V peak-to-peak = 0.5 V peak
                end = time.monotonic() + seconds
                while time.monotonic() < end:
                    samples, rate = instrument.acquire()
                    result = measure(samples, rate)
                    self.root.after(0, self.add_result, frequency, result)
                    time.sleep(0.25)
            self.root.after(0, self.status.config, {"text": "Complete"})
        except Exception as exc:
            self.root.after(0, self.status.config, {"text": f"Error: {exc}"})
        finally:
            instrument.close()
            self.root.after(0, lambda: self.start.config(state="normal"))

    def add_result(self, set_frequency: float, result: dict[str, float]) -> None:
        self.table.insert("", "end", values=(f"{set_frequency:.0f}", f"{result['ac_rms_v']:.4f}",
            f"{result['peak_amplitude_v']:.4f}", f"{result['peak_to_peak_v']:.4f}", f"{result['frequency_hz']:.1f}"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--simulate", action="store_true", help="Run without an ADP2230")
    args = parser.parse_args()
    root = tk.Tk()
    App(root, args.simulate)
    root.mainloop()
