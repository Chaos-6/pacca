/**
 * DirectorQueue — Medical Director review queue.
 *
 * Reskinned in PR-UI-4 to the Editorial-Clinical aesthetic. Each escalated
 * case renders as a .sme-card-emphasis with a mustard top border (signals
 * "in review"). Override-and-approve uses .sme-button-approve.
 *
 * PHI: the payload is opaque id + age only. The backend's ReviewQueueItem
 * carries no name and no date of birth, so none can reach this component.
 *
 * What changed, and why it matters
 * --------------------------------
 * This component used to render a hardcoded one-element `DEMO_QUEUE`. It
 * showed the same synthetic case whether the system had escalated nothing or
 * a hundred cases. It now reads `GET /api/v1/authorizations/review-queue`
 * through the typed client in `src/api/authorizations.ts`. An empty queue is
 * a real answer and is rendered as one, never padded with sample data.
 *
 * Two other pieces of theatre are gone:
 *
 *   - "Confirm denial" called no endpoint. It filtered the row out of local
 *     state and nothing else, so it looked like a disposition and was not
 *     one. It is removed rather than relabelled — the server has no way to
 *     record that decision (see `resolution_supported`).
 *   - The override rationale was a canned paragraph about a "manual labourer
 *     and primary earner". That text was written for one demo case and would
 *     have been attached, as clinical justification, to whatever real case
 *     the button happened to be under. The director now types it.
 *
 * Overriding still has a real effect: /feedback writes a precedent into the
 * case_precedents collection, which the DecisionAgent weighs on later runs.
 * It does not resolve the case, so the case stays in the list.
 */

import { useCallback, useEffect, useState } from 'react';
import { authorizationsApi, type ReviewQueueItem } from '../api/authorizations';
import { MonoChip } from './MonoChip';
import { StatusInk } from './StatusInk';
import { PageHeader } from '../sme-authoring/components/PageHeader';

type Banner = { tone: 'approved' | 'denied' | 'info'; message: string };

export function DirectorQueue() {
  const [queue, setQueue] = useState<ReviewQueueItem[]>([]);
  const [resolutionSupported, setResolutionSupported] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [status, setStatus] = useState<Banner | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const [rationales, setRationales] = useState<Record<string, string>>({});

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const response = await authorizationsApi.reviewQueue();
      setQueue(response.items);
      setResolutionSupported(response.resolution_supported);
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : 'Failed to load the review queue.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const handleOverride = async (caseItem: ReviewQueueItem) => {
    const rationale = (rationales[caseItem.request_id] ?? '').trim();
    if (!rationale) {
      setStatus({
        tone: 'denied',
        message: 'Enter the clinical rationale for the override before submitting.',
      });
      return;
    }

    setPending(caseItem.request_id);
    setStatus({ tone: 'info', message: 'Recording the override as a precedent…' });

    try {
      await authorizationsApi.submitFeedback({
        case_summary: caseItem.clinical_notes,
        decision: 'APPROVED',
        rationale,
      });
      setStatus({
        tone: 'approved',
        message: resolutionSupported
          ? 'Precedent recorded. The agent will reference this decision for future cases.'
          : 'Precedent recorded — the agent will reference it for future cases. The case stays in the queue: no endpoint records a disposition yet.',
      });
    } catch (error) {
      setStatus({
        tone: 'denied',
        message: error instanceof Error ? error.message : 'Failed to submit the override.',
      });
    } finally {
      setPending(null);
    }
  };

  return (
    <div className="sme-page sme-page-enter sme-page-enter-active">
      <PageHeader
        label="Director"
        title="Medical Director queue"
        hint="Cases the agent escalated for human review. Recording an override teaches the AI by writing a precedent it consults on later cases."
      />

      {!loading && !loadError && !resolutionSupported && (
        <div
          className="sme-card"
          style={{ borderLeft: '4px solid var(--sme-review)', marginBottom: '2rem' }}
        >
          <StatusInk outcome="review">
            Case resolution is not implemented. An override is recorded as a precedent, but no
            disposition is stored against the case, so it remains listed here after review.
          </StatusInk>
        </div>
      )}

      {status && (
        <div
          className="sme-card"
          style={{
            borderLeft: `4px solid ${
              status.tone === 'approved'
                ? 'var(--sme-approve)'
                : status.tone === 'denied'
                  ? 'var(--sme-deny)'
                  : 'var(--sme-info)'
            }`,
            marginBottom: '2rem',
          }}
        >
          <StatusInk outcome={status.tone === 'info' ? 'info' : status.tone}>
            {status.message}
          </StatusInk>
        </div>
      )}

      {loading ? (
        <div className="sme-empty">Loading the review queue…</div>
      ) : loadError ? (
        <div
          className="sme-card"
          style={{ borderLeft: '4px solid var(--sme-deny)', marginBottom: '2rem' }}
        >
          <StatusInk outcome="denied">{loadError}</StatusInk>
          <div style={{ marginTop: '1rem' }}>
            <button type="button" className="sme-button" onClick={() => void load()}>
              Retry
            </button>
          </div>
        </div>
      ) : queue.length === 0 ? (
        <div className="sme-empty">Queue empty. No cases pending Medical Director review.</div>
      ) : (
        queue.map((caseItem) => (
          <article
            key={caseItem.request_id}
            className="sme-card-emphasis"
            style={{
              borderTopColor: 'var(--sme-review)',
              marginBottom: '2rem',
            }}
          >
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'baseline',
                gap: '1rem',
                marginBottom: '1rem',
                flexWrap: 'wrap',
              }}
            >
              <div>
                <div className="sme-label">Case</div>
                <div
                  style={{
                    fontFamily: 'var(--sme-font-display)',
                    fontSize: '1.375rem',
                    fontWeight: 600,
                    color: 'var(--sme-deep-ink)',
                    marginTop: '0.25rem',
                  }}
                >
                  {caseItem.patient_ref}
                </div>
                <MonoChip size="sm" tone="muted">
                  {caseItem.request_id}
                </MonoChip>
              </div>
              <StatusInk
                outcome="review"
                style={{
                  fontSize: '0.9375rem',
                  fontVariant: 'small-caps',
                  letterSpacing: '0.04em',
                }}
              >
                {caseItem.ai_status.toLowerCase().replace(/_/g, ' ')}
              </StatusInk>
            </div>

            <hr className="sme-rule-soft" />

            <div
              style={{
                display: 'grid',
                gridTemplateColumns: '1fr 1fr',
                gap: '1.5rem',
                marginBottom: '1.5rem',
              }}
            >
              <div>
                <div className="sme-label">Diagnosis</div>
                <p style={{ marginTop: '0.25rem', marginBottom: 0 }}>{caseItem.diagnosis_code}</p>
              </div>
              <div>
                <div className="sme-label">Requested procedure</div>
                <p style={{ marginTop: '0.25rem', marginBottom: 0 }}>{caseItem.procedure_code}</p>
              </div>
            </div>

            <div style={{ marginBottom: '1.5rem' }}>
              <div className="sme-label">Clinical notes</div>
              <p
                style={{
                  marginTop: '0.25rem',
                  color: 'var(--sme-ink-soft)',
                  lineHeight: 1.55,
                }}
              >
                {caseItem.clinical_notes}
              </p>
            </div>

            <div
              style={{
                borderLeft: '2px solid var(--sme-review)',
                paddingLeft: '0.75rem',
                marginBottom: '2rem',
              }}
            >
              <div className="sme-label sme-status-review">
                Agent escalation rationale · confidence {caseItem.confidence_score.toFixed(2)}
              </div>
              <p
                style={{
                  marginTop: '0.25rem',
                  marginBottom: 0,
                  fontStyle: 'italic',
                  color: 'var(--sme-ink-soft)',
                }}
              >
                {caseItem.ai_rationale}
              </p>
            </div>

            <div style={{ marginBottom: '1.5rem' }}>
              <label className="sme-label" htmlFor={`rationale-${caseItem.request_id}`}>
                Override rationale
              </label>
              <textarea
                id={`rationale-${caseItem.request_id}`}
                className="sme-input"
                rows={3}
                style={{ width: '100%', marginTop: '0.25rem' }}
                placeholder="Clinical justification for approving against the agent's assessment."
                value={rationales[caseItem.request_id] ?? ''}
                onChange={(event) =>
                  setRationales((current) => ({
                    ...current,
                    [caseItem.request_id]: event.target.value,
                  }))
                }
              />
            </div>

            <div
              style={{
                display: 'flex',
                gap: '0.75rem',
                paddingTop: '1rem',
                borderTop: '1px solid var(--sme-rule-soft)',
              }}
            >
              <button
                type="button"
                className="sme-button sme-button-approve"
                onClick={() => void handleOverride(caseItem)}
                disabled={pending === caseItem.request_id}
                style={{
                  opacity: pending === caseItem.request_id ? 0.6 : 1,
                  cursor: pending === caseItem.request_id ? 'not-allowed' : 'pointer',
                }}
              >
                {pending === caseItem.request_id ? 'Submitting…' : 'Override · teach AI'}
              </button>
            </div>
          </article>
        ))
      )}
    </div>
  );
}
