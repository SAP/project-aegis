# Aegis Claude Skills

This directory holds Claude Code skills for working with aegis. Each skill is a self-contained
instruction set that Claude loads on demand to perform a specialized task. (See the Claude Code
documentation on *skills* for the file format and frontmatter schema.)

## How to use

Invoke a skill in Claude Code with its slash command:

```
/<skill-name> [optional arguments]
```

For example:

```
/aegis-custom-validator detect and redact email addresses in agent output
```

List available skills at any time with `/skills`.

## Available skills

| Skill | Command | What it does |
| :--- | :--- | :--- |
| [aegis-custom-validator](./aegis-custom-validator/SKILL.md) | `/aegis-custom-validator` | Author a custom aegis validator in your own project, end to end — research, spec, implement, test, document, and review. Reads the contract from the installed `aegis` package, conforms to the `validate()` / `mutate()` contract, and keeps a human in the loop at every real design decision. Bundles ready-to-adapt templates and process references. |

## Using this skill

`aegis-custom-validator` is standalone — it authors a validator **in your own project**. It does
not need the aegis source tree; it reads the contract from the `aegis` package you install.

To add it to your repo:

1. Copy the `aegis-custom-validator/` folder into your repo's `.claude/skills/` directory.
2. Make sure the aegis package is installed in your project (add it the way your project manages
   dependencies). Its import name is `aegis`; the PyPI/distribution name may differ, so don't
   assume `pip install aegis`. The skill checks the import before doing anything.
3. Restart Claude Code (or run `/skills`) so it is picked up, then invoke `/aegis-custom-validator`.

The skill reads the validator contract from the installed `aegis` package, so it always matches the
version your project uses. The validator it produces lives in your codebase and is registered with
`.use()` on your own `Guard` / `MutationGuard`.

## Skill structure

Each skill lives in its own subdirectory:

```
<skill-name>/
├── SKILL.md          # Frontmatter + the instructions Claude follows
├── references/       # Supporting docs the skill cites (read on demand)
└── templates/        # Copy-pasteable code/doc templates the skill adapts
```

The `SKILL.md` frontmatter declares the skill name, description, argument hint, and whether it is
user-invocable.

## Contributing a new skill

1. Create a directory under `.claude/skills/<your-skill-name>/` (lowercase, hyphenated).
2. Add a `SKILL.md` with frontmatter (`name`, `description`, `argument-hint`, `user-invocable`).
   Write a trigger-heavy `description` — it is how Claude decides when to load the skill.
3. Put reference material under `references/` and code/doc templates under `templates/`.
4. Add a row to the **Available skills** table above.
5. Keep skills focused — one well-scoped task per skill beats a catch-all.
