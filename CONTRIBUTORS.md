# Contributors

**Project:** Codebase-Wide Version Migration & Refactoring Agent
**Problem statement:** CAIML_Nimrita_Koul_3_PS_209
**Guide:** Dr Nimrita Koul, Center for Artificial Intelligence & Machine Learning
**Institution:** School of CSE, REVA University

The design this code implements is our own: the Literature Survey and
Requirement Analysis (Task 1), the System Design Document (Task 2) and the
Module 2 Detailed Design (Task 3), all in [`docs/design/`](docs/design/).

## Team and ownership

The work is split into four equal parts by module of the System Design Document.

| Name | SRN | GitHub | Area | Modules | Code |
|---|---|---|---|---|---|
| Verril Vaz | R23EN179 | [@Verril-hash](https://github.com/Verril-hash) | Analysis, planning and integration; PR review | M2 Static analysis, M3 Planner, agent loop | `analysis/`, `planning/`, `agent.py` |
| Adithya Naik | R23EF014 | [@mradinax](https://github.com/mradinax) | Migration knowledge and editing | M1 Spec and CLI, M5 Edit engine, SQLAlchemy rules | `spec/`, `cli.py`, `editing/`, `specs/` |
| Prajwal Ragavendra Hawaldar | R23EF183 | [@prajwal-tech07](https://github.com/prajwal-tech07) | Execution and recovery | M6 Sandbox verification, M7 Self-correction, LLM client | `sandbox/`, `correction/`, `llm/` |
| Prabanjan R | R23EF181 | [@prabanjan-ux](https://github.com/prabanjan-ux) | State, evidence and evaluation | M4 Graph memory, M8 Logs and patch, M9 Benchmark | `memory/`, `trajectory/`, `benchmark/` |

All code paths are under `src/migration_agent/`. Ownership is enforced for review
by [`.github/CODEOWNERS`](.github/CODEOWNERS).

**Shared by all four, equally:**

* each member writes 3 to 4 benchmark tasks in `corpus/` for their own area;
* each member writes the tests for their own modules;
* each member writes their own section of the paper and the final report;
* each member reviews the others' pull requests.

`src/migration_agent/models.py` is the shared contract. Changes to it need a
review from every owner.

## How we work

* Branch from `main` as `<name>/<topic>`, for example `verril/callgraph`.
* Every change reaches `main` through a pull request with one approval; nobody
  pushes to `main` directly.
* Use **Squash and merge**.
* Before asking for review, run: `ruff format .`, `ruff check .`, `pytest`.
* Commit your own work from your own account.
* Never edit a test to make it pass, and never commit secrets (`.env` is
  gitignored; copy `.env.example`).
