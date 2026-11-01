# Scheduling watchdog implementation plan

> Execute inline under superpowers:executing-plans; use test-driven-development and the repository branch review.

**Goal:** Deliver hourly original acquisitions despite missing native trigger delivery, while keeping GitHub Actions and immutable evidence publication.

**Architecture:** A five-minute Cloudflare cron calls one SQLite-backed Durable Object. GitHub's data branch owns append-only hourly claims committed with durable acquisition starts. The object owns one durable dispatch reservation per slot. Actions performs a live pause check and rechecks the slot under evidence-writer concurrency before requesting NOAA.

**Tech stack:** Dependency-free Python pipeline; ES modules on Workers; Wrangler; Node's built-in test runner.

**Spec:** Authorized handoff in this chat, issue #1, and docs/operations.md.

## Constraints and decisions

- Keep minute-50 hourly windows, 02:00 UTC science and 14:00 UTC operations checkpoints, original receipts and frozen science unchanged.
- A slot is the latest elapsed minute-50 boundary. A writer must start within that same hourly window. Native/manual runs derive an intended slot from Actions creation time; external runs carry explicit intent. Native scheduledAt stays null because GitHub supplies no trustworthy original scheduled clock.
- Claims and starts are one Git commit before NOAA. Failed/partial/interrupted attempts consume the slot. The same Actions run cannot acquire again on rerun, even in a later window. Fresh native/external triggers for a consumed slot skip.
- Historical starts without claims count by actual start window. Do not edit historical evidence.
- The Worker checks live COLLECTION_ENABLED, exact reviewed main, and queued/running collection and reconciliation before reserving. The writer checks live COLLECTION_ENABLED again with a Variables:read-only token. Missing control access fails closed. Imports retain their existing pause-and-drain contract.
- Reserve dispatch identity/time durably before POST. Any outcome, including timeout/5xx, consumes external dispatch permission for the slot. No automatic redispatch. Next ticks reconcile start, Actions result, terminal, catalog, immutable Release and bundle hashes/lengths; acceptance still requires independent complete original-file verification.
- Workers cron, polling, dispatch, Actions creation/start and original product receipt clocks remain distinct. workflow_dispatch stays workflow_dispatch. No backfill or inferred receipts.
- Reviewed main is pinned in deployment config; an unexpected main revision halts dispatch. Writer compares external expected_commit with its actual code commit to close the dispatch ref race.
- Use existing Workers account conventions, no custom domain, public read-only status with no secrets, no public mutation routes. Missing credentials are deployment blockers, not reasons to weaken checks.

## System map

Collectors, reconciliation, artifact recovery and operator imports write data. Collection/reconciliation share evidence-writer; imports pause and drain them. Audits, replay, selection and recovery read starts, terminals and catalogs; all must tolerate additional context fields and an independent scheduling directory. The frozen collector owns raw bodies and receipt clocks. The publication layer owns checkpoints, hashes, Releases and catalogs. The watchdog observes these and never writes evidence or calls NOAA. Upgrade requires merging writer protection before enabling the Worker. Old queued workflow revisions must be drained before activation.

## Review focus

Ambiguous POST or isolate loss after reservation; pause changes while queued; delayed native/manual retries crossing a window; incomplete publication with a terminal or catalog absent; concurrent native/external claims and exact-main races.

## Tasks

- [x] 1. Write failing Python admission tests: adjacent minute-50 boundaries, malformed intent, expired intent, native/external collision, historical start collision, rerun across hours, pause/API failure, atomic start/claim before NOAA. Implement pipeline/scheduling.py, collect CLI admission, hosted claim and workflow inputs/control secret. Run Python suites.
- [x] 2. Write failing Node watchdog tests: repeated/concurrent ticks, every active writer state and pagination, live pause, reviewed main mismatch, response loss, reserve failure, failed acquisition, terminal without publication, corrupt bundle, successful verified publication and read-only entry points. Implement watchdog/core.mjs, worker.mjs and pinned Wrangler config/package/lock; add offline CI tests. Verify real Workers runtime and dry-run bundle.
- [ ] 3. Document credentials, deployment order, pause/recovery, observable status, acceptance coverage and rollout blockers. Run independent branch review, verify/fix findings, rerun suites and prepare a reviewed PR. Deploy only reviewed main with credentials, record exact code/deployment/evidence and notify the parent monitoring chat. Do not change local overlap or declare migration accepted.

## Execution ledger

Ruling: proceed through implementation under the explicit user instruction to carry the selected approach through deployment readiness and surface only real missing access/approvals; no design-choice reapproval.
Baseline: clean linked worktree from origin/main 2ae80775f6a630b1adf5706795dc607ffd16a443; main checkout preserved.
Access: Cloudflare saved OAuth expired, no Cloudflare env credentials and no repository secrets. GitHub CLI access works. Managed worktree tool rejects nested sourceRoot; use ignored project-local worktree.

Task 1 complete: guarded Python admission and atomic claim/start; 69 pipeline tests pass. Actual start rechecks expiry without backdating.
Task 2 complete: durable dispatch and publication observer; 19 Node tests pass. Configured Workers runtime exercises scheduled handler/waitUntil, local GitHub fixture, durable restart and two 32 MiB stored assets in local workerd (225 ms fixture verification in that run; production resource limits were not exercised).
Review round 1: Codex and Opus complete; Copilot unavailable. Sonnet report has incomplete consumer mapping. Fixed clock validation, stale-snapshot finalization, retry metadata, concurrent outcome loss, real-runtime fetch binding, rejection signals and lookup precision. One dispatch per slot is an explicit conservative contract, not an automatic retry policy.
Review round 2: Opus complete; Sonnet mapping remains incomplete and some findings overlook the current ambiguity error signal. Fixed pin-drift and long-pause signals, documented pre-guard rerun prohibition, empty-interrupted publication classification, stale error presentation, public status HTTP forwarding and runtime-test boundaries. Original recovery records remain independently verified; the watchdog intentionally does not interpret separate recovery catalogs.
Task 3 in progress: final review and deployment-ready PR. No active Worker deployment, source boundary or acceptance clock exists. Collector control secret and Worker token remain missing.

Final scoped fix: the third whole-branch review identified an absent closed-slot failure signal. Ruling: finish this routine watchdog fix under the explicit authorization to carry implementation through deployment readiness; no additional account or permission choice is needed. Limit further review to this delta instead of repeating the whole-repository review loop. Add a durable closed-window absence/resolution signal, preserve native/external provenance with an explicit dispatch match flag, and run regression/runtime validation plus an independent focused review. Do not claim three clean whole-branch rounds.

Final focused review: independent Codex reviewer found no findings within the closed-window/provenance delta and surrounding paths. Final checks: 90 science, 69 pipeline and 21 Node tests; configured Workers runtime with local GitHub fixture, scheduled handler/waitUntil and SQLite restart; Actionlint and whitespace checks. Final local 32 MiB fixture verification: two assets, 223 ms; no production-limit claim. No scientific source or input diff. Task 3 implementation/review is complete; PR, disabled staging and credential-dependent rollout are operational follow-up, with local overlap unchanged.
