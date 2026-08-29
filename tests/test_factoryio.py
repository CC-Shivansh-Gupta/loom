import time

from loom.fakefactory import FakeFactory
from loom.factoryio import Feed, load_map
from loom.modbus import ModbusClient, ModbusServer


def test_modbus_roundtrip():
    srv = ModbusServer(port=0).start()
    try:
        c = ModbusClient("127.0.0.1", srv.port)
        srv.discrete[3] = True
        srv.input[2] = 1234
        assert c.read_discrete_inputs(0, 8) == [False, False, False, True] + [False] * 4
        assert c.read_input_registers(0, 4) == [0, 0, 1234, 0]
        c.write_coil(5, True)
        c.write_register(1, 77)
        assert srv.coils[5] is True and srv.holding[1] == 77
        assert c.read_coils(4, 3) == [False, True, False]
        c.close()
    finally:
        srv.stop()


def test_map_profiles_follow_the_sensors():
    mp, cfg = load_map("configs/factoryio_map.yaml")
    prof = {s.id: s.profile for s in mp.stations}
    assert prof == {"S1": "photo_eyes", "S2": "photo_eyes", "S3": "dark",
                    "S4": "photo_eyes", "S5": "exit_eye", "S6": "photo_eyes"}
    assert mp.input_span == (0, 12)


def test_feed_reconstructs_a_live_line_through_modbus():
    speed = 120.0
    fake = FakeFactory("configs/factoryio_map.yaml", speed=speed, port=0).start()
    try:
        mp, cfg = load_map("configs/factoryio_map.yaml")
        mp.port = fake.port
        mp.time_scale = speed
        feed = Feed(mp, cfg)
        feed.t0 = time.perf_counter()
        feed.start()
        time.sleep(12.0)                       # ~24 sim minutes: S2 wears from 10 min
        feed.stop()
    finally:
        fake.stop()
    twin = feed.twin
    assert feed.errors == 0 and feed.polls > 300
    assert feed.events > 150
    # vehicles flowed end to end through the twin, S3 (dark) was reconstructed
    assert twin.exited >= 30
    twin.refresh()
    assert twin.stations["S3"].state.source == "inferred"
    assert twin.stations["S3"].inferred_samples > 5
    assert twin.stations["S1"].measured_samples > 20
    # the wear at S2 was forecast from sensor edges alone
    assert any(x.action == "raised" and x.alert.station == "S2" for x in twin.log), [str(x) for x in twin.log]
    f = feed.frame()
    assert len(f["st"]) == 6 and f["sensors"]["polls"] == feed.polls
