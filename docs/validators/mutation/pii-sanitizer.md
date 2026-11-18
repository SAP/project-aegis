# PiiSanitizer

Detect and redact Personally Identifiable Information (PII) — protecting emails, phone numbers, credit card numbers, IBANs, Social Security numbers (SSNs), and IP addresses.

## Description

`PiiSanitizer` scans content for sensitive entity patterns and replaces unmasked private information with clean, structured placeholders (such as `<EMAIL>`, `<PHONE>`, `<CREDIT_CARD>`, `<IBAN>`, `<SSN>`, or `<IPV4>`). It defends against data exfiltration, accidental privacy leakage in model outputs, and compliance violations under GDPR, CCPA, and enterprise privacy standards.

It is a **detect-and-fix** validator:
- Under **`MutationGuard`**, it sanitizes content in-place, ensuring downstream storage and display contain no sensitive data.
- Under **`Guard`**, it runs detect-only, rejecting any input or output that contains unredacted PII entities.

Detection and repair share the same definition of safe: content is clean when no configured PII patterns remain unmasked.

## Key Features

- **Luhn Algorithm Check:** Credit card candidates are validated via the Luhn checksum (mod 10). Random 16-digit order or tracking numbers that fail the Luhn check are preserved, avoiding false positives.
- **Configurable Entities:** Selectively enable specific PII entity types via the `entities` parameter.
- **Customizable Mask Format:** Tailor replacement tokens to match downstream parser expectations (e.g., `[REDACTED_{entity}]` or `<{entity}>`).
- **Zero Sensitive Data Leakage:** Validation failure `details` contain only entity names, match counts, and affected part metrics — never the raw sensitive values.

## Usage

### In-Place Sanitization with `MutationGuard`

```python
from aegis import MutationGuard
from aegis.validators.mutation import PiiSanitizer

guard = MutationGuard("output").use(PiiSanitizer())
result = await guard.validate("Contact Alice at alice@example.com or +1-555-0199.")

print(result.was_mutated)  # True
print(result.output)       # "Contact Alice at <EMAIL> or <PHONE>."
```

### Detect-Only with `Guard`

```python
from aegis import Guard
from aegis.validators.mutation import PiiSanitizer

guard = Guard("input").use(PiiSanitizer())
result = await guard.validate("My SSN is 123-45-6789.")

if result.has_failures:
    print(result.failures[0].error_code)  # "PII_DETECTED"
    print(result.failures[0].details)     # {'detected_entities': ['SSN'], 'total_matches': 1, 'affected_parts': 1}
```

### Selective Entity Redaction & Custom Masks

```python
from aegis import MutationGuard
from aegis.validators.mutation import PiiEntity, PiiSanitizer

# Only redact emails and phone numbers, with custom bracketed masks
sanitizer = PiiSanitizer(
    entities={PiiEntity.EMAIL, PiiEntity.PHONE},
    mask_format="[REDACTED_{entity}]"
)
guard = MutationGuard("output").use(sanitizer)
result = await guard.validate("Email info@company.com or host 10.0.0.1.")

print(result.output)  # "Email [REDACTED_EMAIL] or host 10.0.0.1."
```

## Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `entities` | `Set[PiiEntity] \| None` | `DEFAULT_PII_ENTITIES` | The set of PII entity types to detect and redact. |
| `mask_format` | `str` | `"<{entity}>"` | Replacement format template. Token `{entity}` is replaced by the entity name. |
