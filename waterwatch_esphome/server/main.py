import asyncio
import io
import json
import logging
import secrets
import sqlite3
import threading
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from server.config import Settings
from server.home_assistant import CameraCollector, CameraError, HomeAssistantCamera
from server.models import ManualReading, MeterConfig
from server.mqtt import MqttBridge
from server.ocr import align, decode, jpeg
from server.service import Service, public_reading, token_hash

bearer = HTTPBearer(auto_error=False)
MAX_IMAGE_BYTES = 2 * 1024 * 1024
WEB = Path(__file__).resolve().parent / "web"


def create_app(settings=None, pipeline=None, start_background=True, ha_camera=None):
    settings = settings or Settings.from_env()
    settings.validate()
    service = Service(settings, pipeline)
    bridge = MqttBridge(service)
    ha_camera = ha_camera or HomeAssistantCamera(settings)
    collector = CameraCollector(service, ha_camera)
    stop = threading.Event()

    def worker():
        ticks = 0
        while not stop.is_set():
            try:
                bridge.tick()
                if ticks % 240 == 0:
                    service.cleanup_images()
            except Exception:
                logging.getLogger(__name__).exception("Background maintenance failed")
            ticks += 1
            stop.wait(15)

    def capture_worker():
        while not stop.is_set():
            try:
                collector.tick()
            except Exception:
                logging.getLogger(__name__).warning("Camera polling failed; retrying next cycle")
            stop.wait(1)

    @asynccontextmanager
    async def lifespan(app):
        thread = capture_thread = None
        if start_background:
            bridge.start()
            thread = threading.Thread(target=worker, daemon=True)
            thread.start()
            if ha_camera.configured:
                capture_thread = threading.Thread(target=capture_worker, daemon=True)
                capture_thread.start()
        try:
            yield
        finally:
            stop.set()
            for task in (capture_thread, thread):
                if task:
                    await asyncio.to_thread(task.join)
            bridge.stop()
            ha_camera.close()
            service.store.close()

    app = FastAPI(
        title="WaterWatch",
        version="0.4.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.service = service
    app.state.bridge = bridge
    app.state.collector = collector

    def ingress_request(request):
        return settings.ingress_mode and request.client and request.client.host == "172.30.32.2"

    @app.middleware("http")
    async def security_headers(request, call_next):
        local_health = (
            request.url.path == "/healthz"
            and request.method == "GET"
            and request.client
            and request.client.host in ("127.0.0.1", "::1")
        )
        if settings.ingress_mode and not ingress_request(request) and not local_health:
            return JSONResponse({"detail": "Home Assistant ingress required"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' blob:; style-src 'self'; script-src 'self'; "
            + ("frame-ancestors 'self'" if settings.ingress_mode else "frame-ancestors 'none'")
        )
        return response

    def admin(
        request: Request,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ):
        if ingress_request(request):
            return
        if credentials is None or not secrets.compare_digest(
            credentials.credentials.encode(), settings.admin_token.encode()
        ):
            raise HTTPException(
                401, "Admin bearer token required", headers={"WWW-Authenticate": "Bearer"}
            )

    def camera(
        meter_id: str, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]
    ):
        row = service.store.one("SELECT token_hash FROM meters WHERE id=?", (meter_id,))
        supplied = credentials.credentials if credentials else ""
        if row is None or not secrets.compare_digest(row["token_hash"], token_hash(supplied)):
            raise HTTPException(401, "Camera bearer token required")

    def config(meter_id: str):
        try:
            return service.store.config(meter_id)
        except KeyError:
            raise HTTPException(404, "Meter not found") from None

    async def image_body(request):
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_IMAGE_BYTES:
                raise HTTPException(413, "Image limit is 2 MiB")
            chunks.append(chunk)
        if not size:
            raise HTTPException(400, "Send raw JPEG/PNG bytes as the request body")
        return b"".join(chunks)

    @app.get("/healthz")
    def health():
        service.store.one("SELECT 1")
        return {"status": "ok"}

    @app.get("/api/session")
    def session():
        return {"authentication": "ingress" if settings.ingress_mode else "bearer"}

    @app.exception_handler(CameraError)
    async def camera_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=502)

    @app.get("/api/status", dependencies=[Depends(admin)])
    def status():
        engine = service.pipeline.engine
        return {
            "mqtt": "connected"
            if bridge.connected.is_set()
            else ("disabled" if not settings.mqtt_host else "disconnected"),
            "ocr_engine": type(engine).__name__,
            "ocr_available": getattr(engine, "available", True),
            "capabilities": {"Docker": True, "ESP32": False, "Auto": False},
            "pending_mqtt": service.store.one("SELECT count(*) AS n FROM outbox")["n"],
            "home_assistant_camera": ha_camera.configured,
        }

    @app.get("/api/openapi.json", dependencies=[Depends(admin)], include_in_schema=False)
    def openapi():
        return app.openapi()

    @app.get("/api/meters", dependencies=[Depends(admin)])
    def meters():
        return [
            {**meter, "capture_status": collector.status(meter["config"]["id"])}
            for meter in service.summary()
        ]

    @app.get("/api/home-assistant/cameras", dependencies=[Depends(admin)])
    def cameras():
        return ha_camera.cameras()

    @app.get("/api/home-assistant/flash-lights", dependencies=[Depends(admin)])
    def flash_lights():
        return ha_camera.flash_lights()

    @app.get("/api/meters/{meter_id}/camera-image", dependencies=[Depends(admin)])
    def camera_preview(meter_id: str):
        cfg = config(meter_id)
        if cfg.source.kind != "home_assistant":
            raise HTTPException(409, "Select and save a Home Assistant camera first")
        data = ha_camera.snapshot(cfg.source.camera_entity)
        media = "image/png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
        return Response(data, media_type=media)

    @app.post("/api/meters/{meter_id}/camera-image", dependencies=[Depends(admin)])
    def camera_preview_with_flash(meter_id: str):
        config(meter_id)
        data = collector.preview(meter_id)
        media = "image/png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
        return Response(data, media_type=media)

    @app.post("/api/meters/{meter_id}/capture", dependencies=[Depends(admin)])
    def capture(meter_id: str, test_only: bool = True):
        config(meter_id)
        return collector.capture(meter_id, test_only=test_only)

    @app.post("/api/meters/{meter_id}/transform-image", dependencies=[Depends(admin)])
    async def transform_image(
        meter_id: str, request: Request,
        rotation_degrees: float | None = Query(default=None, ge=-180, le=180),
    ):
        cfg = config(meter_id)
        if rotation_degrees is not None:
            cfg = cfg.model_copy(update={"rotation_degrees": rotation_degrees})
        data = await image_body(request)

        def render_transform():
            with service.processing:
                return jpeg(align(decode(data), cfg))

        try:
            result = await run_in_threadpool(render_transform)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        return Response(result, media_type="image/jpeg")

    @app.post("/api/meters", status_code=201, dependencies=[Depends(admin)])
    def register(cfg: MeterConfig):
        try:
            token = service.save_config(cfg, create=True)
        except (ValueError, sqlite3.IntegrityError) as exc:
            raise HTTPException(409, str(exc)) from None
        return {"config": cfg.model_dump(), "camera_token": token}

    @app.put("/api/meters/{meter_id}", dependencies=[Depends(admin)])
    def configure(meter_id: str, cfg: MeterConfig):
        config(meter_id)
        if cfg.id != meter_id:
            raise HTTPException(422, "Meter ID cannot change")
        try:
            service.save_config(cfg)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        return cfg

    @app.post("/api/meters/{meter_id}/rotate-token", dependencies=[Depends(admin)])
    def rotate(meter_id: str):
        config(meter_id)
        return {"camera_token": service.rotate_token(meter_id)}

    @app.get("/api/device/{meter_id}/config", dependencies=[Depends(camera)])
    def device_config(meter_id: str):
        cfg = config(meter_id)
        return {"mode": cfg.mode, **cfg.camera.model_dump()}

    @app.post("/api/device/{meter_id}/images", dependencies=[Depends(camera)])
    async def upload(
        meter_id: str,
        request: Request,
        captured_at: datetime,
        event_id: Annotated[str, Query(pattern=r"^[a-zA-Z0-9_-]{1,80}$")],
        x_wifi_rssi: Annotated[int | None, Header(ge=-127, le=0)] = None,
    ):
        data = await image_body(request)
        try:
            return await run_in_threadpool(
                service.process, meter_id, event_id, captured_at, data, x_wifi_rssi
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.post("/api/meters/{meter_id}/test", dependencies=[Depends(admin)])
    async def test(meter_id: str, request: Request, captured_at: datetime):
        config(meter_id)
        data = await image_body(request)
        try:
            return await run_in_threadpool(
                service.process, meter_id, "test", captured_at, data, None, True
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.get("/api/meters/{meter_id}/history", dependencies=[Depends(admin)])
    def history(meter_id: str, limit: int = Query(100, ge=1, le=1000), before: int | None = None):
        config(meter_id)
        rows = service.store.all(
            "SELECT * FROM readings WHERE meter_id=? AND seq<? ORDER BY seq DESC LIMIT ?",
            (meter_id, before or 2**63 - 1, limit),
        )
        return [public_reading(row) for row in rows]

    @app.post("/api/meters/{meter_id}/manual", dependencies=[Depends(admin)])
    def manual(meter_id: str, correction: ManualReading):
        config(meter_id)
        try:
            return service.manual(meter_id, correction)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.get("/api/meters/{meter_id}/images/{event_id}/{variant}", dependencies=[Depends(admin)])
    def image(meter_id: str, event_id: str, variant: Literal["original", "overlay", "processed"]):
        row = service.store.one(
            "SELECT image_key FROM readings WHERE meter_id=? AND event_id=?", (meter_id, event_id)
        )
        if not row or not row["image_key"]:
            raise HTTPException(404, "Image not found")
        path = service.images / f"{row['image_key']}-{variant}.jpg"
        if not path.exists():
            raise HTTPException(404, "Image expired or variant unavailable")
        # Original may be a PNG; use signature instead of trusting the filename.
        media = "image/png" if path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
        return FileResponse(path, media_type=media)

    @app.get("/api/config/export", dependencies=[Depends(admin)])
    def export():
        return {"version": 1, "meters": [cfg.model_dump() for cfg in service.store.configs()]}

    @app.post("/api/config/import", dependencies=[Depends(admin)])
    async def import_config(request: Request):
        # Update existing meters only. Registration/secret rotation is always explicit.
        data = await image_body(request)
        if len(data) > 128_000:
            raise HTTPException(413, "Configuration is too large")
        try:
            payload = json.loads(data)
            if payload["version"] != 1 or len(payload["meters"]) > 100:
                raise ValueError("Unsupported version or too many meters")
            configs = [MeterConfig.model_validate(m) for m in payload["meters"]]
            if len({c.id for c in configs}) != len(configs):
                raise ValueError("Duplicate meter IDs")
            with service.processing, service.store.transaction() as db:
                for cfg in configs:
                    if cfg.mode != "Docker":
                        raise ValueError("Local OCR is not installed")
                    if (
                        db.execute(
                            "UPDATE meters SET config=? WHERE id=?", (cfg.model_dump_json(), cfg.id)
                        ).rowcount
                        != 1
                    ):
                        raise ValueError(f"Register meter {cfg.id} before importing")
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(422, str(exc)) from None
        return {"updated": len(configs)}

    @app.get("/api/meters/{meter_id}/logs", dependencies=[Depends(admin)])
    def logs(meter_id: str, limit: int = Query(1000, ge=1, le=10000)):
        config(meter_id)
        rows = service.store.all(
            "SELECT * FROM readings WHERE meter_id=? ORDER BY seq DESC LIMIT ?", (meter_id, limit)
        )
        content = "\n".join(json.dumps(public_reading(row)) for row in rows)
        return StreamingResponse(
            io.BytesIO(content.encode()),
            media_type="application/x-ndjson",
            headers={"Content-Disposition": f'attachment; filename="{meter_id}-readings.ndjson"'},
        )

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(WEB / "index.html")

    app.mount("/static", StaticFiles(directory=WEB), name="static")
    return app
