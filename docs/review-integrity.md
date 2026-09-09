# DS-CHG-001 review integrity

Scope: FR-1.3, FR-2.2, FR-4.1, NFR-3.1 and constitution section 5 (consistency and promotion gates).

The correction preserves the existing DSPy/SQLite architecture, explicit Review/Approve controls, loopback runtime, provider selection, and draft-only revision application.

- Revision executes the declared `ReviseSpec` signature through the real ChainOfThought/Refine path.
- Semantic consistency issues are blocking findings even when the reported numeric score is high.
- Formal review records a SHA-256 identity of the current tier and all prior tiers: formal revision IDs/content, draft buffers, and saved discovery answers. Saved answers are also supplied to semantic review.
- The backend reads each session from one SQLite snapshot. Approve validates evidence and writes approval in the same transaction.
- Changed context exposes effective `draft` / `STALE` status. Historical revision content and original review evidence remain stored. Legacy reviews without context identity require review again; no destructive migration is performed.
- Pending draft changes must be saved before review. Every new review requires explicit approval; a failed re-review cannot preserve approval.
- In-flight generation, revision, or review may commit only if its input snapshot is still current. Conflicts return HTTP 409 (or a terminal streaming error) and preserve newer user work.
- Approved export consumes one consistent snapshot and rejects stale approvals. Explicit labeled-draft export remains available for the formal revisions.

Regression evidence is in `tests/test_review_integrity.py` and `tests/browser_review_integrity.py`. Both run real DSPy code with deterministic DummyLM responses at the model boundary. The browser test uses the actual HTTP endpoints, SQLite, review board, and export gating. No test fixture is enabled in the production runtime.

These checks do not establish model quality, live provider interoperability, live first-token latency, physical-Mac usability, deployment, or known-good status. Full DS-CHG-001 remains VALIDATING.
