import * as THREE from "three";
import type { SceneGeo } from "../types";

// Подложка карты под сценой (issue #115): тайлы растровой карты (по
// умолчанию OpenStreetMap, VITE_BASEMAP_URL -- свой сервер тайлов в закрытой
// сети) кладутся в координаты сцены по meta.geo -- аффинному преобразованию
// «долгота, широта -> метры сцены», которое бэкенд строит для чертежей в
// Московской системе координат (backend/core/geo.py).
export const BASEMAP_URL =
  (import.meta.env.VITE_BASEMAP_URL as string | undefined) ?? "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const MARGIN_M = 150; // карта вокруг участка
const MAX_TILES = 80;

export interface Bounds {
  minX: number;
  maxX: number;
  minZ: number;
  maxZ: number;
}

// x = a*lon + b*lat + c, z = d*lon + e*lat + f -- и обратно.
function toScene(geo: SceneGeo, lon: number, lat: number): [number, number] {
  const [a, b, c, d, e, f] = geo.toScene;
  return [a * lon + b * lat + c, d * lon + e * lat + f];
}

function toLonLat(geo: SceneGeo, x: number, z: number): [number, number] {
  const [a, b, c, d, e, f] = geo.toScene;
  const det = a * e - b * d;
  const dx = x - c;
  const dz = z - f;
  return [(e * dx - b * dz) / det, (a * dz - d * dx) / det];
}

const tileX = (lon: number, zoom: number) => Math.floor(((lon + 180) / 360) * 2 ** zoom);
const tileY = (lat: number, zoom: number) => {
  const r = (lat * Math.PI) / 180;
  return Math.floor(((1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2) * 2 ** zoom);
};
const tileLon = (x: number, zoom: number) => (x / 2 ** zoom) * 360 - 180;
const tileLat = (y: number, zoom: number) => (Math.atan(Math.sinh(Math.PI * (1 - (2 * y) / 2 ** zoom))) * 180) / Math.PI;

export interface Tile {
  key: string;
  url: string;
  geometry: THREE.BufferGeometry;
}

// Тайлы, покрывающие участок с запасом, -- на самом крупном масштабе, при
// котором их не больше MAX_TILES.
export function basemapTiles(geo: SceneGeo, bounds: Bounds, url = BASEMAP_URL): Tile[] {
  const corners = [
    toLonLat(geo, bounds.minX - MARGIN_M, bounds.minZ - MARGIN_M),
    toLonLat(geo, bounds.maxX + MARGIN_M, bounds.minZ - MARGIN_M),
    toLonLat(geo, bounds.minX - MARGIN_M, bounds.maxZ + MARGIN_M),
    toLonLat(geo, bounds.maxX + MARGIN_M, bounds.maxZ + MARGIN_M),
  ];
  const lons = corners.map((c) => c[0]);
  const lats = corners.map((c) => c[1]);
  for (let zoom = 19; zoom >= 12; zoom--) {
    const x0 = tileX(Math.min(...lons), zoom);
    const x1 = tileX(Math.max(...lons), zoom);
    const y0 = tileY(Math.max(...lats), zoom);
    const y1 = tileY(Math.min(...lats), zoom);
    if ((x1 - x0 + 1) * (y1 - y0 + 1) > MAX_TILES) continue;
    const tiles: Tile[] = [];
    for (let x = x0; x <= x1; x++) {
      for (let y = y0; y <= y1; y++) {
        // Углы тайла: СЗ, СВ, ЮВ, ЮЗ -- в координаты сцены.
        const nw = toScene(geo, tileLon(x, zoom), tileLat(y, zoom));
        const ne = toScene(geo, tileLon(x + 1, zoom), tileLat(y, zoom));
        const se = toScene(geo, tileLon(x + 1, zoom), tileLat(y + 1, zoom));
        const sw = toScene(geo, tileLon(x, zoom), tileLat(y + 1, zoom));
        const geometry = new THREE.BufferGeometry();
        geometry.setAttribute(
          "position",
          new THREE.Float32BufferAttribute([nw, ne, se, sw].flatMap(([px, pz]) => [px, 0, pz]), 3),
        );
        geometry.setAttribute("uv", new THREE.Float32BufferAttribute([0, 1, 1, 1, 1, 0, 0, 0], 2));
        geometry.setIndex([0, 2, 1, 0, 3, 2]);
        geometry.computeVertexNormals();
        tiles.push({
          key: `${zoom}/${x}/${y}`,
          url: url.replace("{z}", String(zoom)).replace("{x}", String(x)).replace("{y}", String(y)),
          geometry,
        });
      }
    }
    return tiles;
  }
  return [];
}
