# agent.stream() Integration

How to wire `aegis` inside the agent's `stream()` method — the layer that runs the
underlying graph and yields response items back to the executor.

Guards here run before the graph is invoked, keeping the graph itself free of guardrail
logic. The executor (the A2A layer above) handles converting guard results into A2A protocol
responses — see [AgentExecutor Integration](agent-executor.md).

The examples below use `Guard` for input (detect and reject). You can also use a
`MutationGuard` for input — for example, redacting PII from the query before it reaches
the graph. See [Standalone Usage — Guard Combinations](standalone.md#guard-combinations).

---

## Guard Initialization

Build the guard once in `__init__`, not per-request.

`ContentFilter` is a built-in validator and `MaxLength` is a custom one you write against the
contract. Aegis ships built-in validators (see [Validators](../validators/index.md)) and lets
you add your own (see [Custom Validators](../custom-validators.md)).

```python
from aegis import Guard, OnFailAction
from aegis.validators.detection import ContentFilter
from my_validators import MaxLength  # your own validator
from agent_config import BLOCKED_TOPICS


class MyAgent:
    def __init__(self):
        self.input_guard = (
            Guard("my-agent-input")
            .use(MaxLength(5000), on_fail=OnFailAction.NOOP)
            .use(ContentFilter(blocked_topics=BLOCKED_TOPICS), on_fail=OnFailAction.NOOP)
        )
```

`OnFailAction.NOOP` keeps all validators running and returns failures for the caller to
inspect — no exceptions, no implicit logging.

---

## Running the Guard and Yielding a Rejection

Run the guard at the top of `stream()`. On failure, yield a completed response item and
return — the graph is never invoked.

```python
async def stream(self, query: str, context_id: str):
    guard_result = await self.input_guard.validate(value=query)

    if guard_result.has_failures:
        failure = guard_result.failures[0]

        # Build message based on which validator fired
        if failure.error_code == "CONTENT_BLOCKED":
            message = "I'm unable to assist with that request."
        else:
            message = "That request can't be processed. Please rephrase and try again."

        yield {
            "is_task_complete": True,
            "require_user_input": False,
            "content": message,
            "metadata": {"guardrail": failure.details},
        }
        return  # Graph never runs

    # Guard passed — invoke graph
    result = await self.graph.ainvoke(build_state(query, context_id))
    yield {
        "is_task_complete": True,
        "require_user_input": False,
        "content": result["final_response"],
    }
```

Including `failure.details` in the yielded item's `metadata` lets the executor (and the
A2A caller) see structured context — e.g. `matched_topic` from ContentFilter.

---

## Full stream() Pattern

```python
async def stream(self, query: str, context_id: str):
    # 1. Input guard
    guard_result = await self.input_guard.validate(value=query)

    if guard_result.has_failures:
        failure = guard_result.failures[0]
        yield {
            "is_task_complete": True,
            "require_user_input": False,
            "content": self._rejection_message(failure),
            "metadata": {"guardrail": failure.details},
        }
        return

    # 2. Build state
    input_state = {
        "messages": [HumanMessage(content=query)],
        "thread_id": context_id,
    }

    # 3. Run graph
    yield {
        "is_task_complete": False,
        "require_user_input": False,
        "content": "Analyzing your request...",
    }

    result = await self.graph.ainvoke(
        input_state,
        config={"configurable": {"thread_id": context_id}},
    )

    # 4. Output mutation guard — redact PII and normalize tone before returning
    raw_response = result["final_response"]
    mutation_result = await self.output_guard.validate(raw_response)
    final_response = mutation_result.output if mutation_result.was_mutated else raw_response

    yield {
        "is_task_complete": True,
        "require_user_input": False,
        "content": final_response,
        "metadata": result.get("metadata", {}),
    }

def _rejection_message(self, failure) -> str:
    if failure.error_code == "CONTENT_BLOCKED":
        return "I'm unable to assist with that request."
    return "That request can't be processed. Please rephrase and try again."
```

`output_guard` is a `MutationGuard` initialised in `__init__` alongside the input guard:

```python
from aegis import MutationGuard
from my_validators import PIIRedactor  # your own MutationValidator

class MyAgent:
    def __init__(self):
        self.output_guard = MutationGuard("my-agent-output").use(PIIRedactor())
```
