# Reviewing a computation source

An editor can explicitly read the complete computation source while keeping the configured calculation request. The review shows the shared revision, complete input version, adoption version, read time and business changes. Parameters, demands, actual cumulative inputs, locks, plan reset, preferences and adopted baseline remain in the complete received source. The main review uses business values; a separate JSON export retains every source field.

The source stays separate from the displayed shared plan. A changed revision, adoption or planning window requires loading and reviewing the complete shared plan before another source read. Submission checks all current source versions again. A conflict does not silently substitute another version or submit automatically. An unknown submission retains its original UUID for verification.

`GET /jobs/source` is read-only and returns the complete typed snapshot with its revision/input/adoption pins. It does not evaluate the displayed output. `POST /jobs` accepts optional `reviewed_as_of` and rejects a production-date change between review and submission. It freezes the server's accepted submission time. For example, a source read at08:01:00 and an accepted submission at08:01:05 retain those two literal times; the latter is both the job's `created_at` and frozen snapshot's `time.as_of`. The complete non-time source and version pins must still match. An original accepted UUID replay returns its original result and time.

Fixed-clock acceptance continues to compare complete snapshots exactly, including time. Advancing-clock checks retain both raw source snapshots and independent public job metadata. An absent or null review time preserves the legacy canonical command shape and accepted UUID identity.

The small browser regression exercises source conflicts, visible old/new values, explicit review, native candidate provenance, all33 objective coordinates and restoration. It does not establish the fixed-scale timing gates, five sustained editors or final business acceptance.
