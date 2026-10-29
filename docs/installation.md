# Installation

<!-- TODO(pre-PyPI-release): replace every `<PYPI_PACKAGE_NAME>` placeholder below with the
approved PyPI distribution name and restore Method 1 as the primary/recommended install — just
before the first PyPI release, to avoid disclosing the name early (OSPO §9). -->

`aegis` requires **`a2a-sdk`** (`>=0.3.20,<2`) and works with both the 0.3.x and 1.x majors. A single install works against either — Aegis detects the installed SDK at import time. See [the a2a-sdk v1 migration notes](a2a-v1-migration.md) for the API differences between the two.

Aegis ships the engine (`Guard`, `MutationGuard`), a set of built-in validators (see
[Validators](validators/index.md)), and the validator contract (`Validator` /
`MutationValidator` protocols, `BaseValidator` / `BaseMutationValidator`) so you can write
your own too (see [Custom Validators](custom-validators.md)).

Until the first release, install **from the repository** (Method 3) or **from source**
(Method 4). A PyPI package will be the recommended install from the first release onward
(Method 1).

## Optional extras

| Extra | Pulls in | Required for |
|---|---|---|
| `telemetry` | `opentelemetry-sdk` | Exporting OpenTelemetry traces (the core only depends on the OTEL API, which is a no-op without an SDK) |

There are four ways to consume `aegis`, depending on your context:

| # | Method | When to use |
|---|---|---|
| 1 | [PyPI](#method-1-pypi-recommended) | Standard install |
| 2 | [Vendored wheel](#method-2-vendored-wheel) | Air-gapped environments or no network access at install time |
| 3 | [Git URL](#method-3-git-url) | When you need the latest unreleased changes |
| 4 | [From source](#method-4-from-source-contributors) | Contributors / active development |

---

## Method 1 — PyPI (recommended)

> **Available from the first release.** Until then, use Method 3 or 4. The PyPI distribution
> name is published with the first release; the `<PYPI_PACKAGE_NAME>` placeholder below marks
> where it goes.

```bash
pip install <PYPI_PACKAGE_NAME>
```

Pin it in `requirements.txt`:

```txt
<PYPI_PACKAGE_NAME>==1.0.0
requests==2.31.0
# ... other packages
```

```bash
pip install -r requirements.txt
```

If your project uses `uv`:

```bash
uv add <PYPI_PACKAGE_NAME>
```

In a `Dockerfile`, install from the committed `requirements.txt` — no credentials needed:

```dockerfile
COPY requirements.txt .
RUN pip install --no-cache -r requirements.txt
```

---

## Method 2 — Vendored wheel

Use this for air-gapped environments or anywhere without network access at install time. Committing the `.whl` into your repository means nothing is fetched at install time, in any environment.

**Step 1 — Download the wheel from a GitHub Release:**

```bash
gh release download v1.0.0 \
  --pattern '*.whl' \
  --repo SAP/project-aegis \
  --dir vendor/wheels/
```

Or copy the `.whl` file manually from the release assets.

> **Note:** You can replace `v1.0.0` with `--latest` to always download the most recent release. However, since the `.whl` filename embeds the version number, you will still need to update the filename reference in `vendor/wheels/` and `requirements.txt` after each upgrade.

**Step 2 — Add it to your repo layout:**

```text
your-app/
  vendor/
    wheels/
      <pypi_package_name>-1.0.0-py3-none-any.whl
  requirements.txt
  Dockerfile
```

**Step 3 — Reference it in `requirements.txt`:**

```txt
./vendor/wheels/<pypi_package_name>-1.0.0-py3-none-any.whl
```

**Step 4 — Install locally or in Docker:**

```bash
# Local development
pip install -r requirements.txt
```

```dockerfile
# Docker build
COPY requirements.txt .
COPY vendor/wheels/ ./vendor/wheels/

RUN pip install --no-cache -r requirements.txt
```

The same `requirements.txt` works in both contexts — no token injection needed anywhere.

---

## Method 3 — Git URL

Use this when you need changes that haven't been released yet:

```bash
pip install git+https://github.com/SAP/project-aegis.git
```

---

## Method 4 — From source (contributors)

```bash
git clone https://github.com/SAP/project-aegis.git
cd project-aegis
pip install -e ".[dev]"
```

---

## Updating to a new version

```bash
pip install --upgrade <PYPI_PACKAGE_NAME>
```

If using the vendored wheel approach: download the new `.whl` from the GitHub Release, replace the old one in `vendor/wheels/`, update the path in `requirements.txt` if the version changed, and commit both together.

---

## Releasing a new version (maintainers only)

Releases publish to PyPI via [OIDC trusted publishing](https://docs.pypi.org/trusted-publishers/). The automated release workflow is configured once the project is public; once in place, it runs on every published GitHub Release.

**Step 1 — Bump the version in `pyproject.toml`:**

```toml
[project]
version = "1.0.0"
```

**Step 2 — Tag and publish a GitHub Release:**

The release tag must be `v<version>` (e.g. `v1.0.0`) — the release workflow validates that the tag matches the package version in `pyproject.toml`, then triggers `uv build` and the upload to PyPI; no local credentials are involved.
