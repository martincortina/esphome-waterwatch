"""Fetch the pinned public OCR asset at image-build time, never on app startup."""
import hashlib

import httpx

from server.neural_ocr import MODEL_DIRECTORY, MODEL_NAME, MODEL_SHA256, MODEL_URL


def install():
    path = MODEL_DIRECTORY / MODEL_NAME
    if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == MODEL_SHA256:
        return
    MODEL_DIRECTORY.mkdir(exist_ok=True)
    with httpx.Client(timeout=90, follow_redirects=True, trust_env=False) as client:
        with client.stream("GET", MODEL_URL) as response:
            response.raise_for_status()
            data = bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > 20_000_000:
                    raise ValueError("OCR model exceeds its asset size limit")
    if hashlib.sha256(data).hexdigest() != MODEL_SHA256:
        raise ValueError("OCR model checksum mismatch")
    temporary = path.with_suffix(".download")
    temporary.write_bytes(data)
    temporary.replace(path)


if __name__ == "__main__":
    install()
    print("Pinned local OCR model ready")
