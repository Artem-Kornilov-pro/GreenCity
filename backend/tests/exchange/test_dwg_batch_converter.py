"""exchange/dwg_batch_converter.py -- dwg2dxf -- внешний бинарник (LibreDWG), которого
на тестовой машине может не быть, поэтому subprocess.run и shutil.which
подменяются: тест сам "конвертирует" .dwg в .dxf, копируя заранее собранный
ezdxf-документ на путь вывода -- это ровно то, что делает настоящий dwg2dxf,
просто без реального бинарного формата DWG на входе."""

from pathlib import Path

import ezdxf
import pytest

from exchange.dwg_batch_converter import Dwg2DxfNotFound, merge_dwg_files


def _write_dxf_with_line(path: Path, layer: str) -> None:
    doc = ezdxf.new()
    doc.layers.add(layer)
    doc.modelspace().add_line((0, 0), (1, 1), dxfattribs={"layer": layer})
    doc.saveas(path)


@pytest.fixture
def fake_dwg2dxf(monkeypatch, tmp_path):
    """Подменяет dwg2dxf так, что каждый *.dwg с телом b"good" конвертируется
    в валидный DXF с одной линией, а с телом b"bad" -- проваливается
    (returncode != 0), как повёл бы себя реальный dwg2dxf на файле, который
    не умеет читать."""

    def fake_which(name):
        return "/usr/bin/dwg2dxf" if name == "dwg2dxf" else None

    class FakeCompleted:
        def __init__(self, returncode, stderr=b""):
            self.returncode = returncode
            self.stdout = b""
            self.stderr = stderr

    def fake_run(args, capture_output):
        out_path = Path(args[2])
        in_path = Path(args[3])
        if in_path.read_bytes() == b"bad":
            # Байты, не валидные как UTF-8 (\xbd), -- ровно то, что реальный
            # dwg2dxf пишет в stderr на кириллических именах слоёв, и ровно
            # то, из-за чего text=True раньше падал внутри subprocess.run
            # (см. комментарий у _convert_one).
            return FakeCompleted(1, stderr=b"dwg2dxf: unsupported object type \xbd")
        _write_dxf_with_line(out_path, layer=in_path.stem)
        return FakeCompleted(0)

    monkeypatch.setattr("exchange.dwg_batch_converter.shutil.which", fake_which)
    monkeypatch.setattr("exchange.dwg_batch_converter.subprocess.run", fake_run)


def test_merge_dwg_files_raises_when_tool_missing(monkeypatch, tmp_path):
    monkeypatch.setattr("exchange.dwg_batch_converter.shutil.which", lambda name: None)
    dwg = tmp_path / "a.dwg"
    dwg.write_bytes(b"good")
    with pytest.raises(Dwg2DxfNotFound):
        merge_dwg_files([dwg], tmp_path)


def test_merge_dwg_files_combines_entities_from_all_files(fake_dwg2dxf, tmp_path):
    a = tmp_path / "a.dwg"
    b = tmp_path / "b.dwg"
    a.write_bytes(b"good")
    b.write_bytes(b"good")

    result = merge_dwg_files([a, b], tmp_path)

    assert sorted(result.converted) == ["a.dwg", "b.dwg"]
    assert result.failed == {}
    lines = list(result.doc.modelspace().query("LINE"))
    assert len(lines) == 2


def test_merge_dwg_files_reports_partial_failure_without_stopping_the_batch(fake_dwg2dxf, tmp_path):
    good = tmp_path / "good.dwg"
    bad = tmp_path / "bad.dwg"
    good.write_bytes(b"good")
    bad.write_bytes(b"bad")

    result = merge_dwg_files([good, bad], tmp_path)

    assert result.converted == ["good.dwg"]
    assert "bad.dwg" in result.failed
    assert "unsupported object type" in result.failed["bad.dwg"]
    lines = list(result.doc.modelspace().query("LINE"))
    assert len(lines) == 1


def test_merge_dwg_files_falls_back_to_recover_on_unicode_decode_error(fake_dwg2dxf, tmp_path, monkeypatch):
    """dwg2dxf на реальных DWG иногда пишет несогласованную кодировку текста
    (см. docstring _read_converted_dxf) -- строгий ezdxf.readfile должен
    падать с UnicodeDecodeError, а merge_dwg_files -- восстанавливаться через
    ezdxf.recover.readfile, а не терять файл целиком."""
    real_readfile = ezdxf.readfile

    def _flaky_readfile(path):
        if Path(path).stem == "flaky":
            raise UnicodeDecodeError("utf-8", b"\xbd", 0, 1, "simulated dwg2dxf encoding bug")
        return real_readfile(path)

    monkeypatch.setattr("exchange.dwg_batch_converter.ezdxf.readfile", _flaky_readfile)

    dwg = tmp_path / "flaky.dwg"
    dwg.write_bytes(b"good")

    result = merge_dwg_files([dwg], tmp_path)

    assert result.converted == ["flaky.dwg"]
    assert result.failed == {}
    assert len(list(result.doc.modelspace().query("LINE"))) == 1


def test_merge_dwg_files_all_failed_returns_empty_converted(fake_dwg2dxf, tmp_path):
    bad = tmp_path / "bad.dwg"
    bad.write_bytes(b"bad")

    result = merge_dwg_files([bad], tmp_path)

    assert result.converted == []
    assert "bad.dwg" in result.failed
    assert len(result.doc.modelspace()) == 0


def test_merge_dwg_files_processes_in_original_order_regardless_of_size(fake_dwg2dxf, tmp_path):
    # Обработка последовательная (issue #50 follow-up -- см. докстринг модуля
    # про то, почему параллелить это оказалось не как выиграть: пул потоков
    # не ускоряет из-за GIL, а пул процессов в реальном процессе backend
    # съедал весь выигрыш на накладных расходах), поэтому результат должен
    # быть строго в порядке dwg_paths -- регрессия на случайную перестановку.
    paths = [tmp_path / f"{name}.dwg" for name in ("c", "a", "b")]
    for p in paths:
        p.write_bytes(b"good")

    result = merge_dwg_files(paths, tmp_path)

    assert result.converted == ["c.dwg", "a.dwg", "b.dwg"]
