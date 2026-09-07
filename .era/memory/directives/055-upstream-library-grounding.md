---
category: upstream-library-grounding
title: External Grounding for Library Versions
order: 55
---

## External Grounding for Library Versions

LLM training data is stale by months at minimum. Recommending a dependency, asserting that a version is "current," or scoping a stack from memory routinely surfaces outdated or deprecated libraries — and tool-reported confidence ("`react-query` is the standard") is not evidence. Before any claim about a library's existence, currency, or maintenance status, ground externally.

### When this applies

Trigger external grounding before any of the following:

- Scoping a new project or feature that names specific libraries, frameworks, or SDKs
- Adding, removing, or changing a dependency in `package.json`, `requirements.txt`/`pyproject.toml`, `go.mod`, `Cargo.toml`, `Gemfile`, `composer.json`, or equivalent
- Recommending an upgrade, downgrade, or replacement of an existing dependency
- Stating that a library is "the current standard," "deprecated," "maintained," or any version-sensitive claim
- Answering "what version are we on / should we be on" for a dependency in this repo

If none of these apply (e.g., editing application code that imports an already-pinned library at its existing version), grounding is not required.

### How to ground

Use **Context7 first** when available. It is configured as an MCP server by `era-code init` and pre-allowlisted for most agents. Resolve the library, then read the docs for the version that's actually current — not the version you remember.

If Context7 is unavailable, unauthenticated, or doesn't index the library, fall back to the canonical package registry:

| Ecosystem | Command |
|---|---|
| npm / yarn / pnpm | `npm view <pkg> version` (latest), `npm view <pkg> versions --json` (all), `npm view <pkg> deprecated` |
| Python (PyPI) | `pip index versions <pkg>` or `curl -s https://pypi.org/pypi/<pkg>/json \| jq '.info.version, .info.yanked_reason'` |
| Go modules | `go list -m -versions <module>` |
| Rust (crates.io) | `cargo search <crate>` or `curl -s https://crates.io/api/v1/crates/<crate> \| jq '.crate.max_stable_version'` |
| Ruby | `gem search -r ^<gem>$` |
| Java (Maven Central) | `curl -s "https://search.maven.org/solrsearch/select?q=g:<group>+AND+a:<artifact>&rows=1&wt=json"` |

Registry queries are authoritative — they hit the source — but only cover existence, current version, and deprecation flags. Use Context7 for "is this still the right library for this job" questions; use the registry for "what version is current and is it deprecated."

### What to report

When you ground a library claim, **cite the resolved version inline** with the claim. Examples:

- "Using `@tanstack/react-query` v5.62.0 (latest per Context7, 2026-05-19)."
- "Pinned to `pydantic` 2.10.x (current major per `pip index versions`)."
- "`request` is deprecated (npm flag, last published 2020) — recommend `undici` or native `fetch` instead."

If grounding failed (Context7 down, registry unreachable, library not indexed), **state that explicitly** rather than falling back to memory:

- "Context7 returned no entry for `<lib>` and `npm view` failed — I am not confident in the current version; please confirm before I pin it."

Silent guesses about library currency are the failure mode this directive exists to prevent. A loud "I don't know" is always preferable.

### Anti-patterns

1. **Pinning from memory.** Writing `"react-query": "^3.39.0"` because that's what the training data remembers, when v5 has been the recommended package (`@tanstack/react-query`) for two years.
2. **Trusting a `package.json` already in the repo as "current."** It may be months stale. If the user asks whether dependencies are up to date, ground each one — don't read the existing pin and call it current.
3. **Citing without grounding.** Claiming "this is the latest" without having actually run Context7 or a registry query in the current session. Either ground it or hedge it.
4. **Skipping grounding for "obvious" choices.** Popular libraries change names, split packages, and deprecate fastest. `request`, `moment`, `node-sass`, `enzyme`, `tslint` all looked obvious at the time.
