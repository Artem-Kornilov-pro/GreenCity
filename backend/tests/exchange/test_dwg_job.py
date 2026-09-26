"""exchange/dwg_job.py -- разбор пачки .dwg в отдельном процессе и очередь
слотов. Настоящего dwg2dxf на тестовой машине может не быть: для запуска в
дочернем процессе подменой (monkeypatch) не обойтись, поэтому на PATH кладётся
поддельный исполняемый dwg2dxf -- скрипт на Python, который пишет валидный
DXF, как настоящий. Такие тесты -- только на POSIX (скрипт с shebang, fcntl)."""

import json
import os
import stat
import sys
from pathlib import Path

import ezdxf
import pytest

from exchange import dwg_batch_converter, dwg_job
from exchange.dxf_parser import parse_dxf_doc, parse_dxf_file

posix_only = pytest.mark.skipif(os.name == "nt", reason="поддельный dwg2dxf -- скрипт с shebang, слоты -- fcntl")


def _doc_with_site() -> ezdxf.document.Drawing:
    doc = ezdxf.new()
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (40, 0), (40, 30), (0, 30)], close=True, dxfattribs={"layer": "TERRITORY_BOUNDARY"})
    msp.add_lwpolyline([(5, 5), (15, 5), (15, 12), (5, 12)], close=True, dxfattribs={"layer": "BUILDING"})
    msp.add_circle((25, 20), 1.5, dxfattribs={"layer": "ДЕРЕВЬЯ"})
    return doc


def test_build_scene_parses_merged_doc_in_memory_same_as_through_a_file(monkeypatch, tmp_path):
    doc = _doc_with_site()
    doc.saveas(tmp_path / "combined.dxf")
    expected = parse_dxf_file(str(tmp_path / "combined.dxf"))

    def _merged(dwg_paths, work_dir, workers=1):
        result = dwg_batch_converter.BatchConversionResult(doc=_doc_with_site())
        result.converted = ["a.dwg"]
        result.failed = {"b.dwg": "unsupported"}
        return result

    monkeypatch.setattr(dwg_batch_converter, "merge_dwg_files", _merged)
    scene, summary = dwg_job.build_scene([tmp_path / "a.dwg", tmp_path / "b.dwg"], tmp_path)
    for key in expected:
        assert scene[key] == expected[key]
    assert scene["dwgConversionWarnings"] == [{"file": "b.dwg", "error": "unsupported"}]
    assert summary == {"converted": 1, "failed": 1, "objects": len(expected["objects"]), "zones": len(expected["restrictions"])}


def test_build_scene_all_failed_reports_outcome_and_failed_count(monkeypatch, tmp_path):
    def _all_failed(dwg_paths, work_dir, workers=1):
        result = dwg_batch_converter.BatchConversionResult(doc=ezdxf.new())
        result.failed = {p.name: "не читается" for p in dwg_paths}
        return result

    monkeypatch.setattr(dwg_batch_converter, "merge_dwg_files", _all_failed)
    with pytest.raises(dwg_job.DwgJobError) as err:
        dwg_job.build_scene([tmp_path / "a.dwg", tmp_path / "b.dwg"], tmp_path)
    assert (err.value.status, err.value.outcome, err.value.failed) == (400, "all_failed", 2)
    assert "a.dwg" in err.value.detail


def _fake_dwg2dxf(bin_dir: Path, body: str) -> None:
    """Исполняемый dwg2dxf в bin_dir: `dwg2dxf -o out.dxf in.dwg`."""
    bin_dir.mkdir(exist_ok=True)
    script = bin_dir / "dwg2dxf"
    script.write_text(f"#!{sys.executable}\nimport os, sys\nout, src = sys.argv[2], sys.argv[3]\n{body}\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def isolated_env(monkeypatch, tmp_path):
    bin_dir = tmp_path / "bin"
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("DWG_SLOT_DIR", str(tmp_path / "slots"))
    return bin_dir


@posix_only
def test_run_isolated_returns_scene_json_from_child_process(isolated_env, tmp_path):
    _fake_dwg2dxf(
        isolated_env,
        "import ezdxf\n"
        "doc = ezdxf.new(); msp = doc.modelspace()\n"
        "msp.add_lwpolyline([(0, 0), (40, 0), (40, 30), (0, 30)], close=True, dxfattribs={'layer': 'TERRITORY_BOUNDARY'})\n"
        "msp.add_circle((25, 20), 1.5, dxfattribs={'layer': 'ДЕРЕВЬЯ'})\n"
        "doc.saveas(out)",
    )
    work = tmp_path / "work"
    work.mkdir()
    (work / "a.dwg").write_bytes(b"dwg")
    payload, summary = dwg_job.run([work / "a.dwg"], work, isolated=True)
    scene = json.loads(payload)
    assert scene["boundary"] is not None
    assert "buildingSetbacks" in scene
    assert summary["converted"] == 1 and summary["failed"] == 0


@posix_only
def test_run_isolated_reports_job_error_from_child(isolated_env, tmp_path):
    _fake_dwg2dxf(isolated_env, "sys.stderr.write('unsupported object'); sys.exit(1)")
    work = tmp_path / "work"
    work.mkdir()
    (work / "a.dwg").write_bytes(b"dwg")
    with pytest.raises(dwg_job.DwgJobError) as err:
        dwg_job.run([work / "a.dwg"], work, isolated=True)
    assert (err.value.status, err.value.outcome, err.value.failed) == (400, "all_failed", 1)


@posix_only
def test_run_isolated_turns_killed_child_into_clear_error(isolated_env, tmp_path):
    # Дочерний процесс убит системой (как при нехватке памяти) -- раньше это
    # убивало процесс uvicorn и давало 502, теперь -- понятная ошибка.
    _fake_dwg2dxf(isolated_env, "import signal\nos.kill(os.getppid(), signal.SIGKILL)")
    work = tmp_path / "work"
    work.mkdir()
    (work / "a.dwg").write_bytes(b"dwg")
    with pytest.raises(dwg_job.DwgJobError) as err:
        dwg_job.run([work / "a.dwg"], work, isolated=True)
    assert (err.value.status, err.value.outcome) == (500, "crashed")
    assert "памяти" in err.value.detail


@posix_only
def test_slot_limits_parallel_batches_and_reports_busy(monkeypatch, tmp_path):
    monkeypatch.setenv("DWG_SLOT_DIR", str(tmp_path))
    monkeypatch.setenv("DWG_MAX_PARALLEL", "1")
    with dwg_job.slot():
        with pytest.raises(dwg_job.DwgJobError) as err, dwg_job.slot(wait_s=0):
            pass
        assert (err.value.status, err.value.outcome) == (503, "busy")
    with dwg_job.slot(wait_s=0):  # освобождён -- снова свободен
        pass


@posix_only
def test_slot_allows_as_many_batches_as_configured(monkeypatch, tmp_path):
    monkeypatch.setenv("DWG_SLOT_DIR", str(tmp_path))
    monkeypatch.setenv("DWG_MAX_PARALLEL", "2")
    with dwg_job.slot(wait_s=0), dwg_job.slot(wait_s=0), pytest.raises(dwg_job.DwgJobError), dwg_job.slot(wait_s=0):
        pass


def test_scene_in_memory_equals_parse_of_saved_file_for_empty_doc(tmp_path):
    doc = ezdxf.new()
    doc.saveas(tmp_path / "c.dxf")
    assert parse_dxf_doc(ezdxf.new()) == parse_dxf_file(str(tmp_path / "c.dxf"))
