export interface RecentCheck {
  check_id: string;
  project_label: string;
  created_at: string;
}

const KEY = "stackcheck.recentChecks.v1";
const MAX = 100;

function isRecent(value: unknown): value is RecentCheck {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v["check_id"] === "string" &&
    typeof v["project_label"] === "string" &&
    typeof v["created_at"] === "string"
  );
}

/** Last checks run on this device. Empty when storage is blocked. */
export function loadRecentChecks(): RecentCheck[] {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(isRecent).slice(0, MAX);
  } catch {
    return [];
  }
}

function saveAll(list: RecentCheck[]): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(list.slice(0, MAX)));
  } catch {
    // Storage blocked: the list simply doesn't persist.
  }
}

/** Newest first, deduplicated by check_id, capped at 100. */
export function addRecentCheck(entry: RecentCheck): RecentCheck[] {
  const list = loadRecentChecks().filter((e) => e.check_id !== entry.check_id);
  list.unshift(entry);
  saveAll(list);
  return list.slice(0, MAX);
}

export function clearRecentChecks(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    // Storage blocked: nothing to clear.
  }
}
