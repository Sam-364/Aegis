/**
 * Demo mode: replay a recorded stack instead of calling one.
 *
 * The console is a pure client of the Aegis API, so with no API to reach it renders an empty
 * shell. Rather than mock data that drifts from the real shapes, `scripts/capture_demo_fixtures.py`
 * records the responses of a running stack and this module replays them. The fixtures are static
 * assets under `/demo`, so none of it enters the JavaScript bundle.
 *
 * Writes are refused rather than faked: a demo that lets you click "approve" and appears to
 * remediate something would be lying about the one thing this system is careful about.
 */
import { ApiError } from "@/lib/api-error";

interface DemoIndex {
  captured_at: string;
  featured: { number: number; id: string }[];
  routes: Record<string, string>;
}

let indexPromise: Promise<DemoIndex> | null = null;
const bodies = new Map<string, unknown>();

const BASE = "/demo";

async function loadIndex(): Promise<DemoIndex> {
  indexPromise ??= fetch(`${BASE}/index.json`, { cache: "force-cache" }).then(async (r) => {
    if (!r.ok) throw new Error(`demo index ${r.status}`);
    return (await r.json()) as DemoIndex;
  });
  return indexPromise;
}

/** The key the capture script indexed this call under: path, plus a sorted query when present. */
export function demoKey(path: string, query?: Record<string, unknown>): string {
  const entries = Object.entries(query ?? {})
    .filter(([, v]) => v !== undefined && v !== null && v !== "")
    .map(([k, v]) => [k, Array.isArray(v) ? v.join(",") : String(v)] as const)
    .sort(([a], [b]) => a.localeCompare(b));
  return entries.length ? `${path}?${entries.map(([k, v]) => `${k}=${v}`).join("&")}` : path;
}

export async function demoGet<T>(path: string, query?: Record<string, unknown>): Promise<T> {
  const index = await loadIndex();
  // Exact call first; then the bare path, so a different limit or offset still resolves.
  const file = index.routes[demoKey(path, query)] ?? index.routes[path];
  if (!file) {
    throw new ApiError({
      status: 404,
      title: "Not in the recording",
      detail: `The demo console replays a recorded incident set; ${path} was not part of it.`,
      type: "demo_not_recorded",
      url: path,
      method: "GET",
    });
  }
  if (!bodies.has(file)) {
    const res = await fetch(`${BASE}/${file}`, { cache: "force-cache" });
    if (!res.ok) {
      throw new ApiError({
        status: res.status,
        title: "Demo fixture unavailable",
        detail: `Could not load ${file}.`,
        url: path,
        method: "GET",
      });
    }
    bodies.set(file, await res.json());
  }
  return bodies.get(file) as T;
}

export function demoWrite(path: string, method: string): never {
  throw new ApiError({
    status: 403,
    title: "This console is a recording",
    detail:
      "You are looking at a real incident captured from a running Aegis stack, served as static " +
      "data. Approving, rejecting and injecting faults need the live control plane — run the " +
      "stack locally with `docker compose up` to drive it for real.",
    type: "demo_read_only",
    url: path,
    method,
  });
}

export async function demoCapturedAt(): Promise<string> {
  try {
    return (await loadIndex()).captured_at;
  } catch {
    return "";
  }
}

export async function demoFeatured(): Promise<{ number: number; id: string }[]> {
  try {
    return (await loadIndex()).featured;
  } catch {
    return [];
  }
}
