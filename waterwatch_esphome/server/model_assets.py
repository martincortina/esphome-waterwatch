"""Fetch the pinned public OCR asset at image-build time, never on app startup."""
import hashlib

import httpx

from server.neural_ocr import (
    ASSIST_MODEL_NAME,
    ASSIST_MODEL_SHA256,
    ASSIST_MODEL_URL,
    MODEL_DIRECTORY,
    MODEL_NAME,
    MODEL_SHA256,
    MODEL_URL,
)


def install_asset(name, digest, url):
    path = MODEL_DIRECTORY / name
    if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == digest:
        return
    MODEL_DIRECTORY.mkdir(exist_ok=True)
    with httpx.Client(timeout=90, follow_redirects=True, trust_env=False) as client:
        with client.stream("GET", url) as response:
            response.raise_for_status()
            data = bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > 20_000_000:
                    raise ValueError("OCR model exceeds its asset size limit")
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError("OCR model checksum mismatch")
    temporary = path.with_suffix(".download")
    temporary.write_bytes(data)
    temporary.replace(path)


def install():
    install_asset(MODEL_NAME, MODEL_SHA256, MODEL_URL)
    install_asset(ASSIST_MODEL_NAME, ASSIST_MODEL_SHA256, ASSIST_MODEL_URL)


if __name__ == "__main__":
    install()
    print("Pinned local OCR model ready")
