# Cookbook

Integration patterns for A2A agent developers.

| Section | What it covers |
|---|---|
| [AgentExecutor Integration](cookbook/agent-executor.md) | Input guard + output mutation guard wired into the A2A `AgentExecutor`, with `TaskState` mapping and `TaskUpdater` rejection |
| [agent.stream() Integration](cookbook/agent-stream.md) | Lazy guard initialization, branching on `GuardResult`, passing guard metadata into graph state |
| [Standalone Usage](cookbook/standalone.md) | Using guards in scripts, tests, or batch jobs — interpreting `GuardResult` and `MutationGuardResult` directly |
| [Observability](cookbook/observability.md) | Soft guardrails, audit logging, inspecting all validator results |
