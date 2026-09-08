// Нормативные отступы посадок от зданий и инженерных сетей различаются по
// виду посадки (дерево/кустарник) — у дерева корни глубже и шире, крона
// крупнее, поэтому ему требуется больший отступ, чем кустарнику. Источник:
// табличные нормы СНиП 2.07.01-89*/СП 42.13330.2016 "Расстояния от зданий,
// сооружений и объектов инженерного благоустройства до оси растения".
//
// Это ДОПОЛНИТЕЛЬНЫЙ запас поверх уже нарисованной зоны (для труб/кабелей она
// уже отбуферена на minDistance из parser/parse_dxf.py — это охранная зона
// самой сети, не связанная с видом посадки). 0 означает "не нормируется" —
// кустарник можно сажать вплотную к границе зоны.
export type PlantKind = "tree" | "bush";

interface SetbackRule {
  tree: number;
  bush: number;
}

const SETBACK_NORMS: Record<string, SetbackRule> = {
  building: { tree: 5.0, bush: 1.5 },
  road: { tree: 2.0, bush: 1.0 },
  gas_pipeline: { tree: 1.5, bush: 0 },
  sewer: { tree: 1.5, bush: 0 },
  water_pipeline: { tree: 2.0, bush: 0 },
  electrical: { tree: 2.0, bush: 0.7 },
  pedestrian_path: { tree: 0.7, bush: 0.5 },
};

// Для зон без табличного значения (трансформатор, детская площадка, парковка,
// охраняемая зона, наземная ЛЭП) — берём minDistance зоны как есть, без
// выдумывания цифр, которые нечем подтвердить.
export function setbackFor(zoneType: string, plantKind: PlantKind, zoneMinDistance: number): number {
  const rule = SETBACK_NORMS[zoneType];
  return rule ? rule[plantKind] : zoneMinDistance;
}

export function plantKindOfObjectType(type: string): PlantKind | undefined {
  return type === "tree" || type === "bush" ? type : undefined;
}
