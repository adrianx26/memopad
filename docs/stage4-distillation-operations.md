# Stage 4: Distillation Operations and Maintenance

**Status:** Operational hardening implemented; maintenance policy remains open
**Source:** Phase 4 in [Implementation Roadmap](implementation-roadmap.md)

## Implemented

- Scheduler events for the same project are serialized. A callback failure or
  cancellation restores cadence state and retains the triggering ingestion as
  pending, so an idempotent retry can process it rather than silently losing
  the count. `DistillationDispatcher` logs and re-raises failures; API and watch
  fire-and-forget boundaries remain responsible for keeping distillation errors
  from failing the source write.
- Idle L1 evaluation now has an atomic dispatch entry point. The filesystem
  watcher checks it periodically while running, with a maximum 30-second poll
  interval (or half the configured idle timeout when shorter), and uses the
  shared background-task concurrency limit.
- The per-project JSON watermark is written via a same-directory temporary file,
  flushed, and atomically replaced. State I/O errors and malformed state are
  surfaced instead of silently cold-starting or continuing without persistence.
  Successfully persisted watermarks remain separate from scheduler counters.

## Restart and retry semantics

Cadence counts and warmup cursors are process-local and reset on restart. They
are not durable ingestion queues. The persisted L1/L2/L3 pass watermarks make
completed passes incremental; a failed pass does not advance its watermark.
Partially completed passes may be repeated, so writes must remain idempotent.
Pending work after a process restart is not immediately dispatched solely from
the old in-memory counter; a later ingestion event or manual distillation pass
re-enters the pipeline.

## Maintenance policy boundary

No automatic decay or pruning is enabled. `decay_factor`, confidence,
`last_confirmed`, and provenance are not enough to justify deletion thresholds
without representative project data and an agreed retention policy. A follow-up
must first define eligible entity types/levels, inactivity and confidence
thresholds, provenance handling, exclusions, and recovery behavior, then
implement a dry-run report that lists exact entity IDs and source chains. L0
source files must never be automatically pruned.

## Remaining Stage 4 work

- Exercise creation, import, manual distillation, sync, and watcher triggers
  end-to-end, including cancellation and restart during large backlogs.
- Add representative-volume and cross-backend operational measurements.
- Agree on retention and decay policy before building a dry-run/pruning tool.

## Validation

The focused scheduler, distillation-service, and watch-hook selection passed
**62 tests** after the operational changes. Stage 4 changes are also included
in the combined Stage 4/5 check results recorded in the implementation
roadmap.
