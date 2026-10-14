// The hosted API (src/brink/hosted/app.py): JSON over fetch, with the Supabase access token.
import type { Phase } from "../types";
import { accessToken } from "./auth";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function api<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {};
  const token = await accessToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  let r: Response;
  try {
    r = await fetch(`api/${path.replace(/^\//, "")}`, { method, headers, body: body !== undefined ? JSON.stringify(body) : undefined });
  } catch {
    throw new ApiError(0, "Can't reach the server. Check your connection and try again.");
  }
  const text = await r.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    throw new ApiError(r.status, `The server answered HTTP ${r.status} without data.`);
  }
  if (!r.ok) throw new ApiError(r.status, (data as { error?: string } | null)?.error ?? `HTTP ${r.status}`);
  return data as T;
}

// ------------------------------------------------------------------ shapes
export interface Me { id: string; display_name: string; email: string | null; unread: number; email_notifications: boolean }

export interface GameSummary {
  id: string; name: string; status: "forming" | "active" | "finished" | "cancelled";
  visibility: "public" | "private"; deadline_hours: number; press: "full" | "none"; last_year: number;
  phase_name: string | null; deadline: string | null; step: number; created_at: string;
  started_at: string | null; finished_at: string | null; created_by: string; created_by_name?: string;
  players?: number; open_seats?: string[]; invite_code?: string;
}

export interface MyGame extends GameSummary {
  power: string; kind: string | null; my_turn: boolean; involved: boolean; ready: boolean; saved: boolean;
  creator: boolean; result: { kind: string; winner: string | null; scores?: Record<string, number> } | null;
}

export interface Seat {
  power: string; name: string | null; human: boolean; bot: boolean; you: boolean; involved: boolean;
  ready: boolean | null; saved: boolean | null; missed: number;
}

export interface Decision {
  kind: "orders" | "retreats" | "propose" | "winter"; phase: string; step: number; powers: string[];
  position: Phase; events: string[]; options: Record<string, Record<string, unknown>>;
  chair?: string; proposal?: string | null;
}

export interface GameView {
  game: GameSummary;
  seats: Seat[];
  you: { power: string | null; creator: boolean; signed_in: boolean };
  pending: Omit<Decision, "options"> | null;
  decision: Decision | null;
  submission: { orders: string[]; ready: boolean; updated_at: string } | null;
  result: (MyGame["result"] & { year?: number; centres?: Record<string, number> }) | null;
  phases: Phase[];
  phase_count: number;
  server_time: string;
}

export interface Message {
  id: number; sender_power: string; recipients: string[]; body: string; phase_name: string | null; created_at: string;
}

export interface Notification {
  id: number; game_id: string | null; game_name: string | null; kind: string; body: string;
  read_at: string | null; created_at: string;
}
