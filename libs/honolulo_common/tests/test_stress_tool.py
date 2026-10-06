"""La herramienta de estrés (scripts/stress.py): solo apunta al sistema local y sus escenarios son ampliables."""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

import stress  # noqa: E402


def run_cli(*args: str):
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "stress.py"), *args], capture_output=True, text=True,
                          timeout=60)


@pytest.mark.parametrize("target", ["http://example.com", "http://192.168.1.10:8000", "https://honolulo.pe",
                                    "http://10.0.0.5", "http://127.0.0.1.evil.example"])
def test_it_refuses_to_target_anything_but_the_local_machine(target):
    """No es una herramienta para atacar servidores ajenos."""
    result = run_cli("read-flood", "--base", target, "--duration", "1")
    assert result.returncode != 0
    assert "Solo se permite probar el sistema local" in (result.stderr + result.stdout)


def test_the_load_is_capped_so_a_typo_cannot_flood_the_machine(monkeypatch):
    seen = {}
    monkeypatch.setitem(stress.SCENARIOS, "baseline",
                        stress.Scenario("baseline", "x", lambda settings: seen.update(s=settings)))
    monkeypatch.setattr(sys, "argv", ["stress.py", "baseline", "--concurrency", "99999", "--duration", "99999",
                                      "--sockets", "99999"])
    stress.main()
    settings = seen["s"]
    assert settings.concurrency == stress.MAX_CONCURRENCY and settings.duration == stress.MAX_DURATION
    assert settings.sockets == stress.MAX_SOCKETS


def test_every_documented_scenario_is_registered_and_new_ones_need_no_changes_elsewhere(monkeypatch):
    assert {"baseline", "read-flood", "ramp", "login-flood", "slow-connections", "oversize", "recovery",
            "all"} <= set(stress.SCENARIOS)
    called = []
    monkeypatch.setitem(stress.SCENARIOS, "demo", stress.Scenario("demo", "uno nuevo", lambda s: called.append(s.host)))
    monkeypatch.setattr(sys, "argv", ["stress.py", "demo"])
    stress.main()
    assert called == ["127.0.0.1"]


def test_percentiles_and_verdicts():
    assert stress.percentile([], .5) != stress.percentile([], .5)             # sin datos: NaN
    assert stress.percentile([10, 20, 30, 40, 50], .5) == 30
    healthy = stress.Samples([(0.05, "200")] * 100)
    assert stress.verdict(healthy).startswith("✅")
    slow = stress.Samples([(2.0, "200")] * 100)
    assert stress.verdict(slow).startswith("⚠️")
    broken = stress.Samples([(0.1, "PUERTOS-LOCALES")] * 90 + [(0.1, "200")] * 10)
    assert stress.verdict(broken).startswith("❌")


def test_local_port_exhaustion_is_labelled_as_a_test_artefact_not_a_server_failure():
    assert stress.classify_error(OSError("[WinError 10048] Solo se permite un uso")) == "PUERTOS-LOCALES"
    assert stress.classify_error(ConnectionResetError("reset")) == "ERR:ConnectionResetError"
