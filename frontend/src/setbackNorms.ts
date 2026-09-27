// Нормативные отступы посадок от зданий и сетей по виду посадки (дерево или
// кустарник): СП 42.13330.2016, п. 9.6, табл. 9.1 и табл. 3.6.1 ППМ 743-ПП.
// Отступ добавляется к зоне, которая для сетей уже включает охранный
// коридор. 0 -- не нормируется.
//
// Копия backend/core/setback_norms.py: при правке обновить оба файла.
// Обоснование чисел -- там.
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
  // "Силовой кабель и кабель связи" — одна строка таблицы.
  electrical: { tree: 2.0, bush: 0.7 },
  signal_cable: { tree: 2.0, bush: 0.7 },
  heat_network: { tree: 2.0, bush: 1.0 },
  pedestrian_path: { tree: 0.7, bush: 0.5 },
};

// Правила по породе: задаются по РОДУ (первое слово названия вида, без учёта
// регистра, ё == е) и только ужесточают табличную норму — берётся максимум.
interface SpeciesSetbackRule {
  genera: string[];
  zoneType: string;
  distance: number;
  kinds: PlantKind[];
}

const WIDE_CROWN = ["липа", "клен", "дуб", "каштан", "тополь"]; // 743-ПП, табл. 3.6.1, прим. 3
const THORNY = ["роза", "шиповник", "барбарис", "боярышник"]; // СП 82.13330.2016, п. 9.22
const HEAT_2M = ["липа", "клен", "сирень", "жимолость"]; // МГСН 1.02-02, п. 4.2.8
const HEAT_4M = ["тополь", "боярышник", "кизильник", "дерен", "лиственница", "береза"]; // МГСН 1.02-02, п. 4.2.8
const BOTH: PlantKind[] = ["tree", "bush"];

const SPECIES_SETBACK_RULES: SpeciesSetbackRule[] = [
  { genera: WIDE_CROWN, zoneType: "building", distance: 10.0, kinds: ["tree"] },
  { genera: THORNY, zoneType: "pedestrian_path", distance: 2.0, kinds: BOTH },
  { genera: THORNY, zoneType: "playground_zone", distance: 2.0, kinds: BOTH },
  { genera: HEAT_2M, zoneType: "heat_network", distance: 2.0, kinds: BOTH },
  { genera: HEAT_4M, zoneType: "heat_network", distance: 4.0, kinds: BOTH },
  // Экспертная оценка, не норма акта.
  { genera: ["тополь"], zoneType: "sewer", distance: 3.0, kinds: ["tree"] },
  { genera: ["тополь"], zoneType: "water_pipeline", distance: 3.0, kinds: ["tree"] },
  { genera: ["ива"], zoneType: "building", distance: 6.0, kinds: ["tree"] },
  { genera: ["ива"], zoneType: "sewer", distance: 3.5, kinds: ["tree"] },
  { genera: ["ива"], zoneType: "water_pipeline", distance: 3.5, kinds: ["tree"] },
];

// Наибольший отступ из обеих таблиц — на столько geometry.ts расширяет
// габарит зоны в индексе, чтобы не потерять нарушение у точки за её границей.
export const MAX_SETBACK_M = Math.max(
  ...Object.values(SETBACK_NORMS).flatMap((rule) => [rule.tree, rule.bush]),
  ...SPECIES_SETBACK_RULES.map((rule) => rule.distance)
);

export function genusOf(species?: string): string | undefined {
  const first = species?.replace(/[(,]/g, " ").trim().split(/\s+/)[0];
  return first ? first.toLowerCase().replace(/ё/g, "е") : undefined;
}

// Зоны без табличного значения (трансформатор, площадка, парковка, ЛЭП) --
// minDistance зоны. Правила по породе только ужесточают норму.
export function setbackFor(zoneType: string, plantKind: PlantKind, zoneMinDistance: number, species?: string): number {
  const rule = SETBACK_NORMS[zoneType];
  let distance = rule ? rule[plantKind] : zoneMinDistance;
  const genus = genusOf(species);
  if (genus) {
    for (const speciesRule of SPECIES_SETBACK_RULES) {
      if (speciesRule.zoneType === zoneType && speciesRule.kinds.includes(plantKind) && speciesRule.genera.includes(genus)) {
        distance = Math.max(distance, speciesRule.distance);
      }
    }
  }
  return distance;
}

export function plantKindOfObjectType(type: string): PlantKind | undefined {
  return type === "tree" || type === "bush" ? type : undefined;
}
