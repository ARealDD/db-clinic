const TOKEN_KEY = 'db_clinic_token';
const LEGACY_TOKEN_KEY = 'jwt_token';

function getToken(): string | null {
  try {
    const current = localStorage.getItem(TOKEN_KEY);
    if (current) return current;
    // One-time migration from the pre-SPA HTML/JS bundles that stored the
    // JWT under 'jwt_token'. We adopt the legacy value rather than silently
    // logging the user out, then delete the old key so this branch only runs
    // until the next page load.
    const legacy = localStorage.getItem(LEGACY_TOKEN_KEY);
    if (legacy) {
      localStorage.setItem(TOKEN_KEY, legacy);
      localStorage.removeItem(LEGACY_TOKEN_KEY);
      return legacy;
    }
    return null;
  } catch {
    return null;
  }
}

export function setToken(token: string | null) {
  if (token) {
    localStorage.setItem(TOKEN_KEY, token);
  } else {
    localStorage.removeItem(TOKEN_KEY);
  }
  // Always clear the legacy key on any explicit set/clear so logging out can
  // never leave a stale jwt_token behind for getToken() to resurrect.
  try {
    localStorage.removeItem(LEGACY_TOKEN_KEY);
  } catch {
    /* ignore storage errors */
  }
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
  try {
    localStorage.removeItem(LEGACY_TOKEN_KEY);
  } catch {
    /* ignore storage errors */
  }
}

export async function api<T = unknown>(path: string, options: { method?: string; body?: unknown } = {}): Promise<T> {
  const { method = 'GET', body } = options;
  const opts: RequestInit = { method, headers: {} as Record<string, string> };

  // Attach JWT token if available
  const token = getToken();
  if (token) {
    (opts.headers as Record<string, string>)['Authorization'] = `Bearer ${token}`;
  }

  if (body && !(body instanceof FormData)) {
    (opts.headers as Record<string, string>)['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  } else if (body instanceof FormData) {
    opts.body = body;
  }

  const res = await fetch(path, opts);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error((err as { error?: string }).error || (err as { detail?: string }).detail || `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}
