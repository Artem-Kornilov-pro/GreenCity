// Каталог видов посадок для отрисовки (размеры/форма кроны/цвет).
// Вынесен из ObjectVisual.tsx отдельным модулем сознательно: там, рядом с
// React-компонентами, экспорт констант ломает Fast Refresh — Vite при каждой
// правке файла делал полную перезагрузку страницы вместо горячей замены
// (см. предупреждение react/only-export-components).

export type TreeKind = "medium" | "tall" | "short" | "pine" | "round" | "round_large";

export interface TreePreset {
  label: string;
  trunkHeight: number;
  trunkRadius: [number, number];
  crownHeight: number;
  crownRadius: number;
  crownShape: "cone" | "cluster" | "pine";
  color: string;
}

// "средние"/"высокие"/"низкие" — варианты размера с конической кроной;
// "конусовые сосны" — узкая многоярусная крона отдельным силуэтом;
// "круглые" — лиственная крона кластером сфер (не один плоский шар — иначе
// смотрится как примитивная заготовка, а не дерево), в двух размерах.
export const TREE_PRESETS: Record<TreeKind, TreePreset> = {
  medium: {
    label: "Дерево — среднее",
    trunkHeight: 1.2,
    trunkRadius: [0.12, 0.16],
    crownHeight: 1.8,
    crownRadius: 0.9,
    crownShape: "cone",
    color: "#2e7d3a",
  },
  tall: {
    label: "Дерево — высокое",
    trunkHeight: 2.0,
    trunkRadius: [0.14, 0.18],
    crownHeight: 2.6,
    crownRadius: 1.1,
    crownShape: "cone",
    color: "#2e7d3a",
  },
  short: {
    label: "Дерево — низкое",
    trunkHeight: 0.6,
    trunkRadius: [0.1, 0.13],
    crownHeight: 1.0,
    crownRadius: 0.6,
    crownShape: "cone",
    color: "#3f9146",
  },
  pine: {
    label: "Сосна (конусовая)",
    trunkHeight: 1.4,
    trunkRadius: [0.12, 0.15],
    crownHeight: 3.2,
    crownRadius: 0.7,
    crownShape: "pine",
    color: "#1f5c33",
  },
  round: {
    label: "Дерево — круглая крона",
    trunkHeight: 1.2,
    trunkRadius: [0.12, 0.16],
    crownHeight: 1.6,
    crownRadius: 0.85,
    crownShape: "cluster",
    color: "#3a8f45",
  },
  round_large: {
    label: "Дерево — крупная круглая крона",
    trunkHeight: 1.6,
    trunkRadius: [0.16, 0.2],
    crownHeight: 2.2,
    crownRadius: 1.35,
    crownShape: "cluster",
    color: "#357f40",
  },
};

// Смещения (доля от crownRadius) для кластера из 4 сфер разного размера,
// собранных в один пушистый ком вместо одного идеального шара.
export const CLUSTER_LOBES: [number, number, number, number][] = [
  [0, 0.55, 0, 1],
  [0.55, 0.15, 0.2, 0.68],
  [-0.5, 0.3, -0.3, 0.62],
  [0.1, 0.65, -0.55, 0.58],
];

export type BushKind = "medium" | "tall" | "short";

export interface BushPreset {
  label: string;
  radius: number;
  color: string;
}

export const BUSH_PRESETS: Record<BushKind, BushPreset> = {
  medium: { label: "Кустарник — средний", radius: 0.5, color: "#4a9450" },
  tall: { label: "Кустарник — высокий", radius: 0.75, color: "#3f8a46" },
  short: { label: "Кустарник — низкий", radius: 0.32, color: "#5aa15f" },
};

export function isTreeKind(value: unknown): value is TreeKind {
  return typeof value === "string" && value in TREE_PRESETS;
}

export function isBushKind(value: unknown): value is BushKind {
  return typeof value === "string" && value in BUSH_PRESETS;
}
