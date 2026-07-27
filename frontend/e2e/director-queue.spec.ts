/**
 * Medical Director queue E2E — the queue reflects the backend, not a constant.
 *
 * The component previously rendered a hardcoded one-element `DEMO_QUEUE`, so
 * it showed the same synthetic case (REQ-8842) regardless of what the system
 * had escalated. These tests drive the rendered UI against mocked
 * `/review-queue` responses and assert the two states that constant made
 * impossible: real cases render, and an empty queue renders as empty.
 *
 * Backend is mocked via route interception — no FastAPI server needed.
 *
 * To run:
 *   cd frontend
 *   npx playwright install --with-deps chromium    # one-time
 *   npx playwright test e2e/director-queue.spec.ts
 */

import { expect, test, type Page } from '@playwright/test';

const SYNTHETIC_TOKEN = 'eyJ.director-queue-test.token';

const CASE_ONE = {
  request_id: 'AUTH-E2E-1',
  decision_id: 'DEC-E2E-1',
  patient_ref: 'Patient P-AUTH-E2E-1 · 47yo',
  diagnosis_code: 'M54.5',
  procedure_code: '72148',
  clinical_notes: 'Low back pain for 2 weeks. No red flags.',
  ai_status: 'IN_REVIEW',
  ai_rationale: 'Conservative therapy duration below the guideline threshold.',
  confidence_score: 0.82,
  escalated_at: '2026-07-20T10:00:00Z',
};

const CASE_TWO = {
  ...CASE_ONE,
  request_id: 'AUTH-E2E-2',
  decision_id: 'DEC-E2E-2',
  patient_ref: 'Patient P-AUTH-E2E-2 · 61yo',
  diagnosis_code: 'C50.911',
  procedure_code: '77067',
  clinical_notes: 'Screening interval shorter than guideline.',
  ai_rationale: 'Interval ambiguity; escalating for human judgement.',
  confidence_score: 0.64,
  escalated_at: '2026-07-21T09:00:00Z',
};

async function mockQueue(page: Page, items: unknown[]) {
  await page.route('**/api/v1/authorizations/review-queue*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items, resolution_supported: false }),
    });
  });
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript((token) => {
    window.localStorage.setItem('token', token);
  }, SYNTHETIC_TOKEN);
});

test.describe('Medical Director queue', () => {
  test('renders every case the API returns', async ({ page }) => {
    await mockQueue(page, [CASE_ONE, CASE_TWO]);
    await page.goto('/director');

    await expect(page.getByText('AUTH-E2E-1', { exact: true })).toBeVisible();
    await expect(page.getByText('AUTH-E2E-2', { exact: true })).toBeVisible();
    await expect(page.getByText(CASE_TWO.ai_rationale)).toBeVisible();

    // The removed constant's case id must not appear from anywhere.
    await expect(page.getByText('REQ-8842')).toHaveCount(0);
  });

  test('an empty queue renders as empty, not as sample data', async ({ page }) => {
    await mockQueue(page, []);
    await page.goto('/director');

    await expect(
      page.getByText('No cases pending Medical Director review.'),
    ).toBeVisible();
    await expect(page.getByText('REQ-8842')).toHaveCount(0);
    await expect(page.getByRole('button', { name: /Override/ })).toHaveCount(0);
  });

  test('states that acting on a case does not resolve it', async ({ page }) => {
    await mockQueue(page, [CASE_ONE]);
    await page.goto('/director');

    await expect(page.getByText(/Case resolution is not implemented/)).toBeVisible();

    // "Confirm denial" filtered local state and called nothing. It is gone.
    await expect(page.getByRole('button', { name: 'Confirm denial' })).toHaveCount(0);
  });

  test('a failed fetch reports the error and offers a retry', async ({ page }) => {
    await page.route('**/api/v1/authorizations/review-queue*', async (route) => {
      await route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'queue unavailable' }),
      });
    });
    await page.goto('/director');

    await expect(page.getByText('queue unavailable')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Retry' })).toBeVisible();
  });

  test('an override sends the typed rationale and leaves the case listed', async ({
    page,
  }) => {
    await mockQueue(page, [CASE_ONE]);

    let sent: Record<string, string> | null = null;
    await page.route('**/api/v1/authorizations/feedback', async (route) => {
      sent = route.request().postDataJSON();
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'recorded' }),
      });
    });

    await page.goto('/director');
    await page.getByLabel('Override rationale').fill('Imaging justified by occupational risk.');
    await page.getByRole('button', { name: /Override/ }).click();

    await expect(page.getByText(/Precedent recorded/)).toBeVisible();
    expect(sent).toMatchObject({
      decision: 'APPROVED',
      rationale: 'Imaging justified by occupational risk.',
    });

    // Not resolved, so still listed — the message says so and the UI agrees.
    await expect(page.getByText('AUTH-E2E-1', { exact: true })).toBeVisible();
  });

  test('an override with no rationale is refused before any request', async ({ page }) => {
    await mockQueue(page, [CASE_ONE]);

    let feedbackCalls = 0;
    await page.route('**/api/v1/authorizations/feedback', async (route) => {
      feedbackCalls += 1;
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    });

    await page.goto('/director');
    await page.getByRole('button', { name: /Override/ }).click();

    await expect(page.getByText(/Enter the clinical rationale/)).toBeVisible();
    expect(feedbackCalls).toBe(0);
  });
});
