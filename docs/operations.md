# Operations

## Hosted workflows

`collect.yml` runs one finite acquisition hourly at minute 50 on a GitHub-hosted Linux runner. It also supports manual dispatch. Each NOAA product request uses the same explicit 30-second timeout as the deployed legacy heartbeat. `COLLECTION_ENABLED=true` enables acquisition and scheduled development evaluation; manual evaluation remains available while acquisition is paused. All write workflows require this repository's `main` ref; acquisition and reconciliation share one concurrency group and never cancel an active writer. Git commits use the job's short-lived `GITHUB_TOKEN` via `gh`, without embedding credentials in files or URLs.

`audit.yml` checks hourly at minute 25 and verifies the latest successful observation archive. The monthly first-day 08:17 UTC run verifies the full archive. It maintains one `Collection health` issue. `reconcile.yml` repairs missing catalog entries from existing Releases without NOAA requests. `restore.yml` accepts an exact data commit and verifies the full public archive and retained development reference.

Schedules can be delayed or dropped, and public-repository schedules can be disabled after 60 days without activity. Actual start/receipt clocks are authoritative. Missing minute-50 windows are scheduling/acquisition gaps, not proof of a NOAA outage. An audit cannot notify during a GitHub-wide outage; failed audit workflows are themselves actionable. Re-running a collection job makes a new attempt with new receipt times. Reconcile saved bytes before re-fetching when recovering a failed publication.

## Commands

Use Linux or macOS, Python 3.12+ and authenticated `gh` with access to this public repository. No Python dependencies are required. Use a fresh `--work` directory for each independent operation; it contains disposable branch checkouts and receipts. Preserve a failed work directory until recovered.

```sh
gh workflow run collect.yml --repo openwatersio/noaa-water-level-research
gh workflow run reconcile.yml --repo openwatersio/noaa-water-level-research
python -B -m pipeline --work /tmp/noaa-restore restore --data-sha FULL_DATA_COMMIT --output /tmp/restored
python -B -m pipeline.replay --root /tmp/restored
python -B -m pipeline --work /tmp/noaa-audit audit --full --notify
```

Operator imports require the repository variable `COLLECTION_ENABLED=false` and all queued or active collection/reconciliation jobs to finish. Do not dispatch reconciliation during an operator import. Schedule the final delta import between hourly slots, re-enable collection immediately afterward, and report any missed slot rather than hiding it. A draft import is recovered only when its operator explicitly names it in the recovery workflow's `stopped_import_tag` input, confirming the import has stopped. Hosted drafts require their original Actions run to have completed. A malformed orphan is reported without blocking independent recoveries. A completed run with no remaining Release evidence can be acknowledged as an unrecoverable gap; no receipt is reconstructed.

For saved failure artifacts, use `recover-artifact --source RECEIPTS_DIRECTORY --source-batch ORIGINAL_BATCH_ID` with a fresh work directory. It verifies the original completed Actions run against the durable start record and preserves GitHub origin, rather than relabeling these bytes as legacy.

The `import --source FROZEN_DIRECTORY` command accepts only original-layout `ticks`, `observations` and `forecasts` JSON/binary files. Snapshot the local archive first; compare the entire source inventory before and after import and after a clean remote restore. Imports preserve original metadata byte for byte, including public NOAA response headers (which may contain expired load-balancer cookies, network-location metadata and historical local error paths). No authentication tokens, correspondence or unrelated local files belong in this archive. A final delta import is required after cutover.

## Evaluation

A scheduled daily development refresh resolves and records an exact data commit. Automatic development refreshes stop after October 31, 2026. Operational collection and audits continue independently. Formal evaluation is explicit and remains subject to the unchanged scientific phase/date/width/truth-vintage gates in `tools/etss/report.py`.

Manual `evaluate.yml` requests are JSON, for example:

```json
{"data_sha":"FULL_DATA_COMMIT","phase":"development","first":"2026-10-14","last":"2026-10-16","truth_as_of":"2026-10-17T01:47:57.294377Z"}
```

For calibration use `phase: calibration` and the protocol's fixed November calendar. For a width freeze add `freeze_widths: true` within the preregistered December 3 13:00 to December 4 02:00 UTC window. For confirmation supply `widths_asset` (integer Release asset ID), `widths_sha256` (hash of original JSON), `calibration_asset` (gzipped report asset ID), and `calibration_sha256` (hash of uncompressed report JSON). The downloader verifies hashes before invoking the original phase gates. Formal requests require both `data_sha` and `truth_as_of`. Widths must belong to an immutable Release published inside the freeze window; its provenance must bind the exact calibration report. Local CLI confirmation additionally requires `--widths-release RELEASE_TAG`. Full CLI equivalents are available through `python -m pipeline evaluate --help`.

Reports publish `report.json.gz` and `provenance.json` to immutable `report-...` Releases; summaries and provenance go to `reports`. A report names its data commit, code commit, original science/input pin, truth snapshot, protocol hash, transition hash and catalog hashes. The frozen original source files remain unchanged. The replay comparison ignores only absolute evidence-directory prefixes and compares selected receipt identities, clocks, hashes, predictions, truth, labels, scores and verdict.

## Migration and research dates

Until `protocols/collector-transition.json` publishes a future UTC boundary, GitHub receipts are shadow evidence and primary selection uses local-origin receipts. Maintain the old collector through at least 48 hours of verified hosted overlap covering the daily 02:00 UTC scientific decision cutoff and the 14:00 UTC operational coverage checkpoint. Audit all missing or late items; different acquisition times need not produce identical bodies.

The source-boundary document must include `publicationTag` and be uploaded byte for byte as `collector-transition.json` to an immutable Release before `boundaryAt`. Then commit that exact document to main. The hosted report verifies the Release publication clock and bytes; a retroactive boundary is rejected. Use the `pipeline` evaluation entry point for official reports: the preserved low-level scientific CLI does not apply hosted provenance rules.

Before handoff: verify a real scheduled collection and audit, immutable assets, interrupted-publication recovery, full legacy restoration and replay. Publish a future boundary after those checks. Local receipts before that boundary and hosted receipts at/after it are eligible; compare each member's receipt time. Keep all shadow evidence. After the boundary disable the old collector, let any in-flight attempt finish, import the final delta, and verify the next hosted run and final inventory before declaring migration complete.

Calibration remains November 1–30, widths freeze December 3–4, confirmation December 4–January 31. Migration does not imply forecast accuracy or production acceptance.

Published archive restoration uses unauthenticated public Release URLs, so full-archive downloads do not consume the workflow token's REST API budget. Draft checkpoints use authenticated read-back. Concurrency can coalesce pending scheduled runs; health reports preserve missing-window diagnostics.

Collection has a 20-minute step deadline inside a 30-minute job, reserving time for failure-artifact upload. Cancellation upload is best effort; a hard runner loss can still lose bytes after the last durable checkpoint. The durable start makes that incomplete attempt visible. Full restores download at most four bundles concurrently, with every hash checked. Synthetic `archive-test-*` and `receipts-test-*` Releases are excluded from research reconciliation.

The original `tools/etss/README.md` is retained with the frozen source; its heartbeat and default-timeout examples describe the predecessor. Use this document for hosted operations. Public-publication inspection is an operator prerequisite for imports, not a promise that filename checks detect secrets.

A full archive verification failure is saved and remains an actionable health problem until a later full verification succeeds. Public archive downloads retry transient network/5xx errors at most twice; missing assets and hash mismatches fail closed. The full audit has a 120-minute job budget. Import inventories are committed before upload, retained in the Release, and checked before publication and again during recovery. Unexpected files stay in their existing draft for inspection; they are never silently cataloged as the declared import.

Health audits remain enabled when collection is paused, so a forgotten re-enable becomes visible. A failed per-product checkpoint stops further NOAA requests for that tick; this preserves the requirement to make each product durable before fetching the next. Reconcile saved bytes first. Pending manual reconciliation can also be replaced by a queued collection: check the dispatched run completed. The 48-hour overlap is an operator acceptance check with evidence recorded in issue #1; the boundary validator proves prior publication, not completion of that review.
