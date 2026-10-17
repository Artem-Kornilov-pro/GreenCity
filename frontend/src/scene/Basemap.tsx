import { useEffect, useMemo, useState } from "react";
import * as THREE from "three";
import type { SceneGeo } from "../types";
import { basemapTiles, type Bounds, type Tile } from "./basemapTiles";
import { FLAT_LAYER_ORDER } from "./geometryHelpers";

// Тайлы подложки карты (basemapTiles.ts) -- текстурами под сценой.
const BASEMAP_Y = -0.14; // под землёй окрестностей (-0.12) и участка (-0.1)

function BasemapTile({ tile }: { tile: Tile }) {
  const [texture, setTexture] = useState<THREE.Texture | null>(null);
  useEffect(() => {
    let alive = true;
    const loader = new THREE.TextureLoader();
    loader.setCrossOrigin("anonymous");
    loader.load(tile.url, (loaded) => {
      loaded.colorSpace = THREE.SRGBColorSpace;
      if (alive) setTexture(loaded);
      else loaded.dispose();
    });
    return () => {
      alive = false;
    };
  }, [tile.url]);
  useEffect(() => () => texture?.dispose(), [texture]);
  useEffect(() => () => tile.geometry.dispose(), [tile.geometry]);
  if (!texture) return null;
  return (
    <mesh geometry={tile.geometry} position={[0, BASEMAP_Y, 0]} renderOrder={FLAT_LAYER_ORDER.basemap}>
      <meshBasicMaterial map={texture} side={THREE.DoubleSide} depthWrite={false} toneMapped={false} />
    </mesh>
  );
}

export function Basemap({ geo, bounds }: { geo: SceneGeo; bounds: Bounds }) {
  const tiles = useMemo(() => basemapTiles(geo, bounds), [geo, bounds]);
  return (
    <>
      {tiles.map((tile) => (
        <BasemapTile key={tile.key} tile={tile} />
      ))}
    </>
  );
}
