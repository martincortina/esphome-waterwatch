import json

from server.models import MeterConfig
from server.mqtt import discovery


def test_home_assistant_metadata_and_stable_identity():
    cfg = MeterConfig(id="house", name="House")
    messages = dict(discovery(cfg))
    total = messages["homeassistant/sensor/waterwatch_house_total_m3/config"]
    assert total["device_class"] == "water"
    assert total["unit_of_measurement"] == "m³"
    assert total["state_class"] == "total_increasing"
    assert total["unique_id"] == "waterwatch_house_total_m3"
    cfg.mode = "ESP32"
    assert dict(discovery(cfg))["homeassistant/sensor/waterwatch_house_total_m3/config"] == total
    assert total["availability_mode"] == "all"
    assert len(messages) == 8


def test_outbox_survives_broker_failure_and_reconnect(client):
    bridge = client.bridge
    pending = client.service.store.one("SELECT count(*) AS n FROM outbox")["n"]
    assert pending == 1
    bridge.tick()  # disconnected, must preserve outbox
    assert client.service.store.one("SELECT count(*) AS n FROM outbox")["n"] == 1
    bridge.connected.set()

    def fail(*args):
        raise ConnectionError("broker unavailable")

    bridge.publish = fail
    bridge.tick()
    assert client.service.store.one("SELECT count(*) AS n FROM outbox")["n"] >= 1
    sent = []
    bridge.publish = lambda topic, payload: sent.append((topic, payload))
    bridge.tick()
    assert client.service.store.one("SELECT count(*) AS n FROM outbox")["n"] == 0
    assert any(topic.endswith("/config") for topic, _ in sent)
    states = [json.loads(p) for t, p in sent if t == "waterwatch/house/state"]
    assert states[-1]["total_m3"] == "123.456000"


def test_home_assistant_birth_republishes_discovery(client):
    bridge = client.bridge
    bridge.connected.set()
    sent = []
    bridge.publish = lambda topic, payload: sent.append((topic, payload))
    bridge.tick()
    sent.clear()

    class Birth:
        payload = b"online"

    bridge._message(None, None, Birth())
    bridge.tick()
    assert len([t for t, _ in sent if t.endswith("/config")]) == 8
