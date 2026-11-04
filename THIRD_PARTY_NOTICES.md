# Third-party boundaries

WaterWatch application, firmware glue, UI, fixtures and mount design are original MIT code.
No source, weights, images or housing files from AI-on-the-edge-device are distributed.
The inspected reference's current [Licence.md](https://github.com/jomjol/AI-on-the-edge-device/blob/main/Licence.md)
is a private/noncommercial plus separately licensed commercial scheme (reviewed 2026-10-19).
Do not copy its models or code into this MIT project assuming a permissive license.

Dependencies retain their own licenses and notices; the root MIT license does not relicense them.

| Component | Upstream / license |
|---|---|
| ESP-IDF | [Espressif](https://github.com/espressif/esp-idf), Apache-2.0 plus component notices |
| esp32-camera | [Espressif](https://github.com/espressif/esp32-camera), Apache-2.0; inspect bundled sensor code notices |
| FastAPI | [FastAPI](https://github.com/fastapi/fastapi), MIT |
| Starlette / Uvicorn | [Starlette](https://github.com/Kludex/starlette), [Uvicorn](https://github.com/Kludex/uvicorn), BSD-3-Clause |
| Pydantic | [Pydantic](https://github.com/pydantic/pydantic), MIT |
| OpenCV | [OpenCV](https://github.com/opencv/opencv), Apache-2.0 for modern versions; wheel includes additional notices |
| NumPy | [NumPy](https://github.com/numpy/numpy), BSD-3-Clause |
| Pillow | [Pillow](https://github.com/python-pillow/Pillow), MIT-CMU |
| Tesseract + English traineddata | [Tesseract](https://github.com/tesseract-ocr/tesseract), [tessdata](https://github.com/tesseract-ocr/tessdata), Apache-2.0 |
| ONNX Runtime CPU | [Microsoft](https://github.com/microsoft/onnxruntime), MIT |
| PP-OCRv5 English mobile model | [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR), Apache-2.0; ONNX conversion distributed by [RapidAI](https://github.com/RapidAI/RapidOCR) |
| Eclipse Paho MQTT Python | [Paho](https://github.com/eclipse-paho/paho.mqtt.python), EPL-2.0 / EDL-1.0 |
| Caddy | [Caddy](https://github.com/caddyserver/caddy), Apache-2.0 |
| Mosquitto (optional test broker) | [Mosquitto](https://github.com/eclipse-mosquitto/mosquitto), EPL-2.0 / EDL-1.0 |

Camera pin assignments are factual connector mappings checked against manufacturer/Espressif
documentation; their example application implementations have not been copied. No model
from the reference project or an unverified dataset is bundled. Add provenance and redistribution
terms to `models/` before installing additional weights. Capture only your own meter images.

The default host recognizer uses `en_PP-OCRv5_rec_mobile.onnx` from RapidAI's
versioned `v3.10.0` model catalog. SHA-256:
`c3461add59bb4323ecba96a492ab75e06dda42467c9e3d0c18db5d1d21924be8`.
The image build downloads and checks that exact asset. It is not fetched at application
startup. The upstream Apache-2.0 text is included at
`server/licenses/paddleocr-apache-2.0.txt`. Digit-row normalization, two-pass agreement
and cell mapping are WaterWatch application code. The BGR scaling and CTC model
interface follow the public PaddleOCR/RapidOCR conventions.
