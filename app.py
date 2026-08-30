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


def configure_waveforms_sdk() -> None:
    """Make the WaveForms SDK package and native library importable."""
    import os
    import sys

    sdk_root = os.environ.get("WAVEFORMS_SDK_ROOT")
    if not sdk_root and os.name == "nt":
        sdk_root = r"C:\Program Files\Digilent\WaveFormsSDK"
    if not sdk_root:
        return

    sdk_python = os.path.join(sdk_root, "samples", "py")
    sdk_lib = os.path.join(sdk_root, "lib", "x64")
    if not os.path.isdir(sdk_lib):
        sdk_lib = os.path.join(sdk_root, "lib")
    if sdk_python not in sys.path:
        sys.path.insert(0, sdk_python)
    if os.path.isdir(sdk_lib):
        if hasattr(os, "add_dll_directory"):
            os.add_dll_directory(sdk_lib)
        else:
            os.environ["PATH"] = sdk_lib + os.pathsep + os.environ.get("PATH", "")


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
        configure_waveforms_sdk()
        from ctypes import byref, c_double, c_int, cdll, create_string_buffer
        from dwfconstants import DwfStateDone, hdwfNone
        self.c_double, self.c_int, self.byref = c_double, c_int, byref
        self.DwfStateDone, self.hdwfNone = DwfStateDone, hdwfNone
        self.dwf = cdll.dwf
        version = create_string_buffer(16)
        self.dwf.FDwfGetVersion(version)
        devices = c_int()
        self.dwf.FDwfEnum(c_int(0), byref(devices))
        if devices.value == 0:
            raise RuntimeError("No WaveForms device detected")
        self.hdwf = c_int()
        self.dwf.FDwfDeviceOpen(c_int(0), byref(self.hdwf))
        if self.hdwf.value == hdwfNone.value:
            raise RuntimeError("Could not open the WaveForms device")
        self.dwf.FDwfDeviceAutoConfigureSet(self.hdwf, c_int(0))
        # Match the verified AnalogOutIn.py acquisition setup.
        self.dwf.FDwfAnalogInFrequencySet(self.hdwf, c_double(100_000))
        self.dwf.FDwfAnalogInChannelRangeSet(self.hdwf, c_int(-1), c_double(4))
        self.dwf.FDwfAnalogInBufferSizeSet(self.hdwf, c_int(1000))
        self.dwf.FDwfAnalogInConfigure(self.hdwf, c_int(1), c_int(0))
        time.sleep(2)  # allow input offset to stabilize after opening

    def configure(self, frequency_hz: float, amplitude_v: float) -> None:
        self.dwf.FDwfAnalogOutEnableSet(self.hdwf, self.c_int(0), self.c_int(1))
        self.dwf.FDwfAnalogOutFunctionSet(self.hdwf, self.c_int(0), self.c_int(1))
        self.dwf.FDwfAnalogOutFrequencySet(self.hdwf, self.c_int(0), self.c_double(frequency_hz))
        self.dwf.FDwfAnalogOutAmplitudeSet(self.hdwf, self.c_int(0), self.c_double(amplitude_v))
        self.dwf.FDwfAnalogOutConfigure(self.hdwf, self.c_int(0), self.c_int(1))

    def acquire(self, duration_s: float = 0.01, sample_rate: float = 100_000) -> tuple[list[float], float]:
        from ctypes import c_double
        count = 1000
        self.dwf.FDwfAnalogInConfigure(self.hdwf, self.c_int(1), self.c_int(1))
        state = self.c_int()
        while True:
            self.dwf.FDwfAnalogInStatus(self.hdwf, self.c_int(1), self.byref(state))
            if state.value == self.DwfStateDone.value:
                break
            time.sleep(0.01)
        buffer = (c_double * count)()
        self.dwf.FDwfAnalogInStatusData(self.hdwf, self.c_int(0), buffer, count)
        return list(buffer), sample_rate

    def close(self) -> None:
        self.dwf.FDwfAnalogOutReset(self.hdwf, self.c_int(0))
        self.dwf.FDwfDeviceCloseAll()


class App:
    # First validation branch: one 10 kHz measurement at 10x sample rate.
    stages = [(10_000, 0.01)]

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
                samples, rate = instrument.acquire(seconds, frequency * 10)
                result = measure(samples, rate)
                self.root.after(0, self.add_result, frequency, result)
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
