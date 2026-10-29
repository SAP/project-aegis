# Aegis Examples

This directory contains example scripts demonstrating how to use the Aegis guardrail library.

Aegis ships the engine (`Guard` / `MutationGuard`), a set of built-in validators, and the
validator contract — use the built-ins or write your own against the contract; see
`docs/custom-validators.md`. The examples below focus on engine / A2A integration features.

## Available Examples

### 1. Homoglyph Guard Demo — `aegis_demo.py`
**File:** `aegis_demo.py`

The flagship demo (featured in the project README's gif): a `Guard` with the built-in
`ContentFilter` plus opt-in Unicode normalization blocks a homoglyph-disguised attack that a
naive blocklist misses.

### 2. History Provider — `related_tasks`
**File:** `history_provider_related_tasks.py`

Reference history provider that extracts prior turns from `context.related_tasks`.
Copy-paste into your project as-is — Aegis does not ship history providers as importable
API, so it stays backend-agnostic.

### 3. History Provider — SAP Agent Memory
**File:** `history_provider_sap_agent_memory.py`

Reference history provider that reads from the SAP Agent Memory Service. Copy-paste and
adapt; the SAP SDK is not an Aegis dependency (install `sap-cloud-sdk` yourself).

## Requirements

All examples require:
- Python >= 3.13
- the `aegis` package installed — see [Installation](../docs/installation.md) (from source until the first PyPI release)

## Running Examples

```bash
# From project root
python examples/<example_name>.py
```

## Contributing

When adding new examples:
1. Use correct import paths (`from aegis import ...`, `from aegis.validators import ...`)
2. Include a docstring with description and requirements
3. Keep examples deterministic (no external credentials required)
4. Update this README with the new example
