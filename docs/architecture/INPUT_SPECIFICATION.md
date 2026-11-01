# Input Specification

## Migration specification

The CLI accepts a JSON migration specification through `--spec`. A minimal
specification has this shape:

```json
{
  "spec_id": "sqlalchemy-1.4-to-2.0",
  "title": "SQLAlchemy 1.4 to 2.0",
  "target_version": "2.0",
  "deprecated_patterns": [
    {
      "pattern_id": "declarative_base_import",
      "ast_matcher": "sqlalchemy.ext.declarative.declarative_base",
      "description": "declarative_base moved to sqlalchemy.orm"
    }
  ],
  "replacement_rules": [
    {
      "pattern_id": "declarative_base_import",
      "strategy": "codemod",
      "ref": "sqla_declarative_base"
    }
  ],
  "oracle": "sqlalchemy_text_oracle"
}
```

## Field rules

| Field | Type | Required | Meaning |
|---|---|---:|---|
| `spec_id` | string | yes | Stable identifier |
| `title` | string | yes | Human-readable migration name |
| `target_version` | string | yes | Destination library/language version |
| `deprecated_patterns` | array | yes | APIs or patterns to find |
| `pattern_id` | string | yes | Unique pattern identifier |
| `ast_matcher` | string | yes | Canonical API or AST matching target |
| `description` | string | no | Explanation for reviewers |
| `replacement_rules` | array | yes | How each pattern is migrated |
| `strategy` | `codemod`/`llm` | yes | Deterministic or model-assisted edit |
| `ref` | string | yes | Codemod name or LLM prompt reference |
| `oracle` | string | no | Completeness detector name |

Every replacement rule must reference a declared `pattern_id`. Duplicate
pattern or rule IDs are invalid.

## CLI inputs

```text
migrate plan --spec <spec.json> --repo <repository>
migrate run --spec <spec.json> --repo <repository> [--resume] [--dry-run]
migrate status --repo <repository>
migrate evaluate [--tasks <corpus-directory>]
```

Paths must point to an existing specification file or repository directory.
