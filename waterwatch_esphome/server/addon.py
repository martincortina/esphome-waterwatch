"""Home Assistant add-on entry point. Credentials stay in process memory."""

import os
import secrets
from pathlib import Path

import httpx
import uvicorn
from pydantic import BaseModel, ConfigDict, Field

from server.config import Settings
from server.main import create_app


class AddonOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    publish_to_mqtt: bool = False
    image_retention_days: int = Field(default=7, ge=1, le=365)


def addon_settings(options, supervisor_token, data_dir=Path("/data"), transport=None):
    if not supervisor_token:
        raise ValueError("Supervisor token is unavailable")
    broker = {}
    if options.publish_to_mqtt:
        try:
            with httpx.Client(
                headers={"Authorization": f"Bearer {supervisor_token}"},
                timeout=10, follow_redirects=False, trust_env=False, transport=transport,
            ) as client:
                response = client.get("http://supervisor/services/mqtt")
                response.raise_for_status()
                payload = response.json()
                if payload.get("result") != "ok":
                    raise ValueError("MQTT service not ready")
                broker = payload["data"]
                if not isinstance(broker.get("host"), str) or not broker["host"]:
                    raise ValueError("MQTT service has no host")
                if broker.get("ssl"):
                    raise ValueError("This add-on profile expects the internal MQTT service without TLS")
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise ValueError(
                "MQTT service is unavailable or unsupported; disable publication or fix the broker"
            ) from None
    settings = Settings(
        data_dir=data_dir,
        admin_token=secrets.token_urlsafe(48),
        image_retention_days=options.image_retention_days,
        home_assistant_url="http://supervisor/core/api",
        home_assistant_token=supervisor_token,
        ingress_mode=True,
        mqtt_host=broker.get("host", ""),
        mqtt_port=int(broker.get("port", 1883)),
        mqtt_username=broker.get("username", ""),
        mqtt_password=broker.get("password", ""),
    )
    settings.validate()
    return settings


def main():
    try:
        options = AddonOptions.model_validate_json(Path("/data/options.json").read_text())
        settings = addon_settings(options, os.environ.get("SUPERVISOR_TOKEN", ""))
    except (ValueError, OSError):
        raise SystemExit("WaterWatch startup failed: check add-on options, Supervisor and MQTT service") from None
    uvicorn.run(
        create_app(settings), host="0.0.0.0", port=8099, workers=1,
        proxy_headers=False, access_log=False,
    )


if __name__ == "__main__":
    main()
