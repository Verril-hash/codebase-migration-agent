# Sequence Flows

## Planned end-to-end migration

```mermaid
sequenceDiagram
    actor Developer
    participant CLI
    participant Spec as Spec Loader
    participant Analyzer
    participant Planner
    participant Agent
    participant Editor
    participant Sandbox
    participant Log

    Developer->>CLI: migrate run --spec ... --repo ...
    CLI->>Spec: load_spec(path)
    Spec-->>CLI: MigrationSpec
    CLI->>Agent: run_migration(RunConfig)
    Agent->>Analyzer: analyze(spec, repository)
    Analyzer-->>Agent: AnalysisResult
    Agent->>Planner: plan(import_graph, impact_set)
    Planner-->>Agent: BatchPlan
    loop For each batch
        Agent->>Editor: apply codemods / request patch
        Editor-->>Agent: Patch
        Agent->>Sandbox: run lint and tests
        Sandbox-->>Agent: TestReport
        Agent->>Log: record batch outcome
    end
    Agent-->>CLI: success or failure
    CLI-->>Developer: status and output artifacts
```

## Deterministic codemod

```mermaid
sequenceDiagram
    participant Agent
    participant Engine as Edit Engine
    participant Registry as Codemod Registry
    participant CST as LibCST
    participant File

    Agent->>Engine: apply_codemod(rule, path)
    Engine->>Registry: resolve rule.ref
    Registry-->>Engine: transformer class
    Engine->>File: read source
    Engine->>CST: parse and visit module
    CST-->>Engine: transformed source
    alt Source changed
        Engine->>File: write transformed source
        Engine-->>Agent: Patch(diff, CODEMOD)
    else No matching node
        Engine-->>Agent: None
    end
```

## Failure repair loop

```mermaid
sequenceDiagram
    participant Agent
    participant Sandbox
    participant Diagnose
    participant LLM
    participant Git

    Agent->>Sandbox: run tests
    Sandbox-->>Agent: failing TestReport
    loop Until green or retry limit
        Agent->>Diagnose: diagnose(report, analysis, memory)
        Diagnose-->>Agent: FixRequest
        Agent->>LLM: propose_fix(context, request)
        LLM-->>Agent: Patch
        Agent->>Agent: validate patch and detect oscillation
        Agent->>Sandbox: rerun tests
    end
    alt Still failing
        Agent->>Git: rollback batch checkpoint
    end
```

The repair and sandbox sequences describe the target architecture. Their
availability depends on the corresponding module implementations.
