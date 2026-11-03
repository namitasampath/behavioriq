const ANON_KEY = "biq_anon_user_id";
const SESSION_KEY = "biq_session";

export interface SessionState {
  session_id: string;
  seq: number;
  last_activity: number;
  last_event_at: number;
}

function randomBytes(n: number): Uint8Array {
  const bytes = new Uint8Array(n);
  if (typeof crypto !== "undefined" && typeof crypto.getRandomValues === "function") {
    crypto.getRandomValues(bytes);
  } else {
    for (let i = 0; i < n; i++) bytes[i] = Math.floor(Math.random() * 256);
  }
  return bytes;
}

function randomId(prefix: string): string {
  const hex = Array.from(randomBytes(8), (b) => b.toString(16).padStart(2, "0")).join("");
  return `${prefix}_${hex}`;
}

/** uuid v4, via crypto.randomUUID when available (Node 19+, all modern browsers). */
export function uuidv4(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  const b = randomBytes(16);
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const hex = Array.from(b, (x) => x.toString(16).padStart(2, "0"));
  return [hex.slice(0, 4).join(""), hex.slice(4, 6).join(""), hex.slice(6, 8).join(""), hex.slice(8, 10).join(""), hex.slice(10, 16).join("")].join("-");
}

/** In-memory fallback for environments with no localStorage (e.g. SSR). */
export function memoryStorage(): Storage {
  const map = new Map<string, string>();
  return {
    getItem: (k: string) => (map.has(k) ? (map.get(k) as string) : null),
    setItem: (k: string, v: string) => void map.set(k, v),
    removeItem: (k: string) => void map.delete(k),
    clear: () => void map.clear(),
    key: (i: number) => Array.from(map.keys())[i] ?? null,
    get length() {
      return map.size;
    },
  } as Storage;
}

/** Persists anon_user_id (forever) and the current session (rotates after idleMs of inactivity). */
export class IdentityStore {
  constructor(
    private readonly storage: Storage,
    private readonly sessionTimeoutMs: number,
  ) {}

  getAnonUserId(): string {
    let id = this.storage.getItem(ANON_KEY);
    if (!id) {
      id = randomId("u");
      this.storage.setItem(ANON_KEY, id);
    }
    return id;
  }

  getSession(now: number): SessionState {
    const raw = this.storage.getItem(SESSION_KEY);
    if (raw) {
      try {
        const parsed = JSON.parse(raw) as SessionState;
        if (now - parsed.last_activity <= this.sessionTimeoutMs) {
          return parsed;
        }
      } catch {
        // malformed - fall through and start a fresh session
      }
    }
    const fresh: SessionState = { session_id: randomId("s"), seq: 0, last_activity: now, last_event_at: now };
    this.save(fresh);
    return fresh;
  }

  save(session: SessionState): void {
    this.storage.setItem(SESSION_KEY, JSON.stringify(session));
  }
}
