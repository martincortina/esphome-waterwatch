import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


def secret(name: str, default: str = "") -> str:
    path = os.getenv(f"{name}_FILE")
    return Path(path).read_text().strip() if path else os.getenv(name, default)


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    admin_token: str = field(repr=False)
    mqtt_host: str = ""
    mqtt_port: int = 1883
    mqtt_username: str = ""
    mqtt_password: str = field(default="", repr=False)
    mqtt_ca: str = ""
    topic_prefix: str = "waterwatch"
    discovery_prefix: str = "homeassistant"
    image_retention_days: int = 7
    home_assistant_url: str = ""
    home_assistant_token: str = field(default="", repr=False)
    ingress_mode: bool = False

    @classmethod
    def from_env(cls):
        return cls(
            data_dir=Path(os.getenv("DATA_DIR", "data")),
            admin_token=secret("ADMIN_TOKEN"),
            mqtt_host=os.getenv("MQTT_HOST", ""),
            mqtt_port=int(os.getenv("MQTT_PORT", "1883")),
            mqtt_username=os.getenv("MQTT_USERNAME", ""),
            mqtt_password=secret("MQTT_PASSWORD"),
            mqtt_ca=os.getenv("MQTT_CA", ""),
            topic_prefix=os.getenv("MQTT_PREFIX", "waterwatch"),
            discovery_prefix=os.getenv("DISCOVERY_PREFIX", "homeassistant"),
            image_retention_days=int(os.getenv("IMAGE_RETENTION_DAYS", "7")),
            home_assistant_url=os.getenv("HOME_ASSISTANT_URL", ""),
            home_assistant_token=secret("HOME_ASSISTANT_TOKEN"),
            ingress_mode=os.getenv("WATERWATCH_INGRESS", "0") == "1",
        )

    def validate(self):
        if len(self.admin_token) < 32 or self.admin_token.startswith("CHANGE"):
            raise ValueError("Set a random ADMIN_TOKEN with at least 32 characters")
        for prefix in (self.topic_prefix, self.discovery_prefix):
            if not prefix or any(c in prefix for c in "+#\x00"):
                raise ValueError("Invalid MQTT topic prefix")
        if not 1 <= self.image_retention_days <= 365:
            raise ValueError("IMAGE_RETENTION_DAYS must be 1..365")
        if bool(self.home_assistant_url) != bool(self.home_assistant_token):
            raise ValueError("Home Assistant URL and token must be configured together")
        if self.home_assistant_url:
            url = urlsplit(self.home_assistant_url)
            if (
                url.scheme not in ("http", "https")
                or not url.hostname
                or url.username
                or url.password
                or url.query
                or url.fragment
                or not url.path.rstrip("/").endswith("/api")
            ):
                raise ValueError("HOME_ASSISTANT_URL must be an HTTP(S) API base without credentials")
