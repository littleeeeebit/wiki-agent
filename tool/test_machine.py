"""The machine's size line and its load line (plan 15a, "The machine and its load")."""

import json
import time

import machine


def test_size_line_leaves_out_what_was_not_read():
    found = {"cpu": {"name": "Ryzen 7", "cores": 8, "threads": 16}, "ram_gb": 32,
             "gpus": [{"name": "RTX 3080", "vram_gb": 10}, {"name": "Intel UHD"}],
             "disks": [{"name": "Samsung 980", "type": "SSD", "bus": "NVMe", "gb": 1000}, {"name": "Old"}]}
    line = machine.size_line(found)
    assert "CPU Ryzen 7 (8 cores, 16 threads)" in line and "RAM 32 GB" in line
    assert "GPU RTX 3080 10 GB VRAM" in line and "GPU Intel UHD ·" in line   # two adapters, one without VRAM
    assert "Samsung 980 (SSD, NVMe, 1000 GB); Old" in line
    assert "RAM" not in machine.size_line({"cpu": {"name": "X"}})   # RAM unread: left out, never guessed
    assert machine.size_line({}) == ""


def test_load_line_says_what_it_could_not_read():
    now = time.time()
    two = {"at": now, "cpu": 12.0, "ram": (10.0, 32.0),
           "gpus": [{"uuid": "a", "name": "A", "used": 9216, "total": 10240},
                    {"uuid": "b", "name": "B", "used": 0, "total": 8192}],
           "holders": [{"gpu": "a", "pid": 42, "name": "python.exe"}, {"gpu": "a", "pid": 7, "name": ""}]}
    line = machine.load_line(two, [])
    assert "GPU 0 VRAM 9.0/10.0 GB used" in line and "GPU 1 VRAM 0.0/8.0 GB used" in line
    assert "python.exe 42, ? 7" in line and machine.UNOWNED not in line
    assert machine.UNOWNED in machine.load_line(two, None)
    assert "this session's own on the GPU: python.exe 42" in machine.load_line(two, [two["holders"][0]])
    blind = machine.load_line({"at": now, "cpu": None, "ram": None, "gpus": None, "holders": None}, None)
    assert f"CPU {machine.UNREAD}" in blind and f"RAM {machine.UNREAD} (no RAM floor)" in blind
    assert "nvidia-smi gave nothing" in blind and machine.UNOWNED not in blind   # no GPU: no owner to tell
    assert machine.load_line({**two, "at": now - machine.STALE - 1}, []) == ""


def test_floor_holds_on_either_adapter_or_ram_and_not_on_what_was_not_read():
    gpus = [{"uuid": "a", "name": "A", "used": 1000, "total": 10240}, {"uuid": "b", "name": "B", "used": 7500,
                                                                       "total": 8192}]
    assert "GPU 1 VRAM free" in machine.floor({"gpus": gpus, "ram": (1, 32)})
    assert "RAM free" in machine.floor({"gpus": gpus[:1], "ram": (30, 32)})
    assert machine.floor({"gpus": gpus[:1], "ram": (1, 32)}) == ""
    assert machine.floor({"gpus": None, "ram": None}) == ""


def test_a_test_machine_reads_its_load_from_a_file(tmp_path, monkeypatch):
    given = tmp_path / "load.json"
    given.write_text(json.dumps({"cpu": 1.0, "ram": [1, 2], "gpus": None, "holders": None}), encoding="utf-8")
    monkeypatch.setenv("WIKI_AGENT_LOAD_FILE", str(given))
    assert machine.sample()["ram"] == [1, 2] and time.time() - machine.sample()["at"] < 5


def test_a_real_sample_has_every_part_and_a_holder_list_only_with_its_adapters():
    found = machine.measured()
    assert set(found) == {"at", "cpu", "ram", "gpus", "holders"}
    assert found["gpus"] is not None or found["holders"] is None
