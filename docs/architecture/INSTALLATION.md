# Installation Guide

## Requirements

- Python 3.12 or newer supported by the project configuration
- Git
- Docker for the sandbox milestone and repository-code verification
- A Groq-compatible API key only when the LLM client is enabled

## Windows

From PowerShell in the repository root:

```powershell
python --version
python -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m pytest
```

Optional environment configuration:

```powershell
Copy-Item .env.example .env
```

Set the values in `.env` without committing the file:

```text
LLM_API_KEY=<provider key>
LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_MODEL=<provider-supported model>
```

## Linux/macOS

```bash
python3 --version
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m pytest
cp .env.example .env
```

## Verify the installation

```text
migrate --help
```

Expected validation commands before opening a pull request:

```text
ruff format .
ruff check .
pytest
```

## Current implementation note

The test suite and CLI help are available. The complete migration workflow
depends on the remaining module implementations, including planning, sandbox
verification, graph memory, trajectory output, and the LLM client. Do not
interpret a configured API key as proof that model calls are active.

## Troubleshooting

### `migrate` is not recognized

Activate the virtual environment or invoke the installed executable directly:

```powershell
.venv\Scripts\Activate.ps1
migrate --help
```

### `No module named pytest`

Install the development extra from the repository root:

```text
python -m pip install -e ".[dev]"
```

### Tests fail after a Python upgrade

Use the project’s supported Python version, recreate `.venv`, and reinstall
the pinned dependencies. Do not bypass failing tests by editing them.
