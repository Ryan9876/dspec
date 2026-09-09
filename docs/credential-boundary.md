# DS-CHG-001 credential failure boundary

Requirement: NFR-2.1 and constitution section 2.3 prohibit credentials in responses and require protected storage.

Reproduction found that invalid API-key types caused FastAPI/Pydantic to echo the raw rejected input. Provider/storage exceptions could also echo arbitrary credentials through REST, SSE, or persisted review errors. Fallback storage wrote a predictable temporary file before setting its mode to 0600.

Corrections:
- Request validation returns only field location, error type, and a fixed message; no raw input or validator context.
- Provider-facing failure responses use actionable fixed messages and retain existing error IDs/state-preservation contracts. Raw provider exceptions are not returned or persisted as review content.
- Fallback credentials use a unique temporary file created as 0600 before the first write, followed by flush/fsync and atomic replacement. Replacement failures preserve the prior credential file and remove the temporary file. Legacy fallback files are repaired to 0600 before reading, and symlinked credential files are rejected.

`tests/test_credential_boundary.py` verifies malformed-body variants, six provider/error paths, permissions before writing, legacy-permission repair, symlink rejection, and replacement failure recovery, using fake credentials only. Browser smoke also verifies that a rejected cloud key is purged from the write-only client field.

No new credential store, model endpoint, schema migration, or deployment is introduced. Physical macOS Keychain and live-provider validation remain separate gates.
