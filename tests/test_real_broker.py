"""Opt-in live MQTT integration; Docker test network has no published ports."""

import json
import os
import queue
import time

import paho.mqtt.client as mqtt
import pytest

from server.config import Settings
from server.models import ManualReading, MeterConfig
from server.mqtt import MqttBridge
from server.service import Service


@pytest.mark.skipif(
    not os.getenv("TEST_MQTT_HOST"), reason="Set TEST_MQTT_HOST or use docker-compose.test.yml"
)
def test_retained_discovery_and_state_on_real_broker(tmp_path):
    host = os.environ["TEST_MQTT_HOST"]
    service = Service(Settings(tmp_path, "a" * 48, mqtt_host=host))
    service.save_config(MeterConfig(id="integration", name="Integration"), create=True)
    service.manual(
        "integration", ManualReading(value_m3="42.123", reason="Integration test baseline")
    )
    bridge = MqttBridge(service)
    received = queue.Queue()
    observer = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="waterwatch-test-observer")
    observer.on_message = lambda c, u, m: received.put((m.topic, m.payload, m.retain))
    try:
        bridge.start()
        assert bridge.connected.wait(15)
        bridge.tick()
        # Subscribe AFTER publication to verify retained discovery and retained state.
        observer.connect(host)
        observer.subscribe("homeassistant/sensor/waterwatch_integration_total_m3/config", qos=1)
        observer.subscribe("waterwatch/integration/state", qos=1)
        observer.loop_start()
        messages = {}
        deadline = time.monotonic() + 15
        while len(messages) < 2 and time.monotonic() < deadline:
            topic, payload, retained = received.get(timeout=10)
            messages[topic] = (json.loads(payload), retained)
        assert all(retained for _, retained in messages.values())
        assert messages["waterwatch/integration/state"][0]["total_m3"] == "42.123000"
        assert (
            messages["homeassistant/sensor/waterwatch_integration_total_m3/config"][0][
                "state_class"
            ]
            == "total_increasing"
        )
    finally:
        observer.disconnect()
        observer.loop_stop()
        bridge.stop()
        service.store.close()
