"""exchange/drawing_package.py -- распаковка комплекта чертежей и выбор файлов."""

import io
import zipfile
from pathlib import Path

import pytest

from exchange import drawing_package
from exchange.drawing_package import PackageError, extract_drawings, select_drawings


class _Cp866Info(zipfile.ZipInfo):
    """Так пишет имена архиватор русской Windows: cp866 без флага UTF-8."""

    def _encodeFilenameFlags(self):  # noqa: N802 -- имя метода zipfile
        return self.filename.encode("cp866"), self.flag_bits & ~0x800


class _Utf8NoFlagInfo(zipfile.ZipInfo):
    """Так пишет имена zip на macOS и Linux: UTF-8, но без флага UTF-8."""

    def _encodeFilenameFlags(self):  # noqa: N802 -- имя метода zipfile
        return self.filename.encode("utf-8"), self.flag_bits & ~0x800


def _zip(path: Path, members: dict[str, bytes], cp866: bool = False, utf8_no_flag: bool = False) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            info = _Cp866Info(name) if cp866 else _Utf8NoFlagInfo(name) if utf8_no_flag else zipfile.ZipInfo(name)
            zf.writestr(info, data)
    return path


def test_drawings_are_extracted_flat_and_the_rest_is_ignored(tmp_path):
    archive = _zip(tmp_path / "объект.zip", {
        "Объект/Подоснова/tp.dwg": b"tp",
        "Объект/Подоснова/up.DWG": b"up",
        "Объект/Генплан.dxf": b"gp",
        "Объект/Ведомость.xlsx": b"x",
        "Объект/фото.jpg": b"x",
    })
    out = tmp_path / "out"
    out.mkdir()
    names = sorted(p.name for p in extract_drawings(archive, out))
    assert names == ["tp.dwg", "up.DWG", "Генплан.dxf"]
    assert sorted(p.name for p in out.iterdir()) == names


def test_service_files_of_archivers_are_skipped(tmp_path):
    archive = _zip(tmp_path / "a.zip", {
        "PaxHeader/tp.dwg": b"x",
        "__MACOSX/Объект/._tp.dwg": b"x",
        "Объект/._up.dwg": b"x",
        "Объект/tp.dwg": b"tp",
    })
    out = tmp_path / "out"
    out.mkdir()
    assert [p.name for p in extract_drawings(archive, out)] == ["tp.dwg"]


def test_cp866_names_from_russian_windows_are_decoded(tmp_path):
    archive = _zip(tmp_path / "a.zip", {"Подоснова/Генплан.dwg": b"gp"}, cp866=True)
    out = tmp_path / "out"
    out.mkdir()
    assert [p.name for p in extract_drawings(archive, out)] == ["Генплан.dwg"]


def test_utf8_names_without_the_flag_from_macos_zip_are_decoded(tmp_path):
    archive = _zip(tmp_path / "a.zip", {"Песчаный/output_1-5__3_ДЖКХ-25_00752tp.dwg": b"tp"}, utf8_no_flag=True)
    out = tmp_path / "out"
    out.mkdir()
    assert [p.name for p in extract_drawings(archive, out)] == ["output_1-5__3_ДЖКХ-25_00752tp.dwg"]


def test_paths_from_the_archive_never_leave_the_target_folder(tmp_path):
    archive = _zip(tmp_path / "a.zip", {"../../evil.dwg": b"x", "/abs/evil2.dxf": b"y"})
    out = tmp_path / "out"
    out.mkdir()
    found = extract_drawings(archive, out)
    assert sorted(p.name for p in found) == ["evil.dwg", "evil2.dxf"]
    assert all(p.parent == out for p in found)
    assert not (tmp_path.parent / "evil.dwg").exists()


def test_same_names_in_different_folders_do_not_overwrite_each_other(tmp_path):
    archive = _zip(tmp_path / "a.zip", {"1/tp.dwg": b"one", "2/tp.dwg": b"two"})
    out = tmp_path / "out"
    out.mkdir()
    found = extract_drawings(archive, out)
    assert sorted(p.read_bytes() for p in found) == [b"one", b"two"]


def test_nested_archive_is_extracted(tmp_path):
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as zf:
        zf.writestr("up.dwg", b"up")
    archive = _zip(tmp_path / "a.zip", {"tp.dwg": b"tp", "сети.zip": inner.getvalue()})
    out = tmp_path / "out"
    out.mkdir()
    assert sorted(p.name for p in extract_drawings(archive, out)) == ["tp.dwg", "up.dwg"]


def test_archive_bomb_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(drawing_package, "MAX_UNPACKED_BYTES", 10)
    archive = _zip(tmp_path / "a.zip", {"tp.dwg": b"x" * 100})
    out = tmp_path / "out"
    out.mkdir()
    with pytest.raises(PackageError, match="после распаковки"):
        extract_drawings(archive, out)
    assert list(out.iterdir()) == []


def test_broken_archive_is_a_clear_error(tmp_path):
    archive = tmp_path / "a.zip"
    archive.write_bytes(b"not a zip")
    with pytest.raises(PackageError, match="повреждён"):
        extract_drawings(archive, tmp_path)


def test_with_dwg_in_the_package_loose_dxf_copies_are_skipped(tmp_path):
    files = [tmp_path / n for n in ("tp.dwg", "up.dwg", "tp.dxf", "readme.txt")]
    selection = select_drawings(files)
    assert [p.name for p in selection.drawings] == ["tp.dwg", "up.dwg"]
    assert list(selection.skipped) == ["tp.dxf"]


def test_dxf_only_package_is_taken_whole(tmp_path):
    files = [tmp_path / n for n in ("tp.dxf", "up.dxf", "brd.dxf")]
    selection = select_drawings(files)
    assert [p.name for p in selection.drawings] == ["tp.dxf", "up.dxf", "brd.dxf"]
    assert selection.skipped == {}
