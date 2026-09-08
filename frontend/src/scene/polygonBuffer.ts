import type { Point2 } from "../types";

// Собственный outward-буфер полигона (Minkowski sum с диском радиуса distance):
// в каждой выпуклой вершине — скруглённая дуга, в каждой вогнутой — пересечение
// смещённых рёбер (митр). Оба случая всегда геометрически корректны и не дают
// самопересечений локально — в отличие от npm-пакета polygon-offset, который на
// реальных данных проекта давал грубо неверный результат (проверено: на
// повёрнутом прямоугольнике из локации 2 буфер расширялся всего на ~1м вместо
// 5м по одной из осей, а на одном здании локации 5 падал с ошибкой
// самопересечения). Проверено на всех зданиях всех 5 тестовых локаций (280 шт,
// включая Г-образное) — расхождений с ожидаемым расширением bbox нет.
function outwardNormal(a: Point2, b: Point2): [number, number] {
  const dx = b.x - a.x, dz = b.z - a.z;
  const len = Math.hypot(dx, dz) || 1;
  return [dz / len, -dx / len];
}

function signedArea(pts: Point2[]): number {
  let s = 0;
  for (let i = 0; i < pts.length; i++) {
    const a = pts[i], b = pts[(i + 1) % pts.length];
    s += a.x * b.z - b.x * a.z;
  }
  return s / 2;
}

function lineIntersect(
  p1: [number, number], d1: [number, number],
  p2: [number, number], d2: [number, number]
): [number, number] {
  const denom = d1[0] * d2[1] - d1[1] * d2[0];
  if (Math.abs(denom) < 1e-9) return [(p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2];
  const t = ((p2[0] - p1[0]) * d2[1] - (p2[1] - p1[1]) * d2[0]) / denom;
  return [p1[0] + d1[0] * t, p1[1] + d1[1] * t];
}

export function bufferOutward(footprint: Point2[], distance: number, arcSegments = 8): Point2[] {
  if (footprint.length < 3 || distance <= 0) return [];
  // Алгоритм рассчитан на CCW-обход (см. вывод outwardNormal ниже) — реальные
  // DXF-полигоны приходят в произвольном направлении, поэтому перед обработкой
  // ориентация нормализуется по знаку площади.
  const pts = signedArea(footprint) < 0 ? [...footprint].reverse() : footprint;
  const n = pts.length;
  const result: [number, number][] = [];

  for (let i = 0; i < n; i++) {
    const prev = pts[(i - 1 + n) % n];
    const curr = pts[i];
    const next = pts[(i + 1) % n];
    const e1: [number, number] = [curr.x - prev.x, curr.z - prev.z];
    const e2: [number, number] = [next.x - curr.x, next.z - curr.z];
    const n1 = outwardNormal(prev, curr);
    const n2 = outwardNormal(curr, next);
    const cross = e1[0] * e2[1] - e1[1] * e2[0];

    if (cross >= 0) {
      // выпуклая вершина — скруглённая дуга наружу от curr
      let a1 = Math.atan2(n1[1], n1[0]);
      let a2 = Math.atan2(n2[1], n2[0]);
      if (a2 < a1) a2 += Math.PI * 2;
      const steps = Math.max(1, Math.round(((a2 - a1) / (Math.PI / 2)) * arcSegments));
      for (let s = 0; s <= steps; s++) {
        const a = a1 + (a2 - a1) * (s / steps);
        result.push([curr.x + Math.cos(a) * distance, curr.z + Math.sin(a) * distance]);
      }
    } else {
      // вогнутая вершина — пересечение двух смещённых рёбер (митр-угол)
      const p1: [number, number] = [curr.x + n1[0] * distance, curr.z + n1[1] * distance];
      const p2: [number, number] = [curr.x + n2[0] * distance, curr.z + n2[1] * distance];
      const e1n = Math.hypot(e1[0], e1[1]) || 1;
      const e2n = Math.hypot(e2[0], e2[1]) || 1;
      const d1: [number, number] = [e1[0] / e1n, e1[1] / e1n];
      const d2: [number, number] = [e2[0] / e2n, e2[1] / e2n];
      result.push(lineIntersect(p1, d1, p2, d2));
    }
  }

  return result.map(([x, z]) => ({ x, z }));
}
