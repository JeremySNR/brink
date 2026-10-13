// Signing in: Supabase Auth email magic links. The client only holds the session; every read and write
// goes through the Python API with the access token (the browser never touches tables).
//
// Configuration: the Supabase URL and anon key are inlined at build time (VITE_ or NEXT_PUBLIC_ variables,
// see vite.config.ts), or fetched from /api/config. A local dev server (BRINK_DEV_AUTH=1) also offers
// "dev:" sign-in without Supabase.
import { createClient, type SupabaseClient } from "@supabase/supabase-js";

export interface Session {
  token: string;
  email: string | null;
  dev: boolean;
}

const DEV_KEY = "brink.devUser";
const NEXT_KEY = "brink.next";
let client: SupabaseClient | null = null;
let devAuth = false;
let current: Session | null = null;
let urlError: string | null = null;
const listeners: (() => void)[] = [];

export function configured(): boolean {
  return client !== null || devAuth;
}

export function devAvailable(): boolean {
  return devAuth;
}

export function session(): Session | null {
  return current;
}

/** The error a magic link came back with (expired, already used), once. */
export function takeUrlError(): string | null {
  const e = urlError;
  urlError = null;
  return e;
}

export function onAuthChange(fn: () => void) {
  listeners.push(fn);
}

function emit() {
  for (const fn of listeners) fn();
}

export async function initAuth(): Promise<void> {
  let url = (import.meta.env.VITE_SUPABASE_URL || import.meta.env.NEXT_PUBLIC_SUPABASE_URL) as string | undefined;
  let key = (import.meta.env.VITE_SUPABASE_ANON_KEY || import.meta.env.NEXT_PUBLIC_SUPABASE_ANON_KEY) as string | undefined;
  try {
    const r = await fetch("api/config");
    if (r.ok) {
      const c = (await r.json()) as { supabase_url?: string; supabase_anon_key?: string; dev_auth?: boolean };
      url ||= c.supabase_url ?? undefined;
      key ||= c.supabase_anon_key ?? undefined;
      devAuth = !!c.dev_auth;
    }
  } catch {
    /* no API (static preview): sign-in unavailable */
  }
  // a magic link that failed comes back as #error=...&error_description=...
  const h = new URLSearchParams(location.hash.replace(/^#/, ""));
  if (h.get("error_description")) urlError = h.get("error_description");
  if (devAuth) {
    try {
      // ?dev_user=ada signs in as a fake player (local dev servers only: the API refuses dev tokens elsewhere)
      const q = new URLSearchParams(location.search).get("dev_user");
      if (q) localStorage.setItem(DEV_KEY, q.toLowerCase().replace(/[^a-z0-9_.-]/g, "").slice(0, 24));
      const name = localStorage.getItem(DEV_KEY);
      if (name) current = { token: `dev:${name}`, email: `${name}@dev.local`, dev: true };
    } catch {
      /* ignore */
    }
  }
  if (url && key) {
    client = createClient(url, key, {
      auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true, flowType: "implicit" },
    });
    // processes #access_token=... from a magic link, or restores the stored session
    const { data } = await client.auth.getSession();
    if (data.session) current = { token: data.session.access_token, email: data.session.user.email ?? null, dev: false };
    client.auth.onAuthStateChange((_ev, s) => {
      const was = current?.token ?? null;
      if (current?.dev) return;
      current = s ? { token: s.access_token, email: s.user.email ?? null, dev: false } : null;
      if ((current?.token ?? null) !== was) emit();
    });
  }
  if (/^#(access_token|error|refresh_token)=/.test(location.hash)) {
    // the magic link landed: go where the player was heading
    let next = "#/";
    try {
      next = localStorage.getItem(NEXT_KEY) || "#/";
      localStorage.removeItem(NEXT_KEY);
    } catch {
      /* ignore */
    }
    if (urlError) next = "#/signin";
    history.replaceState(null, "", `${location.pathname}${location.search}${next}`);
  }
}

/** A fresh access token (refreshed by supabase-js when close to expiry). */
export async function accessToken(): Promise<string | null> {
  if (current?.dev) return current.token;
  if (!client) return null;
  const { data } = await client.auth.getSession();
  return data.session?.access_token ?? null;
}

export async function sendMagicLink(email: string, next: string): Promise<void> {
  if (!client) throw new Error("Sign-in isn't configured on this server.");
  try {
    localStorage.setItem(NEXT_KEY, next);
  } catch {
    /* ignore */
  }
  const { error } = await client.auth.signInWithOtp({
    email, options: { emailRedirectTo: `${location.origin}${location.pathname}`, shouldCreateUser: true },
  });
  if (error) throw new Error(error.message);
}

export function devSignIn(name: string) {
  const clean = name.toLowerCase().replace(/[^a-z0-9_.-]/g, "").slice(0, 24) || "dev";
  try {
    localStorage.setItem(DEV_KEY, clean);
  } catch {
    /* ignore */
  }
  current = { token: `dev:${clean}`, email: `${clean}@dev.local`, dev: true };
  emit();
}

export async function signOut() {
  try {
    localStorage.removeItem(DEV_KEY);
  } catch {
    /* ignore */
  }
  if (client && !current?.dev) await client.auth.signOut();
  current = null;
  emit();
}
