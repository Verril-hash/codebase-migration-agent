# Security Design

## Security objectives

1. Never commit API keys, tokens, certificates, or `.env` contents.
2. Do not execute an untrusted target repository directly on the host.
3. Keep migration edits reviewable through patches and trajectory logs.
4. Fail explicitly on invalid specifications, paths, patches, or syntax.
5. Minimize source code sent to an external LLM provider.

## Threat model

| Threat | Impact | Primary control |
|---|---|---|
| API key committed to Git | Provider account compromise | `.env` is ignored; use environment variables |
| Malicious repository setup/test code | Host compromise | Execute through the sandbox module |
| Prompt injection in source comments/docstrings | Unsafe LLM edit | Treat repository text as untrusted data; validate patches |
| LLM returns destructive or unrelated diff | Source loss or corruption | Scope patches to intended files; checkpoint and review |
| Path traversal in a patch | Modification outside target repo | Resolve and constrain patch paths to repository root |
| Malformed migration spec | Unbounded or incorrect run | Strict typed validation before analysis |
| Sensitive source sent to provider | Data disclosure | Minimize context and configure provider policy |
| Infinite repair loop | Resource exhaustion | Maximum retries, step/token budgets, oscillation detection |

## Secret handling

Copy `.env.example` to `.env`; never place real credentials in source,
documentation, logs, commits, or pull requests. The current environment
variables are:

```text
LLM_API_KEY
LLM_BASE_URL
LLM_MODEL
```

Rotate a key immediately if it is exposed. Verify changes with `git diff
-- . ':!*.lock'` and `git status --ignored` before pushing.

## Patch safety checklist

- Confirm the patch file is inside the target repository.
- Parse the resulting Python source before accepting it.
- Keep each batch checkpointed and rollback-capable.
- Run lint and tests after every batch.
- Review LLM-generated diffs before applying them in production.
- Do not treat a green test run as proof that arbitrary code is safe.

## Security review status

This document records the design controls. Sandbox isolation, LLM client
policy enforcement, path-constrained patch application, and complete
trajectory persistence must be verified when their owning modules are
implemented.
