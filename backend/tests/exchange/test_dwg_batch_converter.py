"""exchange/dwg_batch_converter.py -- dwg2dxf -- внешний бинарник (LibreDWG), которого
на тестовой машине может не быть, поэтому subprocess.run и shutil.which
подменяются: тест сам "конвертирует" .dwg в .dxf, копируя заранее собранный
ezdxf-документ на путь вывода -- это ровно то, что делает настоящий dwg2dxf,
просто без реального бинарного формата DWG на входе."""

import io
import shutil
import subprocess
from collections import Counter
from pathlib import Path, PureWindowsPath

import ezdxf
import pytest

from dxf_parsing.encoding import repair_mojibake
from exchange import dwg_batch_converter
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
        if in_path.read_bytes().removeprefix(b"AC1032 ").startswith(b"bad"):
            # Байты, не валидные как UTF-8 (\xbd), -- ровно то, что реальный
            # dwg2dxf пишет в stderr на кириллических именах слоёв, и ровно
            # то, из-за чего text=True раньше падал внутри subprocess.run
            # (см. комментарий у _convert_one).
            return FakeCompleted(1, stderr=b"dwg2dxf: unsupported object type \xbd")
        # Слой дорожки -- его парсер читает, значит склейка его сохранит.
        _write_dxf_with_line(out_path, layer=f"PATH_{in_path.stem}")
        return FakeCompleted(0)

    monkeypatch.setattr("exchange.dwg_batch_converter.shutil.which", fake_which)
    monkeypatch.setattr("exchange.dwg_batch_converter.subprocess.run", fake_run)


def test_merge_dwg_files_raises_when_tool_missing(monkeypatch, tmp_path):
    monkeypatch.setattr("exchange.dwg_batch_converter.shutil.which", lambda name: None)
    dwg = tmp_path / "a.dwg"
    dwg.write_bytes(b"AC1032 good " + dwg.name.encode())
    with pytest.raises(Dwg2DxfNotFound):
        merge_dwg_files([dwg], tmp_path)


def test_merge_dwg_files_combines_entities_from_all_files(fake_dwg2dxf, tmp_path):
    a = tmp_path / "a.dwg"
    b = tmp_path / "b.dwg"
    a.write_bytes(b"AC1032 good " + a.name.encode())
    b.write_bytes(b"AC1032 good " + b.name.encode())

    result = merge_dwg_files([a, b], tmp_path)

    assert sorted(result.converted) == ["a.dwg", "b.dwg"]
    assert result.failed == {}
    lines = list(result.doc.modelspace().query("LINE"))
    assert len(lines) == 2


def test_merge_dwg_files_reports_partial_failure_without_stopping_the_batch(fake_dwg2dxf, tmp_path):
    good = tmp_path / "good.dwg"
    bad = tmp_path / "bad.dwg"
    good.write_bytes(b"AC1032 good " + good.name.encode())
    bad.write_bytes(b"AC1032 bad " + bad.name.encode())

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
    dwg.write_bytes(b"AC1032 good " + dwg.name.encode())

    result = merge_dwg_files([dwg], tmp_path)

    assert result.converted == ["flaky.dwg"]
    assert result.failed == {}
    assert len(list(result.doc.modelspace().query("LINE"))) == 1


def test_merge_dwg_files_all_failed_returns_empty_converted(fake_dwg2dxf, tmp_path):
    bad = tmp_path / "bad.dwg"
    bad.write_bytes(b"AC1032 bad " + bad.name.encode())

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
        p.write_bytes(b"AC1032 good " + p.name.encode())

    result = merge_dwg_files(paths, tmp_path)

    assert result.converted == ["c.dwg", "a.dwg", "b.dwg"]



# --- Только то, что читает парсер; параллельная обработка файлов ------------


def test_merge_keeps_only_layers_the_parser_reads_and_all_texts(monkeypatch, tmp_path):
    def fake_run(args, capture_output):
        doc = ezdxf.new()
        msp = doc.modelspace()
        msp.add_line((0, 0), (1, 1), dxfattribs={"layer": "ROAD_EDGE"})
        msp.add_line((0, 0), (5, 5), dxfattribs={"layer": "Топосъемка_рельеф"})
        msp.add_text("Дом 1", dxfattribs={"layer": "Подписи"})
        doc.saveas(Path(args[2]))
        return type("Done", (), {"returncode": 0, "stdout": b"", "stderr": b""})()

    monkeypatch.setattr("exchange.dwg_batch_converter.shutil.which", lambda name: "/usr/bin/dwg2dxf")
    monkeypatch.setattr("exchange.dwg_batch_converter.subprocess.run", fake_run)
    dwg = tmp_path / "a.dwg"
    dwg.write_bytes(b"AC1032 x")
    msp = merge_dwg_files([dwg], tmp_path).doc.modelspace()
    assert [e.dxf.layer for e in msp.query("LINE")] == ["ROAD_EDGE"]
    assert [e.dxf.text for e in msp.query("TEXT")] == ["Дом 1"]


class _InProcessPool:
    """Пул "процессов" в том же процессе -- подмены dwg2dxf в настоящие
    дочерние процессы не переходят."""

    def __init__(self, max_workers):
        self.max_workers = max_workers

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def map(self, fn, *iterables):
        return list(map(fn, *iterables))


def test_parallel_merge_gives_the_same_document_in_the_original_order(fake_dwg2dxf, monkeypatch, tmp_path):
    monkeypatch.setattr("exchange.dwg_batch_converter.ProcessPoolExecutor", _InProcessPool)
    paths = []
    for name, body in (("c", b"good"), ("a", b"good"), ("bad", b"bad"), ("b", b"good")):
        path = tmp_path / f"{name}.dwg"
        path.write_bytes(b"AC1032 " + body + b" " + name.encode())  # разное содержимое -- одинаковые файлы пропускаются
        paths.append(path)
    result = merge_dwg_files(paths, tmp_path, workers=3)
    assert result.converted == ["c.dwg", "a.dwg", "b.dwg"]
    assert list(result.failed) == ["bad.dwg"]
    assert [e.dxf.layer for e in result.doc.modelspace().query("LINE")] == ["PATH_c", "PATH_a", "PATH_b"]
    # Промежуточные DXF -- полный и компактный -- после сборки удаляются.
    assert not list(tmp_path.glob("c.dxf")) and not list(tmp_path.glob("c.compact.dxf"))


def test_merge_dwg_files_skips_byte_identical_files(fake_dwg2dxf, tmp_path):
    # Реальная пачка (Харьковская): "АПОТ" и "улица ГП" -- один и тот же файл
    # под двумя именами, и всё его содержимое попадало в сцену дважды.
    a = tmp_path / "АПОТ.dwg"
    b = tmp_path / "улица ГП.dwg"
    c = tmp_path / "посадочный.dwg"
    a.write_bytes(b"AC1032 good same")
    b.write_bytes(b"AC1032 good same")
    c.write_bytes(b"AC1032 good other")

    result = merge_dwg_files([a, b, c], tmp_path)

    assert sorted(result.converted) == ["АПОТ.dwg", "посадочный.dwg"]
    assert "АПОТ.dwg" in result.failed["улица ГП.dwg"]
    assert len(list(result.doc.modelspace().query("LINE"))) == 2


def test_read_converted_dxf_repairs_multiline_values_and_broken_unicode_escapes(tmp_path):
    # dwg2dxf пишет XML спецификации многострочным значением и с битым
    # экранированием "\U+" без hex-цифр: ни ezdxf.readfile, ни recover такой
    # файл не читали (3 из 4 файлов пачки улицы Берзарина).
    from exchange.dwg_batch_converter import _read_converted_dxf

    doc = ezdxf.new()
    msp = doc.modelspace()
    msp.add_line((0, 0), (10, 0), dxfattribs={"layer": "ROAD_EDGE"})
    msp.add_text("SPEC", dxfattribs={"layer": "NOTES"})
    good = tmp_path / "good.dxf"
    doc.saveas(good)
    text = good.read_text(encoding="utf-8")
    backslash = chr(92)
    broken_value = f'SPEC\n    <Item name="ПНД{backslash}U+041F{backslash}U+ 63 мм" count="2" />\n    <Item name="x" />'
    assert "\nSPEC\n" in text
    broken = tmp_path / "broken.dxf"
    broken.write_text(text.replace("\nSPEC\n", "\n" + broken_value + "\n", 1), encoding="utf-8")

    repaired = _read_converted_dxf(broken)

    assert [e.dxf.layer for e in repaired.modelspace().query("LINE")] == ["ROAD_EDGE"]
    assert len(repaired.modelspace().query("TEXT")) == 1
    assert not list(tmp_path.glob("*.clean.dxf"))  # временный файл убран


# --- Отбор с синонимами, блоками и назначениями пользователя -----------------


def _fake_dwg2dxf_writing(monkeypatch, build, encode_utf8_under_cp1252=False):
    def fake_run(args, capture_output):
        doc = ezdxf.new("R2000")
        build(doc)
        out = Path(args[2])
        if encode_utf8_under_cp1252:
            # Как настоящий dwg2dxf: в заголовке ANSI_1252, имена -- в UTF-8.
            text = io.StringIO()
            doc.write(text)
            out.write_bytes(text.getvalue().encode("utf-8"))
        else:
            doc.saveas(out)
        return type("Done", (), {"returncode": 0, "stdout": b"", "stderr": b""})()

    monkeypatch.setattr("exchange.dwg_batch_converter.shutil.which", lambda name: "/usr/bin/dwg2dxf")
    monkeypatch.setattr("exchange.dwg_batch_converter.subprocess.run", fake_run)


def test_merge_keeps_synonym_layers_blocks_with_content_and_assigned_layers(monkeypatch, tmp_path):
    def build(doc):
        msp = doc.modelspace()
        msp.add_line((0, 0), (1, 0), dxfattribs={"layer": "Тепловые сети"})
        msp.add_line((0, 0), (1, 0), dxfattribs={"layer": "Слой 7"})
        msp.add_line((0, 0), (1, 0), dxfattribs={"layer": "Рельеф"})
        plan = doc.blocks.new("ПЛАН")
        plan.add_lwpolyline([(0, 0), (10, 0), (10, 10)], close=True, dxfattribs={"layer": "Здания"})
        msp.add_blockref("ПЛАН", (0, 0), dxfattribs={"layer": "0"})

    _fake_dwg2dxf_writing(monkeypatch, build)
    dwg = tmp_path / "a.dwg"
    dwg.write_bytes(b"AC1032 x")
    # Имена слоёв DXF R2000 с кириллицей ezdxf пишет как \U+XXXX и так их
    # и читает -- как и в чертежах заказчиков; layer_map задан по такому
    # имени, как оно приходит в сводке слоёв.
    layer_7 = "\\U+0421\\U+043b\\U+043e\\U+0439 7"
    result = merge_dwg_files([dwg], tmp_path, layer_map={layer_7: "path"})
    msp = result.doc.modelspace()
    assert sorted(repair_mojibake(e.dxf.layer) for e in msp.query("LINE")) == ["Слой 7", "Тепловые сети"]
    assert [repair_mojibake(e.dxf.name) for e in msp.query("INSERT")] == ["ПЛАН"]
    # Отброшенный слой виден в сводке -- его можно назначить.
    assert {repair_mojibake(name): n for name, n in result.layer_counts.items()} == {"Тепловые сети": 1, "Слой 7": 1, "Рельеф": 1, "0": 1}


def test_merge_reads_names_written_in_utf8_under_a_cp1252_header(monkeypatch, tmp_path):
    def build(doc):
        doc.modelspace().add_lwpolyline([(0, 0), (10, 0), (10, 10)], close=True, dxfattribs={"layer": "Здания"})

    _fake_dwg2dxf_writing(monkeypatch, build, encode_utf8_under_cp1252=True)
    dwg = tmp_path / "a.dwg"
    dwg.write_bytes(b"AC1032 x")
    result = merge_dwg_files([dwg], tmp_path)
    assert [e.dxf.layer for e in result.doc.modelspace()] == ["Здания"]


# --- комплект из DXF и внешние ссылки -------------------------------------------


def test_dxf_files_are_merged_without_dwg2dxf(monkeypatch, tmp_path):
    # Тайлы подосновы в DXF (tp, up, brd): конвертер не нужен вовсе.
    monkeypatch.setattr("exchange.dwg_batch_converter.shutil.which", lambda name: None)
    tp, up = tmp_path / "tp.dxf", tmp_path / "up.dxf"
    _write_dxf_with_line(tp, "PATH_tp")
    _write_dxf_with_line(up, "Газопровод")
    result = merge_dwg_files([tp, up], tmp_path)
    assert result.converted == ["tp.dxf", "up.dxf"]
    assert sorted(e.dxf.layer for e in result.doc.modelspace().query("LINE")) == ["PATH_tp", "Газопровод"]
    assert tp.exists() and up.exists()  # исходники не трогаются


def _write_base_with_xref(path: Path, xref_file: str, insert, rotation=0.0) -> None:
    doc = ezdxf.new()
    name = PureWindowsPath(xref_file).stem
    doc.add_xref_def(xref_file, name)
    doc.modelspace().add_blockref(name, insert, dxfattribs={"rotation": rotation})
    doc.layers.add("Граница работ")
    doc.modelspace().add_lwpolyline([(0, 0), (10, 0), (10, 10)], close=True, dxfattribs={"layer": "Граница работ"})
    doc.saveas(path)


def test_file_referenced_as_xref_with_offset_is_placed_like_the_insert(tmp_path):
    # Генплан вставляет сети внешней ссылкой со сдвигом (1000, 2000) и
    # поворотом 90°: файл сетей ложится туда же, а не в начало координат.
    base, up = tmp_path / "Генплан.dxf", tmp_path / "up.dxf"
    _write_base_with_xref(base, r"C:\Проект\Подоснова\UP.dwg", (1000, 2000), rotation=90)
    _write_dxf_with_line(up, "Газопровод")  # линия (0,0)-(1,1)
    result = merge_dwg_files([base, up], tmp_path)
    line = next(iter(result.doc.modelspace().query('LINE[layer=="Газопровод"]')))
    assert (round(line.dxf.start.x, 6), round(line.dxf.start.y, 6)) == (1000, 2000)
    assert (round(line.dxf.end.x, 6), round(line.dxf.end.y, 6)) == (999, 2001)
    assert result.placed_by_xref == {"up.dxf": "Генплан.dxf"}


def test_xref_inserted_at_origin_leaves_the_file_as_is(tmp_path):
    base, up = tmp_path / "base.dxf", tmp_path / "up.dxf"
    _write_base_with_xref(base, "up.dwg", (0, 0))
    _write_dxf_with_line(up, "Газопровод")
    result = merge_dwg_files([base, up], tmp_path)
    line = next(iter(result.doc.modelspace().query('LINE[layer=="Газопровод"]')))
    assert (line.dxf.start.x, line.dxf.start.y) == (0, 0)
    assert result.placed_by_xref == {}


# --- сверка числа объектов DWG и DXF (issue #117) -------------------------------


def test_dwg_entities_are_counted_from_dwgread_json_lines():
    lines = [
        b'      "entity": "BLOCK",\n', b'      "entity": "LINE",\n', b'      "entity": "POLYLINE_2D",\n',
        b'      "entity": "VERTEX_2D",\n', b'      "entity": "SEQEND",\n', b'      "entity": "DIMENSION_LINEAR",\n',
        b'      "entity": "_3DFACE",\n', b'      "entity": "ENDBLK",\n', b'      "object": "LAYER",\n',
    ]
    assert dwg_batch_converter.count_dwg_entities(lines) == Counter({"LINE": 1, "POLYLINE": 1, "DIMENSION": 1, "3DFACE": 1})


def test_lost_entities_are_the_ones_missing_in_dxf():
    dwg = Counter({"LINE": 10, "PROXY_ENTITY": 2, "INSERT": 3})
    dxf = Counter({"LINE": 10, "INSERT": 1})
    assert dwg_batch_converter.lost_entities(dwg, dxf) == {"INSERT": 2, "PROXY_ENTITY": 2}


def test_file_without_dwg_signature_is_not_converted(fake_dwg2dxf, tmp_path):
    pax = tmp_path / "PaxHeader.dwg"
    pax.write_bytes("path=Объект/tp.dwg\n".encode())
    good = tmp_path / "tp.dwg"
    good.write_bytes(b"AC1032 good tp")
    result = merge_dwg_files([pax, good], tmp_path)
    assert result.converted == ["tp.dwg"]
    assert "AC10" in result.failed["PaxHeader.dwg"]


def test_conversion_losses_are_reported(fake_dwg2dxf, tmp_path, monkeypatch):
    # dwgread видит в DWG прокси-объект, которого dwg2dxf в DXF не записал.
    monkeypatch.setattr(dwg_batch_converter, "dwg_entities", lambda path: Counter({"LINE": 1, "PROXY_ENTITY": 3}))
    dwg = tmp_path / "a.dwg"
    dwg.write_bytes(b"AC1032 good a")
    result = merge_dwg_files([dwg], tmp_path)
    assert result.lost == {"a.dwg": {"PROXY_ENTITY": 3}}


def test_no_dwgread_means_no_check(monkeypatch, tmp_path):
    monkeypatch.setattr(dwg_batch_converter.shutil, "which", lambda name: None)
    assert dwg_batch_converter.dwg_entities(tmp_path / "a.dwg") is None


@pytest.mark.skipif(not (shutil.which("dwgread") and shutil.which("dxf2dwg") and shutil.which("dwg2dxf")), reason="нужен LibreDWG")
def test_real_dwg_converts_without_losses(tmp_path):
    src = tmp_path / "site.dxf"
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    doc.layers.add("Газопровод")
    msp.add_line((0, 0), (10, 0), dxfattribs={"layer": "Газопровод"})
    msp.add_lwpolyline([(0, 0), (5, 0), (5, 5)], dxfattribs={"layer": "Газопровод"})
    msp.add_circle((3, 3), 1.0, dxfattribs={"layer": "Газопровод"})
    doc.saveas(src)
    dwg = tmp_path / "site.dwg"
    subprocess.run(["dxf2dwg", "-y", "-o", str(dwg), str(src)], capture_output=True, check=False)
    if not dwg.exists():
        pytest.skip("dxf2dwg не собрал DWG")
    counts = dwg_batch_converter.dwg_entities(dwg)
    assert counts and counts["LINE"] == 1
    work = tmp_path / "work"
    work.mkdir()
    result = merge_dwg_files([dwg], work)
    if not result.converted:
        pytest.skip(f"LibreDWG не прочитал DWG, который сам записал: {result.failed}")
    assert result.lost == {}


def test_inserts_of_missing_blocks_are_dropped_and_the_file_still_merges(tmp_path):
    # LibreDWG теряет часть определений блоков («Шаблоны значков»: «Вегейла»,
    # «Тсуга»), ссылки остаются -- файл не должен отклоняться целиком.
    from ezdxf.addons.importer import Importer

    from exchange.dwg_batch_converter import _import_all_layouts, drop_dangling_inserts

    doc = ezdxf.new()
    doc.blocks.new("Сирень").add_circle((0, 0), 1)
    doc.blocks.new("Вегейла").add_circle((0, 0), 1)
    nested = doc.blocks.new("Группа")
    nested.add_blockref("Вегейла", (0, 0))
    msp = doc.modelspace()
    msp.add_blockref("Сирень", (1, 1), dxfattribs={"layer": "Кустарники"})
    msp.add_blockref("Вегейла", (2, 2), dxfattribs={"layer": "Кустарники"})
    msp.add_blockref("Группа", (3, 3), dxfattribs={"layer": "Кустарники"})
    doc.blocks.delete_block("Вегейла", safe=False)
    with pytest.raises(ValueError, match="not found"):
        importer = Importer(doc, ezdxf.new())
        importer.import_entities(list(msp))
        importer.finalize()

    assert drop_dangling_inserts(doc) == 2  # в модели и внутри блока «Группа»
    target = ezdxf.new()
    _import_all_layouts(doc, target)
    assert sorted(e.dxf.name for e in target.modelspace().query("INSERT")) == ["Группа", "Сирень"]


def test_conversion_error_hides_server_paths_and_libredwg_noise():
    from exchange.dwg_batch_converter import conversion_error_text

    raw = "ERROR: Invalid or unimplemented DWG version code AC1009|\nERROR: Failed to decode file: /var/folders/hb/T/tmp1/old.dwg 0x800\nREAD ERROR 0x800"
    text = conversion_error_text(raw)
    assert "AC1009" in text and "пересохраните" in text and "/var/" not in text and "0x800" not in text
    assert "/tmp/" not in conversion_error_text("Failed to decode file: /tmp/abc/x.dwg")
