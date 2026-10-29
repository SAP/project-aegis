# Contributing

## Code of Conduct

All members of the project community must abide by the [SAP Open Source Code of Conduct](https://github.com/SAP/.github/blob/main/CODE_OF_CONDUCT.md).
Only by respecting each other we can develop a productive, collaborative community.
Instances of abusive, harassing, or otherwise unacceptable behavior may be reported by contacting [a project maintainer](REUSE.toml).

## Engaging in Our Project

We use GitHub to manage reviews of pull requests.

* If you are a new contributor, see: [Steps to Contribute](#steps-to-contribute)

* Before implementing your change, create an issue that describes the problem you would like to solve or the code that should be enhanced. Please note that you are willing to work on that issue.

* The team will review the issue and decide whether it should be implemented as a pull request. In that case, they will assign the issue to you. If the team decides against picking up the issue, the team will post a comment with an explanation.

## Steps to Contribute

Should you wish to work on an issue, please claim it first by commenting on the GitHub issue that you want to work on. This is to prevent duplicated efforts from other contributors on the same issue.

If you have questions about one of the issues, please comment on them, and one of the maintainers will clarify.

## Contributing Code or Documentation

You are welcome to contribute code in order to fix a bug or to implement a new feature that is logged as an issue.

The following rule governs code contributions:

* Contributions must be licensed under the [Apache 2.0 License](./LICENSE).
* Due to legal reasons, contributors will be asked to accept a Developer Certificate of Origin (DCO) when they create the first pull request to this project. This happens in an automated fashion during the submission process. SAP uses [the standard DCO text of the Linux Foundation](https://developercertificate.org/).
* Contributions must follow our [guidelines on AI-generated code](https://github.com/SAP/.github/blob/main/CONTRIBUTING_USING_GENAI.md) in case you are using such tools.

## Development

Aegis is an open-core guardrail library — code quality is enforced with linters, type checking,
and commit-message hooks.

### Setup

```bash
git clone https://github.com/SAP/project-aegis.git
cd project-aegis
pip install -e ".[dev]"
pre-commit install --hook-type pre-commit --hook-type commit-msg
```

`pre-commit install` wires up both the pre-commit checks (ruff, ruff-format, pyright,
plus stock hygiene hooks) and the `commit-msg` Conventional Commits check.

### Development workflow

- **Lint & format:** `ruff check .` and `ruff format .` (pre-commit runs both with `--fix`).
- **Type check:** `pyright` (basic mode).
- **Tests:** `pytest` (async mode is auto). Coverage: `pytest --cov`.
- Run everything the hooks would run: `pre-commit run --all-files`.

### Commit messages

Commits follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<optional scope>): <subject>
```

Allowed types: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `build`,
`ci`, `chore`, `revert`. Example:

```
feat(guard): add opt-in unicode normalization
```

The `commit-msg` hook rejects messages that don't match.

### Writing a validator

Validators are structural — implement `.name` + `async validate()` (via `BaseValidator`
for detection or `BaseMutationValidator` for mutation). Two docs cover this:

- [docs/custom-validators.md](docs/custom-validators.md) — the **contract**: protocols,
  result types, and full examples.
- [docs/validators/authoring-guide.md](docs/validators/authoring-guide.md) — the **detailed
  guide**: where code/tests/docs live, the registration chain, and the end-to-end workflow.

Key rules:

- `validate()` returns `PassedResult` or `FailedResult` only — detect, don't mutate.
- Mutation happens in `mutate()` on `MutationValidator`, driven by `MutationGuard`.
- Give each `FailedResult` a stable `error_code`.

### Documentation sync

When you change `src/`, update the matching docs under `docs/` in the same PR
(validator pages, `guard.md`/`mutation-guard.md`, `mkdocs.yml` nav, `CHANGELOG.md`).

## Issues and Planning

* We use GitHub issues to track bugs and enhancement requests.

* Please provide as much context as possible when you open an issue. The information you provide must be comprehensive enough to reproduce that issue for the assignee.
