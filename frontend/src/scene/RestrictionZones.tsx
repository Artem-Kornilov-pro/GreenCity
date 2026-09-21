import { useEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import type { RestrictionZone } from "../types";
import { flatPolygonGeometry } from "./geometryHelpers";

const COLOR_BY_SEVERITY: Record<string, string> = {
  forbidden: "#e0433b",
  warning: "#e8a83c",
  allowed: "#3fae5a",
};

// Выше этого числа зон в одной severity-группе контур не рисуем вовсе (см.
// докстринг у <lineSegments> ниже) -- подобрано с запасом от рукописных
// тестовых участков (locations/location_old/, единицы-десятки зон на
// severity, контур там нужен и остаётся) и с запасом ДО реальных
// конвертированных (locations/, сотни зон на severity после слияния по
// слою), где контур уже превращается в штриховку.
const DENSE_GROUP_OUTLINE_CUTOFF = 30;

// Смещение "к камере" через polygonOffset (а не через накопление крошечного Y
// на каждую зону по индексу) — устойчиво и к 2000+ зонам локации 5, и к любому
// масштабу сцены: чем важнее severity, тем меньше offset (=ближе к камере),
// поэтому forbidden всегда читается поверх warning/allowed, а не тонет в них.
const OFFSET_BY_SEVERITY: Record<string, number> = {
  forbidden: -3,
  warning: -2,
  allowed: -1,
};

// polygonOffset решает z-fighting на общей глубине, но порядок альфа-блендинга
// прозрачных объектов Three.js сортирует отдельно (по renderOrder, при равенстве
// — по дистанции до камеры) — без явного renderOrder две наложенные зоны могли
// в зависимости от угла обзора менять, какая перекрывает какую. Задаём его
// явно тем же приоритетом severity, что и offset выше.
const RENDER_ORDER_BY_SEVERITY: Record<string, number> = {
  allowed: 0,
  warning: 1,
  forbidden: 2,
};

// Реальная (не только полигон-офсетная) высота по severity — без неё все зоны
// лежат ровно на Y=0.01, и raycasting (наведение мышью) не может определить,
// какая из наложенных зон "сверху": polygonOffset — чисто растровый трюк для
// GPU, на пересечение луча с геометрией он не влияет. Из-за этого при наведении
// почти всегда "выигрывала" крупная фоновая зона газона (allowed), а не более
// специфичная forbidden/warning-зона поверх неё. Три уровня высоты достаточно —
// forbidden физически выше warning выше allowed, поэтому луч сначала попадает
// в самую важную зону.
const Y_BY_SEVERITY: Record<string, number> = {
  allowed: 0.01,
  warning: 0.02,
  forbidden: 0.03,
};

const SEVERITIES = ["allowed", "warning", "forbidden"] as const;

interface MergedGroup {
  severity: string;
  fill: THREE.BufferGeometry;
  outline: THREE.BufferGeometry;
  // Треугольники в слитой геометрии идут подряд по зонам, поэтому для
  // обратного поиска "какая зона под курсором" хватает границ диапазонов и
  // двоичного поиска по faceIndex из события raycast.
  zoneEnds: number[];
  zones: RestrictionZone[];
}

// Раньше каждая зона была отдельным <mesh> плюс <Line> из drei -- на реальном
// файле из 20 улиц это около пяти тысяч объектов three.js, каждый со своим
// материалом и своими обработчиками указателя (а значит, и участием в
// raycast'е на каждое движение мыши). Сливаем зоны одной severity в одну
// геометрию: три меша и три набора линий на всю сцену, по одному материалу на
// каждый, независимо от числа зон.
function buildGroups(zones: RestrictionZone[]): MergedGroup[] {
  const groups: MergedGroup[] = [];

  for (const severity of SEVERITIES) {
    const list = zones.filter((z) => z.severity === severity && z.polygon.length >= 3);
    if (!list.length) continue;

    const fillPositions: number[] = [];
    const outlinePositions: number[] = [];
    const zoneEnds: number[] = [];
    const kept: RestrictionZone[] = [];

    for (const zone of list) {
      const geo = flatPolygonGeometry(zone.polygon);
      if (!geo) continue;
      // ShapeGeometry отдаёт ИНДЕКСИРОВАННУЮ геометрию: в position лежат только
      // уникальные вершины, а треугольники задаёт index. Копировать одни
      // позиции подряд нельзя -- получится треугольник из каждой случайной
      // тройки соседних вершин вместо настоящей триангуляции (зоны выглядят
      // перекрученными). toNonIndexed раскладывает вершины в порядке
      // треугольников, после чего их можно просто дописывать в общий буфер.
      const flat = geo.index ? geo.toNonIndexed() : geo;
      const pos = flat.getAttribute("position");
      for (let i = 0; i < pos.count; i++) {
        fillPositions.push(pos.getX(i), pos.getY(i), pos.getZ(i));
      }
      // Временные геометрии нужны только как триангулятор -- держать их в
      // памяти после копирования вершин незачем.
      if (flat !== geo) flat.dispose();
      geo.dispose();

      const poly = zone.polygon;
      for (let i = 0; i < poly.length; i++) {
        const a = poly[i];
        const b = poly[(i + 1) % poly.length];
        outlinePositions.push(a.x, 0.005, a.z, b.x, 0.005, b.z);
      }

      zoneEnds.push(fillPositions.length / 9);
      kept.push(zone);
    }

    if (!kept.length) continue;

    const fill = new THREE.BufferGeometry();
    fill.setAttribute("position", new THREE.Float32BufferAttribute(fillPositions, 3));
    const outline = new THREE.BufferGeometry();
    outline.setAttribute("position", new THREE.Float32BufferAttribute(outlinePositions, 3));

    groups.push({ severity, fill, outline, zoneEnds, zones: kept });
  }

  return groups;
}

function zoneAtFace(group: MergedGroup, faceIndex: number | null | undefined): RestrictionZone | null {
  if (faceIndex === undefined || faceIndex === null) return null;
  let lo = 0;
  let hi = group.zoneEnds.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (faceIndex < group.zoneEnds[mid]) hi = mid;
    else lo = mid + 1;
  }
  return group.zones[lo] ?? null;
}

export function RestrictionZones({
  zones,
  onHover,
}: {
  zones: RestrictionZone[];
  onHover: (zone: RestrictionZone | null) => void;
}) {
  const groups = useMemo(() => buildGroups(zones), [zones]);

  // onPointerMove у WebGL-канваса стреляет на КАЖДЫЙ пиксель движения мыши --
  // без дедупликации наведение внутри одной и той же зоны раз за разом
  // вызывало onHover с новым замыканием, что триггерило полный ре-рендер
  // EditorPage (setHoveredZone) на каждое такое событие. Сравниваем с
  // последней сообщённой зоной по id и молчим, если она не изменилась.
  const lastHoveredIdRef = useRef<string | null>(null);

  // Слитые геометрии живут в памяти GPU, и React сам их не освобождает: без
  // явного dispose каждая загрузка новой сцены оставляла бы предыдущую висеть.
  useEffect(
    () => () => {
      for (const g of groups) {
        g.fill.dispose();
        g.outline.dispose();
      }
    },
    [groups]
  );

  return (
    <>
      {groups.map((group) => {
        const color = COLOR_BY_SEVERITY[group.severity] ?? "#999999";
        const offset = OFFSET_BY_SEVERITY[group.severity] ?? 0;
        const order = RENDER_ORDER_BY_SEVERITY[group.severity] ?? 0;
        const y = Y_BY_SEVERITY[group.severity] ?? 0.01;
        return (
          <group key={group.severity} position={[0, y, 0]}>
            <mesh
              geometry={group.fill}
              renderOrder={order}
              onPointerMove={(e) => {
                e.stopPropagation();
                const zone = zoneAtFace(group, e.faceIndex);
                const id = zone?.id ?? null;
                if (id === lastHoveredIdRef.current) return;
                lastHoveredIdRef.current = id;
                onHover(zone);
              }}
              onPointerOut={(e) => {
                e.stopPropagation();
                if (lastHoveredIdRef.current === null) return;
                lastHoveredIdRef.current = null;
                onHover(null);
              }}
            >
              <meshBasicMaterial
                color={color}
                transparent
                opacity={0.28}
                side={THREE.DoubleSide}
                depthWrite={false}
                polygonOffset
                polygonOffsetFactor={offset}
                polygonOffsetUnits={offset}
              />
            </mesh>
            {/* Обычный LineSegments, а не <Line> из drei: тот ради толщины
                строит Line2 с инстансной геометрией на каждый контур, что на
                тысячах зон и съедало память. Толщина здесь всегда 1px, но
                принадлежность зоны читается по заливке (raycast идёт по
                group.fill выше, у контура нет своего onPointerMove) --
                контур ничего не сообщает, чего не сообщает уже сама заливка.

                Поэтому на плотных группах (настоящие конвертированные
                участки: соседние, но не слившиеся при объединении по слою
                куски газона/дороги -- см. parse_dxf.py::_merge_polygon_zones
                -- сотни зон одного severity) контур не приглушаем, а просто
                не рисуем: даже у почти прозрачной линии сотни наложенных
                друг на друга копий на общих границах суммируются альфа-
                смешиванием в заметную штриховку поверх ровной по смыслу
                площади -- убедились на практике, приглушения до пола 0.06
                оказалось недостаточно. На рукописных тестовых участках
                (единицы зон на severity) контур остаётся читаемым, как и
                был. */}
            {group.zones.length <= DENSE_GROUP_OUTLINE_CUTOFF && (
              <lineSegments geometry={group.outline} renderOrder={order + 10}>
                <lineBasicMaterial color={color} transparent opacity={0.9} />
              </lineSegments>
            )}
          </group>
        );
      })}
    </>
  );
}
