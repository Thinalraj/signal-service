"""REST interface for the ADP2230 multi-frequency measurement service."""
from __future__ import annotations

import threading
import time
from statistics import mean, pstdev

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from app import SimulatedADP2230, WaveFormsADP2230, measure


class StartRequest(BaseModel):
    frequency_hz: int = Field(10_000, ge=8_000, le=20_000)
    amplitude_v: float = Field(1.0, ge=0.0, le=5.0)


class SampleRequest(BaseModel):
    count: int = Field(10, ge=1, le=1000)
    interval_s: float = Field(0.5, ge=0.0, le=3600.0)


class Controller:
    allowed_frequencies = {8_000, 10_000, 15_000, 20_000}

    def __init__(self, simulate: bool = False):
        self.simulate = simulate
        self.lock = threading.Lock()
        self.instrument = None
        self.running = False
        self.frequency_hz = None
        self.amplitude_v = None

    def start(self, request: StartRequest):
        if request.frequency_hz not in self.allowed_frequencies:
            raise ValueError("frequency_hz must be 8000, 10000, 15000, or 20000")
        with self.lock:
            if self.instrument is None:
                self.instrument = SimulatedADP2230() if self.simulate else WaveFormsADP2230()
            self.instrument.start_sine(request.frequency_hz, request.amplitude_v)
            self.running = True
            self.frequency_hz = request.frequency_hz
            self.amplitude_v = request.amplitude_v
        return self.status()

    def stop(self):
        with self.lock:
            if self.instrument is not None:
                self.instrument.stop_sine()
                self.instrument = None
            self.running = False
            self.frequency_hz = None
            self.amplitude_v = None
        return self.status()

    def acquire(self):
        with self.lock:
            if not self.running or self.instrument is None:
                raise RuntimeError("Start the sine wave before acquiring")
            samples, rate = self.instrument.acquire(0.01, self.frequency_hz * 10)
        result = measure(samples, rate)
        result["sample_rate_hz"] = rate
        result["sample_count"] = len(samples)
        return result

    def read_frequency(self, frequency_hz: int, amplitude_v: float = 1.0):
        self.start(StartRequest(frequency_hz=frequency_hz, amplitude_v=amplitude_v))
        try:
            return self.acquire()
        finally:
            self.stop()

    def average_frequency(self, frequency_hz: int, sample_size: int,
                          interval_s: float, amplitude_v: float = 1.0):
        self.start(StartRequest(frequency_hz=frequency_hz, amplitude_v=amplitude_v))
        try:
            readings = []
            for index in range(sample_size):
                readings.append(self.acquire())
                if index + 1 < sample_size:
                    time.sleep(interval_s)
            output = {}
            for key in ("ac_rms_v", "peak_amplitude_v", "peak_to_peak_v", "frequency_hz"):
                values = [reading[key] for reading in readings]
                avg = mean(values)
                std = pstdev(values) if len(values) > 1 else 0.0
                output[key] = {"mean": avg, "min": min(values), "max": max(values),
                               "std_dev": std, "plus_minus": std}
            return {"frequency_hz": frequency_hz, "amplitude_v": amplitude_v,
                    "sample_size": sample_size, "interval_s": interval_s,
                    "statistics": output}
        finally:
            self.stop()

    def sample(self, request: SampleRequest):
        readings = []
        for index in range(request.count):
            readings.append(self.acquire())
            if index + 1 < request.count:
                time.sleep(request.interval_s)
        output = {}
        for key in ("ac_rms_v", "peak_amplitude_v", "peak_to_peak_v", "frequency_hz"):
            values = [reading[key] for reading in readings]
            avg = mean(values)
            std = pstdev(values) if len(values) > 1 else 0.0
            output[key] = {"mean": avg, "min": min(values), "max": max(values),
                           "std_dev": std, "plus_minus": std}
        return {"count": len(readings), "frequency_hz": self.frequency_hz,
                "amplitude_v": self.amplitude_v, "statistics": output}

    def status(self):
        return {"running": self.running, "frequency_hz": self.frequency_hz,
                "amplitude_v": self.amplitude_v, "simulate": self.simulate}


def create_app(simulate: bool = False) -> FastAPI:
    controller = Controller(simulate=simulate)
    api = FastAPI(title="ADP2230 Signal Service", version="0.1.0")

    @api.get("/status")
    def status():
        return controller.status()

    @api.post("/sine/start")
    def start(request: StartRequest):
        try:
            return controller.start(request)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @api.post("/sine/stop")
    def stop():
        try:
            return controller.stop()
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @api.post("/measurement")
    def measurement():
        try:
            return controller.acquire()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @api.get("/measurement/average")
    def single_frequency_average(frequency_hz: int = Query(...),
                                 sample_size: int = Query(..., ge=1, le=1000),
                                 interval_s: float = Query(..., ge=0.0, le=3600.0),
                                 amplitude_v: float = Query(1.0, ge=0.0, le=5.0)):
        try:
            return controller.average_frequency(frequency_hz, sample_size, interval_s, amplitude_v)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @api.get("/measurement/{frequency_hz}")
    def single_frequency(frequency_hz: int,
                         amplitude_v: float = Query(1.0, ge=0.0, le=5.0)):
        try:
            return controller.read_frequency(frequency_hz, amplitude_v)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @api.post("/measurements/sample")
    def sample(request: SampleRequest):
        try:
            return controller.sample(request)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return api


app = create_app()

if __name__ == "__main__":
    import uvicorn
    import sys
    uvicorn.run(create_app("--simulate" in sys.argv), host="127.0.0.1", port=8000)
