"""
Независимая проверка выгрузки: выходной DXF читается заново, как его прочтёт
CAD, и проверяется без опоры на сцену редактора в памяти.

1. Исходник цел. Отпечаток каждой сущности исходного чертежа (её DXF-теги
   без handle и владельца) сравнивается с отпечатком сущности с тем же
   handle в выходном файле: пропавших и изменённых должно быть 0. Так
   проверяется требование ТЗ «исходный чертёж не изменяется».
2. Нормы. Выходной файл разбирается тем же парсером, что при загрузке:
   ограничения -- из файла. Новые посадки находятся в файле по XDATA с id
   (exchange/dxf_overlay.py), их места -- тоже из файла; из сцены берутся
   только вид, пометка корнезащиты и класс напряжения ЛЭП из параметров.
   Отступы считаются заново (greenplan/violation_report.find_violations).
3. Места. Посадка в файле стоит там же, где в сцене (до сантиметра), и ни
   одна новая посадка не потерялась.

Отчёт несёт SHA-256 исходника и результата и версии правил (core/versions.py).
"""

from __future__ import annotations

import hashlib
import io
import math
from typing import Optional

import ezdxf
from ezdxf.lldxf.tagwriter import TagCollector
from pydantic import BaseModel, Field

from core.schemas import Scene, SceneObject
from core.versions import sha256_of, versions
from exchange.dxf_overlay import APPID
from exchange.dxf_parser import parse_dxf_doc
from exchange.export_dxf import is_source_object
from greenplan.violation_report import Violation, find_violations

# Теги, которые меняются при любой записи и не говорят о содержимом:
# handle, владелец, реакторы.
_VOLATILE_CODES = {5, 330, 102}
_PLANTS = ("tree", "bush")
POSITION_TOLERANCE_M = 0.01


class ExportVerification(BaseModel):
    """Результат проверки выгрузки."""

    ok: bool = Field(description="Исходник цел, посадки на местах, нарушений норм у новых посадок нет.")
    mode: str = Field(description="overlay -- поверх исходника, rebuilt -- собран из сцены.")
    source_sha256: Optional[str] = Field(default=None, description="SHA-256 исходного чертежа (комплекта файлов).")
    output_sha256: str = Field(description="SHA-256 выходного DXF.")
    versions: dict[str, str] = Field(description="Версии приложения, правил разбора, норм и корпуса прецедентов.")
    source_entities_checked: int = 0
    source_entities_missing: int = 0
    source_entities_changed: int = 0
    source_check: str = Field(description="Что проверено про исходник и почему, если не проверено.")
    plants_checked: int = 0
    plants_missing: list[str] = Field(default=[], description="Новые посадки сцены, которых нет в файле.")
    plants_moved: list[str] = Field(default=[], description="Новые посадки, стоящие в файле не там, где в сцене.")
    violations: list[Violation] = Field(default=[], description="Нарушения норм у новых посадок по геометрии из файла.")


def entity_fingerprints(doc) -> dict[str, str]:
    """{handle: отпечаток} всех графических сущностей модели и листов."""
    found = {}
    for layout in doc.layouts:
        for entity in layout:
            collector = TagCollector(dxfversion=doc.dxfversion)
            try:
                entity.export_dxf(collector)
            except Exception:  # noqa: BLE001 -- сущность, которую ezdxf не пишет, сравнивается по типу и слою
                found[entity.dxf.handle] = f"{entity.dxftype()}:{entity.dxf.get('layer', '')}"
                continue
            tags = [(tag.code, str(tag.value)) for tag in collector.tags if tag.code not in _VOLATILE_CODES]
            found[entity.dxf.handle] = hashlib.sha256(repr(tags).encode("utf-8")).hexdigest()
    return found


def _tagged_points(doc) -> dict[str, tuple[float, float]]:
    """{id объекта: (X, Y) в координатах чертежа} -- точки результата с XDATA."""
    found = {}
    for point in doc.modelspace().query("POINT"):
        if not point.has_xdata(APPID):
            continue
        tags = point.get_xdata(APPID)
        if tags:
            found.setdefault(str(tags[0].value), (point.dxf.location.x, point.dxf.location.y))
    return found


def verify_export(
    scene: Scene,
    output: bytes,
    mode: str,
    source_doc=None,
    source_sha256: Optional[str] = None,
    source_note: Optional[str] = None,
) -> ExportVerification:
    """Проверить выходной DXF output. source_doc -- исходный чертёж (как его
    прочитал экспорт), None -- исходник не сравнивается (source_note -- почему)."""
    doc = ezdxf.read(io.StringIO(output.decode("utf-8")))

    checked = missing = changed = 0
    if source_doc is not None:
        before, after = entity_fingerprints(source_doc), entity_fingerprints(doc)
        checked = len(before)
        missing = sum(1 for handle in before if handle not in after)
        changed = sum(1 for handle, fp in before.items() if handle in after and after[handle] != fp)
        note = f"проверено сущностей исходника: {checked}; пропало {missing}, изменено {changed}"
    else:
        note = source_note or "исходника на сервере нет — DXF собран из сцены"

    # Нормы -- по геометрии файла: ограничения разбираются из него же.
    layer_kwargs = {"layer_map": scene.meta.layerMap} if scene.meta.layerMap else {}
    parsed = Scene.model_validate(parse_dxf_doc(doc, **layer_kwargs))
    voltage = {zone.id: zone.voltageKv for zone in scene.restrictions if zone.voltageKv is not None}
    restrictions = [
        zone.model_copy(update={"voltageKv": voltage[zone.id]}) if zone.voltageKv is None and zone.id in voltage else zone
        for zone in parsed.restrictions
    ]
    scale = parsed.meta.scale or 1.0
    ox, oy = float(parsed.meta.origin.get("x", 0.0)), float(parsed.meta.origin.get("y", 0.0))
    sx, sy = float(scene.meta.origin.get("x", ox)), float(scene.meta.origin.get("y", oy))
    in_file = _tagged_points(doc)

    plants: list[SceneObject] = []
    plants_missing, plants_moved = [], []
    for obj in scene.objects:
        if obj.type not in _PLANTS or is_source_object(obj):
            continue
        where = in_file.get(obj.id)
        if where is None:
            plants_missing.append(obj.id)
            continue
        # Место в сцене -- в координатах чертежа: поверх исходника -- по
        # преобразованию сцены, собранный из сцены DXF -- в её метрах.
        scene_scale = (scene.meta.scale or 1.0) if mode == "overlay" else 1.0
        expected = (obj.position.x / scene_scale + sx, obj.position.z / scene_scale + sy) if mode == "overlay" else (obj.position.x, obj.position.z)
        if math.dist(expected, where) * scene_scale > POSITION_TOLERANCE_M:
            plants_moved.append(obj.id)
        # Для норм -- в системе координат разбора выходного файла.
        x, z = (where[0] - ox) * scale, (where[1] - oy) * scale
        plants.append(obj.model_copy(update={"position": obj.position.model_copy(update={"x": x, "z": z})}))

    check_scene = parsed.model_copy(update={"restrictions": restrictions, "objects": plants})
    violations = find_violations(check_scene)
    ok = missing == 0 and changed == 0 and not plants_missing and not plants_moved and not violations
    return ExportVerification(
        ok=ok,
        mode=mode,
        source_sha256=source_sha256,
        output_sha256=sha256_of(output),
        versions=versions(),
        source_entities_checked=checked,
        source_entities_missing=missing,
        source_entities_changed=changed,
        source_check=note,
        plants_checked=len(plants),
        plants_missing=plants_missing,
        plants_moved=plants_moved,
        violations=violations,
    )
