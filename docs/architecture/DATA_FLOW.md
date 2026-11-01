# Data Flow

## Overview

```mermaid
flowchart LR
    SpecFile[(Migration JSON)] --> Loader[M1 loader]
    Source[(Target repository)] --> Scanner[M2 scanner/parser]
    Loader --> SpecModel[MigrationSpec]
    SpecModel --> Matcher[M2 matcher]
    Scanner --> Parsed[Parsed modules]
    Parsed --> Matcher
    Matcher --> Analysis[AnalysisResult]
    Analysis --> Planner[M3 planner]
    Analysis --> Memory[M4 graph memory]
    Planner --> Batches[BatchPlan]
    Batches --> Engine[M5 edit engine]
    Memory --> Engine
    Engine --> Patch[Patch]
    Patch --> Verify[M6 verification]
    Verify --> Report[TestReport]
    Report --> Diagnose[M7 diagnosis]
    Diagnose --> Repair[FixRequest]
    Repair --> Engine
    Analysis --> Log[M8 trajectory log]
    Report --> Log
    Patch --> Output[(Unified patch and JSON log)]
```

## Data stores and artifacts

| Artifact | Producer | Consumer | Sensitivity |
|---|---|---|---|
| Migration JSON | Developer | M1, M2, M5 | Project configuration |
| Parsed AST/module data | M2 | M2, M3, M4 | Source-derived |
| `AnalysisResult` | M2 | M3, M4, M5, M7 | Source-derived |
| `BatchPlan` | M3 | Agent loop | Operational |
| `Patch` | M5/M7 | Verification, M8 | May contain source code |
| `TestReport` | M6 | Agent, M7, M8 | Test output may contain paths |
| Graph memory | M4 | M5/M7 | Source metadata |
| Trajectory log | M8 | Developer/evaluation | Operational evidence |
| Benchmark metrics | M9 | Reports | Aggregate results |

## Trust boundaries

```mermaid
flowchart TB
    UserZone[Developer workstation] -->|trusted configuration| AgentZone[Migration agent]
    AgentZone -->|untrusted source repository| SandboxZone[Isolated sandbox]
    SandboxZone -->|test reports only| AgentZone
    AgentZone -->|sanitized request| LLMZone[External LLM provider]
    LLMZone -->|patch proposal| AgentZone
    AgentZone --> OutputZone[Patch and logs]
```

Untrusted repository code must run in the sandbox once M6 is available. Source
content sent to an external provider should be minimized and reviewed for
secrets before transmission.
