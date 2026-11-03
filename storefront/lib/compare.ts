const KEY = "biq_compare";
const MAX = 3;

export function getCompareIds(): string[] {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as string[]) : [];
  } catch {
    return [];
  }
}

export function addCompareId(id: string): string[] {
  const ids = getCompareIds().filter((x) => x !== id);
  ids.push(id);
  const trimmed = ids.slice(-MAX);
  localStorage.setItem(KEY, JSON.stringify(trimmed));
  return trimmed;
}
