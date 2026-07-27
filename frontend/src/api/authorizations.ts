/**
 * Authorization API client — the Provider/Director surface.
 *
 * Mirrors the `smeFetch` pattern in `src/sme-authoring/api.ts`: relative
 * route base, JWT from localStorage, JSON errors turned into messages.
 * Kept in its own module so the Director queue talks to a typed boundary
 * instead of open-coding `fetch` against a shape nothing checks.
 *
 * The types below are a direct translation of the Pydantic models in
 * `src/pacca/api/routes/authorizations.py` (ReviewQueueItem /
 * ReviewQueueResponse). Field names match on purpose — if the backend
 * model changes, the rename should be mechanical.
 */

const AUTHORIZATIONS_API_BASE = '/api/v1/authorizations';

export interface ReviewQueueItem {
  request_id: string;
  decision_id: string;
  /** Opaque identifier plus age. Never a name or a date of birth. */
  patient_ref: string;
  diagnosis_code: string;
  procedure_code: string;
  clinical_notes: string;
  ai_status: string;
  ai_rationale: string;
  confidence_score: number;
  escalated_at: string;
}

export interface ReviewQueueResponse {
  items: ReviewQueueItem[];
  /**
   * False today. No endpoint records a Medical Director's disposition of a
   * case, so a reviewed case reappears on the next fetch. The server sends
   * this so the UI cannot claim resolution works while the backend says
   * otherwise — do not hardcode it here.
   */
  resolution_supported: boolean;
}

export interface FeedbackRequest {
  case_summary: string;
  decision: string;
  rationale: string;
}

function authHeader(): HeadersInit {
  const token = localStorage.getItem('token');
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function authorizationsFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${AUTHORIZATIONS_API_BASE}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...authHeader(),
      ...init?.headers,
    },
  });

  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = body.detail || body.message || `HTTP ${response.status}`;
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
  }

  return response.json() as Promise<T>;
}

export const authorizationsApi = {
  reviewQueue(limit = 50): Promise<ReviewQueueResponse> {
    return authorizationsFetch(`/review-queue?limit=${encodeURIComponent(limit)}`);
  },

  submitFeedback(body: FeedbackRequest): Promise<unknown> {
    return authorizationsFetch('/feedback', {
      method: 'POST',
      body: JSON.stringify(body),
    });
  },
};
