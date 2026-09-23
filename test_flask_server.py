"""Small Flask test server for exercising the measurement request sequence.

This always uses the simulated ADP2230 and is intended for API/client testing
without WaveForms hardware. It mirrors the production REST endpoints.
"""
from flask import Flask, jsonify, request

from rest_service import Controller

app = Flask(__name__)
controller = Controller(simulate=True)


@app.get("/status")
def status():
    return jsonify(controller.status())


@app.get("/measurement/<int:frequency_hz>")
def measurement(frequency_hz: int):
    amplitude = float(request.args.get("amplitude_v", 1.0))
    try:
        return jsonify(controller.read_frequency(frequency_hz, amplitude))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.get("/measurement/average")
def average():
    try:
        result = controller.average_frequency(
            int(request.args["frequency_hz"]),
            int(request.args["sample_size"]),
            float(request.args["interval_s"]),
            float(request.args.get("amplitude_v", 1.0)),
        )
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
