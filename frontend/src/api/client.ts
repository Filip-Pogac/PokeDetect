// Thin typed wrapper around the PokeDetect REST API.

export interface User {
  id: number;
  email: string;
  display_name: string;
  created_at: string;
}

export interface AuthResponse {
  access_token: string;
  token_type: string;
  user: User;
}

export interface PriceInfo {
  market_price: number | null;
  currency: string;
  estimated_price: number | null;
  source: string;
  disclaimer: string;
}

export interface CardMatch {
  tcg_id: string;
  name: string;
  set_name: string;
  number: string;
  rarity: string;
  image_url: string;
  price: PriceInfo;
  confidence: number;
}

export interface ConditionEstimate {
  condition: string;
  confidence: number;
  is_potentially_damaged: boolean;
  notes: string[];
}

export interface ScanResult {
  matches: CardMatch[];
  condition: ConditionEstimate;
  recognized_text: string[];
  card_detected: boolean;
  message: string;
}

export interface CollectionCard {
  id: number;
  name: string;
  set_name: string;
  number: string;
  rarity: string;
  image_url: string;
  tcg_id: string;
  market_price: number | null;
  currency: string;
  condition: string;
  condition_confidence: number | null;
  damage_notes: string;
  quantity: number;
  created_at: string;
}

const TOKEN_KEY = "pokedetect_token";

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable — ignore */
  }
}

class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string> | undefined),
  };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const resp = await fetch(path, { ...options, headers });

  if (resp.status === 204) return undefined as T;

  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    const detail =
      (data && (data.detail || data.message)) || `Request failed (${resp.status})`;
    throw new ApiError(
      typeof detail === "string" ? detail : "Request failed",
      resp.status,
    );
  }
  return data as T;
}

export const api = {
  register: (email: string, display_name: string, password: string) =>
    request<AuthResponse>("/api/auth/register", {
      method: "POST",
      body: JSON.stringify({ email, display_name, password }),
    }),

  login: (email: string, password: string) =>
    request<AuthResponse>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),

  me: () => request<User>("/api/auth/me"),

  scan: (
    image: string,
    nameHint?: string,
    numberHint?: string,
    // User-corrected card corners as four [x, y] pairs normalized to 0-1.
    corners?: Array<[number, number]>,
  ) =>
    request<ScanResult>("/api/scan", {
      method: "POST",
      body: JSON.stringify({
        image,
        name_hint: nameHint || null,
        number_hint: numberHint || null,
        corners: corners ?? null,
      }),
    }),

  searchCards: (name: string, condition: string) =>
    request<CardMatch[]>(
      `/api/scan/search?name=${encodeURIComponent(name)}&condition=${encodeURIComponent(
        condition,
      )}`,
    ),

  listCollection: () => request<CollectionCard[]>("/api/collection"),

  addCard: (card: Partial<CollectionCard>) =>
    request<CollectionCard>("/api/collection", {
      method: "POST",
      body: JSON.stringify(card),
    }),

  updateCard: (
    id: number,
    patch: Partial<Pick<CollectionCard, "condition" | "damage_notes" | "quantity">>,
  ) =>
    request<CollectionCard>(`/api/collection/${id}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),

  deleteCard: (id: number) =>
    request<void>(`/api/collection/${id}`, { method: "DELETE" }),
};

export { ApiError };
