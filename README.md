# Codebase-Wide Version Migration & Refactoring Agent

An agent that upgrades a multi-module Python repository across a breaking
library or language change. It maps every affected call site before editing,
plans a safe edit order, applies the changes in batches, runs the tests after
each batch and repairs what breaks, then emits one unified patch and a
structured log of every decision.

**Primary migration:** SQLAlchemy 1.4 to 2.0.
**Stretch migration:** Python 3.8 to 3.12 standard-library removals.

School of CSE, REVA University. Guide: Dr Nimrita Koul, Center for AI & ML.
Team and ownership: [CONTRIBUTORS.md](CONTRIBUTORS.md).
Design documents: [`docs/design/`](docs/design/).

## How it works

```
spec + repo -> M1 load spec -> M2 static analysis -> M3 plan batches
                                     |
          for each batch:  M5 edit -> M6 run lint and tests -> pass? -> next batch
                              ^                                  |
                              +------ M7 diagnose and repair <---+ fail (up to N retries)
                                     |
        M4 graph memory holds every file's state; M8 writes the log and the patch;
        M9 runs the whole thing over the benchmark corpus and computes the metrics
```

| Module | Folder | Owner |
|---|---|---|
| M1 Spec and configuration, CLI | `spec/`, `cli.py` | Adithya |
| M2 Static analysis and call-graph mapping | `analysis/` | Verril |
| M3 Planning and batching | `planning/` | Verril |
| M4 Graph memory and context | `memory/` | Prabanjan |
| M5 Edit engine | `editing/` | Adithya |
| M6 Sandboxed verification | `sandbox/` | Prajwal |
| M7 Failure analysis and self-correction | `correction/`, `llm/` | Prajwal |
| M8 Trajectory log and patch | `trajectory/` | Prabanjan |
| M9 Benchmark and evaluation | `benchmark/` | Prabanjan |
| Agent loop (integration) | `agent.py` | Verril |

All folders are under `src/migration_agent/`. The shared data shapes every
module uses are in `src/migration_agent/models.py`.

## Setup

Python 3.12 is required; Docker is needed from the sandbox milestone on.

```bash
git clone https://github.com/Verril-hash/codebase-migration-agent.git
cd codebase-migration-agent
python -m venv .venv
.venv\Scripts\activate            # Windows   (source .venv/bin/activate on Linux/macOS)
pip install -e ".[dev]"
pytest                            # the skeleton tests should pass
migrate --help
```

Using Claude Code? It reads [`CLAUDE.md`](CLAUDE.md) automatically: open a
session in the repo and ask it to "set up the project". `CLAUDE.md` also tells it
who owns which module and the rules every change must follow.

## Plan

| Week | Goal | Everyone |
|---|---|---|
| 1 | Agree the shapes in `models.py`; each owner replaces their stubs with a first working version on a tiny example | Each writes one corpus task |
| 2 | One end-to-end run on a single-file task (deterministic codemod path, no LLM) | Tests for own modules |
| 3 | Multi-file tasks: import graph, batching, verification and rollback | Second corpus task |
| 4 | Self-correction with the LLM; SQLAlchemy rules complete | Third corpus task |
| 5 | Benchmark harness over the whole corpus; metrics and logs | Fix integration bugs |
| 6 | Ablations (no analysis, no memory, no verification, LLM only); stretch migration | Results tables |
| 7 | Paper, final report and demo | Own sections |

Dates are set once the submission deadline is confirmed.

## Rules

1. Never edit a test to make it pass; the tests are the judge of a migration.
2. Never commit secrets. Copy `.env.example` to `.env`, which is gitignored.
3. Mechanical rewrites are codemods; the LLM is only for ambiguous edits and repairs.
4. Pin every dependency in `pyproject.toml`.
5. Every change goes through a reviewed pull request.
