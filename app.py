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
    # Match AnalogIn_Frequency.py: estimate frequency from the FFT, then use
    # parabolic interpolation around the strongest bin.
    import numpy as np
    spectrum = np.abs(np.fft.rfft(np.asarray(ac, dtype=float)))
    spectrum[0] = 0.0
    peak_bin = int(np.argmax(spectrum))
    frequency = peak_bin * sample_rate / len(ac)
    if 0 < peak_bin < len(spectrum) - 1:
        left, center, right = spectrum[peak_bin - 1:peak_bin + 2]
        denominator = left - 2 * center + right
        if denominator:
            correction = 0.5 * (left - right) / denominator
            frequency = (peak_bin + correction) * sample_rate / len(ac)
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

    def start_sine(self, frequency_hz: float, amplitude_v: float) -> None:
        self.configure(frequency_hz, amplitude_v)

    def stop_sine(self) -> None:
        pass

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

    def start_sine(self, frequency_hz: float, amplitude_v: float) -> None:
        self.configure(frequency_hz, amplitude_v)

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
        actual_rate = self.c_double()
        self.dwf.FDwfAnalogInFrequencyGet(self.hdwf, self.byref(actual_rate))
        return list(buffer), actual_rate.value

    def close(self) -> None:
        self.dwf.FDwfAnalogOutReset(self.hdwf, self.c_int(0))
        self.dwf.FDwfDeviceCloseAll()

    def stop_sine(self) -> None:
        # Reset output and close the complete SDK session. The next start
        # creates a fresh device handle instead of reusing stale state.
        self.close()


class App:
    frequencies = (8_000, 10_000, 15_000, 20_000)

    def __init__(self, root: tk.Tk, simulate: bool) -> None:
        self.root, self.simulate = root, simulate
        root.title("ADP2230 Signal Measurement")
        self.selected_frequency = 10_000
        frequency_controls = ttk.LabelFrame(root, text="Select frequency")
        frequency_controls.pack(padx=16, pady=(12, 0))
        for column, frequency in enumerate(self.frequencies):
            ttk.Button(frequency_controls, text=f"{frequency // 1000} kHz",
                       command=lambda f=frequency: self.select_frequency(f)).grid(
                           row=0, column=column, padx=4, pady=4)
        controls = ttk.Frame(root)
        controls.pack(padx=16, pady=12)
        self.start = ttk.Button(controls, text="Start sine wave", command=self.start_sine)
        self.start.grid(row=0, column=0, padx=4)
        self.stop = ttk.Button(controls, text="Stop sine wave", command=self.stop_sine, state="disabled")
        self.stop.grid(row=0, column=1, padx=4)
        self.acquire_button = ttk.Button(controls, text="Acquire measurement", command=self.start_measurement)
        self.acquire_button.grid(row=0, column=2, padx=4)
        self.status = ttk.Label(root, text="Ready")
        self.status.pack(padx=16)
        self.table = ttk.Treeview(root, columns=("set", "rms", "peak", "p2p", "freq"), show="headings")
        for col, title in zip(self.table["columns"], ("Set Hz", "AC RMS V", "Peak V", "Vpp", "Measured Hz")):
            self.table.heading(col, text=title)
        self.table.pack(padx=16, pady=12)
        self.instrument = None
        self.instrument_lock = threading.Lock()
        self.sine_running = False
        self.root.protocol("WM_DELETE_WINDOW", self.close_application)

    def select_frequency(self, frequency: int) -> None:
        if self.sine_running:
            self.status.config(text="Stop the sine wave before selecting a new frequency")
            return
        self.selected_frequency = frequency
        self.status.config(text=f"Selected {frequency / 1000:g} kHz")

    def get_instrument(self):
        if self.instrument is None:
            self.instrument = SimulatedADP2230() if self.simulate else WaveFormsADP2230()
        return self.instrument

    def start_sine(self) -> None:
        def worker() -> None:
            try:
                with self.instrument_lock:
                    self.get_instrument().start_sine(self.selected_frequency, 0.5)
                self.sine_running = True
                self.root.after(0, self.status.config, {"text": f"{self.selected_frequency / 1000:g} kHz sine running (1 Vpp)"})
                self.root.after(0, lambda: self.stop.config(state="normal"))
            except Exception as exc:
                self.root.after(0, self.status.config, {"text": f"Error: {exc}"})
        threading.Thread(target=worker, daemon=True).start()

    def stop_sine(self) -> None:
        def worker() -> None:
            try:
                with self.instrument_lock:
                    if self.instrument:
                        self.instrument.stop_sine()
                        self.instrument = None
                self.sine_running = False
                self.root.after(0, self.status.config, {"text": "Sine wave stopped"})
                self.root.after(0, lambda: self.stop.config(state="disabled"))
            except Exception as exc:
                self.root.after(0, self.status.config, {"text": f"Error: {exc}"})
        threading.Thread(target=worker, daemon=True).start()

    def close_application(self) -> None:
        """Stop output and release the ADP2230 before exiting the GUI."""
        def worker() -> None:
            with self.instrument_lock:
                if self.instrument:
                    self.instrument.close()
                    self.instrument = None
                self.sine_running = False
            self.root.after(0, self.root.destroy)
        threading.Thread(target=worker, daemon=True).start()

    def start_measurement(self) -> None:
        self.start.config(state="disabled")
        threading.Thread(target=self.run, daemon=True).start()

    def run(self) -> None:
        instrument = self.get_instrument()
        try:
            if not self.sine_running:
                with self.instrument_lock:
                    instrument.start_sine(self.selected_frequency, 0.5)
                self.sine_running = True
            with self.instrument_lock:
                samples, rate = instrument.acquire(0.01, self.selected_frequency * 10)
            result = measure(samples, rate)
            self.root.after(0, self.add_result, self.selected_frequency, result)
            self.root.after(0, self.status.config, {"text": "Complete"})
        except Exception as exc:
            self.root.after(0, self.status.config, {"text": f"Error: {exc}"})
        finally:
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
