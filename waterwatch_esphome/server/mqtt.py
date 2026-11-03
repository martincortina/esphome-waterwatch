import json
import logging
import threading

import paho.mqtt.client as mqtt

log = logging.getLogger(__name__)


def discovery(cfg, prefix="waterwatch", discovery_prefix="homeassistant"):
    base = f"{prefix}/{cfg.id}"
    device = {
        "identifiers": [f"waterwatch_{cfg.id}"],
        "name": cfg.name,
        "manufacturer": "WaterWatch DIY",
        "model": "Optical water meter",
        "sw_version": "0.7.0",
    }
    definitions = [
        ("sensor", "total_m3", "Total water", "water", "m³", "total_increasing"),
        ("sensor", "rate_lph", "Average water flow", "volume_flow_rate", "L/h", "measurement"),
        ("sensor", "confidence", "OCR confidence", None, "%", "measurement"),
        ("sensor", "rssi", "Wi-Fi signal", "signal_strength", "dBm", "measurement"),
        ("sensor", "last_success", "Last verified reading", "timestamp", None, None),
        ("sensor", "error", "Reading status", None, None, None),
        ("binary_sensor", "camera_online", "Camera available", "connectivity", None, None),
    ]
    messages = []
    for component, key, name, device_class, unit, state_class in definitions:
        # Stable IDs never include board hardware, OCR mode or model version.
        uid = f"waterwatch_{cfg.id}_{key}"
        payload = {
            "name": name,
            "unique_id": uid,
            "device": device,
            "state_topic": f"{base}/state",
            "value_template": "{{ value_json."
            + key
            + " if value_json."
            + key
            + " is not none else 'None' }}",
            "availability": [{"topic": f"{prefix}/server/availability"}],
            "qos": 1,
        }
        if key in ("total_m3", "rate_lph", "last_success"):
            payload["availability"].append({"topic": f"{base}/availability"})
            payload["availability_mode"] = "all"
        if component == "binary_sensor":
            payload["value_template"] = "{{ 'ON' if value_json.camera_online else 'OFF' }}"
        if device_class:
            payload["device_class"] = device_class
        if unit:
            payload["unit_of_measurement"] = unit
        if state_class:
            payload["state_class"] = state_class
        if key in ("error", "rssi", "confidence", "last_success"):
            payload["entity_category"] = "diagnostic"
        messages.append((f"{discovery_prefix}/{component}/{uid}/config", payload))
    messages.append(
        (
            f"{discovery_prefix}/binary_sensor/waterwatch_{cfg.id}_server/config",
            {
                "name": "OCR server available",
                "unique_id": f"waterwatch_{cfg.id}_server",
                "device": device,
                "state_topic": f"{prefix}/server/availability",
                "device_class": "connectivity",
                "payload_on": "online",
                "payload_off": "offline",
            },
        )
    )
    return messages


class MqttBridge:
    def __init__(self, service):
        self.service = service
        self.settings = service.settings
        self.connected = threading.Event()
        self.announce = threading.Event()
        self.known_configs = {}
        self.last_status = {}
        self.client = None

    def start(self):
        s = self.settings
        if not s.mqtt_host:
            return
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id="waterwatch-server", clean_session=True
        )
        if s.mqtt_username:
            self.client.username_pw_set(s.mqtt_username, s.mqtt_password)
        if s.mqtt_ca:
            self.client.tls_set(ca_certs=s.mqtt_ca)
        self.client.will_set(f"{s.topic_prefix}/server/availability", "offline", qos=1, retain=True)
        self.client.reconnect_delay_set(min_delay=1, max_delay=60)
        self.client.on_connect = self._connected
        self.client.on_disconnect = lambda *args: self.connected.clear()
        self.client.on_message = self._message
        self.client.connect_async(s.mqtt_host, s.mqtt_port, keepalive=30)
        self.client.loop_start()

    def _connected(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log.warning("MQTT connection refused: %s", reason_code)
            return
        self.connected.set()
        self.announce.set()
        client.subscribe(f"{self.settings.discovery_prefix}/status", qos=1)

    def _message(self, client, userdata, message):
        if message.payload == b"online":
            self.announce.set()

    def publish(self, topic, payload):
        data = json.dumps(payload, ensure_ascii=False) if isinstance(payload, dict) else payload
        info = self.client.publish(topic, data, qos=1, retain=True)
        info.wait_for_publish(timeout=5)
        if info.rc != mqtt.MQTT_ERR_SUCCESS or not info.is_published():
            raise ConnectionError("MQTT publish was not acknowledged")

    def tick(self):
        if not self.connected.is_set():
            return
        force = self.announce.is_set()
        if force:
            self.announce.clear()
        s = self.settings
        try:
            self.publish(f"{s.topic_prefix}/server/availability", "online")
            for cfg in self.service.store.configs():
                changed = self.known_configs.get(cfg.id) != cfg.model_dump_json()
                if force or changed:
                    for topic, payload in discovery(cfg, s.topic_prefix, s.discovery_prefix):
                        self.publish(topic, payload)
                    self.known_configs[cfg.id] = cfg.model_dump_json()
                with self.service.store.transaction() as db:
                    state = self.service.state(cfg.id)
                    status_changed = force or changed or self.last_status.get(cfg.id) != state
                    if status_changed:
                        # Snapshot and queue atomically relative to newly committed captures.
                        self.service.store.enqueue(db, f"{s.topic_prefix}/{cfg.id}/state", state)
                if status_changed:
                    self.publish(
                        f"{s.topic_prefix}/{cfg.id}/availability",
                        "offline" if state["stale"] else "online",
                    )
                    self.last_status[cfg.id] = state
            for row in self.service.store.all("SELECT * FROM outbox ORDER BY id LIMIT 100"):
                self.publish(row["topic"], row["payload"])
                with self.service.store.transaction() as db:
                    db.execute("DELETE FROM outbox WHERE id=?", (row["id"],))
        except (OSError, RuntimeError, ConnectionError):
            self.announce.set()
            log.warning("MQTT delivery pending; will retry", exc_info=True)

    def stop(self):
        if self.client:
            try:
                if self.connected.is_set():
                    self.publish(f"{self.settings.topic_prefix}/server/availability", "offline")
            finally:
                self.client.disconnect()
                self.client.loop_stop()
