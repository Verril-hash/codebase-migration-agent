# CLAUDE.md

Instructions for Claude Code (and any AI assistant) working in this repository.
Human contributors should read it too.

## The project

An agent that migrates a multi-module Python repository across a breaking
library or language change: it maps every affected call site, plans an edit
order, edits in batches, runs the tests after each batch, repairs failures,
and emits one unified patch plus a JSON log of every step. Primary migration:
SQLAlchemy 1.4 to 2.0. It is a four-person academic project (School of CSE,
REVA University, guide Dr Nimrita Koul).

The design is in `docs/design/` (System Design Document = `03_...pdf`, Module 2
detailed design = `04_...pdf`). Code follows that design; if the code needs to
differ, say so in the pull request.

## First-time setup

When a contributor asks to set up the project, run these in the repo root and
report the result. On Windows:

```
python --version                 # must be 3.12.x
python -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m pytest
```

On Linux or macOS use `.venv/bin/python` instead. All tests must pass before
any work starts. Docker is needed only for `src/migration_agent/sandbox/`
(Module 6); check it with `docker --version`.

## Who owns what

| Owner (GitHub) | Modules | Paths under `src/migration_agent/` |
|---|---|---|
| Verril Vaz (`Verril-hash`) | M2 static analysis, M3 planner, agent loop | `analysis/`, `planning/`, `agent.py` |
| Adithya Naik (`mradinax`) | M1 spec and CLI, M5 edit engine, SQLAlchemy rules | `spec/`, `cli.py`, `editing/`, and `specs/` at the root |
| Prajwal Ragavendra Hawaldar (`prajwal-tech07`) | M6 sandbox, M7 self-correction, LLM client | `sandbox/`, `correction/`, `llm/` |
| Prabanjan R (`prabanjan-ux`) | M4 graph memory, M8 log and patch, M9 benchmark | `memory/`, `trajectory/`, `benchmark/` |

Ask who the current user is (or read `git config user.name`) and work in
**their** folders. Do not edit another owner's module without the user
confirming that the owner agreed; open an issue or mention it in the PR
instead. Tests for a module go in `tests/test_<module>.py`.

`src/migration_agent/models.py` holds the data shapes every module shares. Change
it only when necessary, keep changes backward compatible where possible, and
point out in the PR that every owner must review it.

## Workflow

1. `git switch main && git pull`
2. `git switch -c <name>/<topic>`, for example `adithya/spec-loader`
3. Write the code **and its tests**.
4. Run the done-gate, all three must pass:
   `ruff format .`, `ruff check .`, `pytest`
5. Commit with a clear message, for example `feat(spec): validate spec files`.
6. `git push -u origin <name>/<topic>` and open a pull request to `main`
   using the PR template. One approval is required; `main` is protected and
   nobody can push to it directly.

Never push to `main`, force-push a shared branch, or merge a pull request
without an approval.

## Hard rules

1. **Never edit or weaken a test to make it pass.** In benchmark tasks the
   tests are the judge of a migration; in our own suite a failing test means
   the code is wrong.
2. **Never commit secrets.** API keys come from environment variables loaded
   from `.env`, which is gitignored; `.env.example` lists the names.
3. **All code must be original work of this team.** Do not copy code from other
   repositories or projects. Libraries listed in `pyproject.toml` are fine.
4. **Codemods first, LLM second.** A mechanical rewrite is a LibCST codemod in
   `editing/codemods/`; the language model is only for ambiguous edits and for
   repairing failures.
5. **Pin every dependency** in `pyproject.toml`, in the PR that first uses it.
6. **Tests and benchmark tasks run in the sandbox** once Module 6 exists; do
   not add code that runs untrusted repository code directly on the host.
7. Keep stubs' signatures unless the PR explains the change; other modules
   call them.

## Style

Python 3.12, type hints on public functions, a short docstring on every module
and public function, line length 100 (ruff enforces formatting and lint).
Prefer small functions and plain data classes from `models.py` over new
ad hoc dictionaries.
