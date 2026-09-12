---
category: upstream-git-discipline
title: Git Discipline
order: 70
---

## Git Discipline

Git actions are visible to other people: collaborators, CI, deploy systems, and external services. They are the easiest way to cause harm that cannot be quietly undone. Before performing **any** git action against a repository, the agent MUST verify that the action is consistent with that repository's directives.

### Always check directives before acting

Before any of the following, the agent MUST first read `.era/memory/directives.md` (or the unbuilt `.era/memory/directives/` files if the assembled doc is missing) for the repo being operated on:

- `git push` (any branch, any remote)
- `git commit` to a branch other than the agent's working feature branch
- `git merge` or `git rebase` involving a protected or deployment branch
- Creating, renaming, or deleting branches
- Force operations (`git push --force`, `git reset --hard`, `git branch -D`)
- Creating tags or GitHub releases
- Any `gh` command that writes to shared state other than pull requests (creating issues, dispatching workflows, editing releases)

Opening, updating, and closing pull requests are **not** on this list — see "Pull requests" below. Workflow dispatch stays on the list (the directive read tells you what the workflow deploys), and additionally requires explicit per-dispatch human approval — see "Workflow dispatch" below.

If the repo has `.era/memory/directives.md`, that file is the load-bearing one — it is the assembled, agent-facing form of the per-category files. Read it whole, not in pieces, before the first git action of a session and again whenever the working repo changes.

### What to look for

When reading directives prior to a git action, the agent MUST identify:

1. **Branch flow.** Which branch is the default for feature work? Which branches are deployment-gated? Is there a promotion workflow that replaces direct pushes? On Era-fleet repos the answer is the **trunk model**: `main` is the only persistent branch — feature branches off `main`, PR back to `main`; a `develop` branch is a cutover anomaly to flag, never a PR base (see the deployment directive where present).
2. **Protected branches.** Which branches must never receive a direct push? Which require PR review?
3. **Commit/PR conventions.** Are there required prefixes, sign-off, or co-author lines? Are squash merges required?
4. **Release triggers.** Does merging into a particular branch trigger a deploy, a TestFlight upload, an App Store submission, or a production rollout?
5. **Tag policy.** Are tags load-bearing for release workflows?

If the repo has no `.era/memory/directives/` (uninitialized) or the directives are empty placeholders, fall back to the constitution's Branch Discipline principle: create a fresh feature branch, never push to `main`/`master` directly, and surface the absence of project directives to the user.

### When directives conflict with the request

If the user requests an action that the directives forbid (e.g. "push directly to `production`" on a repo where `production` is App-Store-triggering), the agent MUST:

1. State the conflict explicitly, quoting the relevant directive.
2. Propose the compliant alternative (e.g. "use the `promote.yml` workflow", "open a PR against `main` first").
3. Wait for explicit override. A user "yes" to a quoted directive override is the same as `/era-directives` — record it, but do not infer authorization from silence.

The constitution still wins: even an explicit user override cannot violate constitutional principles (no force push to shared branches, no exposed secrets, no removal of `.git`).

### Pull requests: open freely, never merge autonomously

Opening, updating, and closing pull requests is **not** directive-gated. Creating a PR is the standard, reversible way to propose reviewable change — the agent may run `gh pr create` / `gh pr edit` / `gh pr close` (and equivalent GitHub PR commands) without a preceding directive read, subject to the branch and push rules above.

**Merging is the exception, and it is absolute: the agent MUST NOT merge a pull request autonomously.** Every merge — `gh pr merge`, the API or merge-button equivalent, or any direct action that lands a PR's branch into its base — requires **explicit human approval for that specific PR**. "Explicit" means a human names the PR (or unambiguously refers to the one in front of them) and says to merge it. A general "yes" to a plan, a green CI run, branch protection permitting the merge, or approval given earlier for a different PR does **not** carry over. When a merge is warranted, propose it and wait for the human to confirm.

**Promotion PRs are the sharpest case of this rule.** On repos with a promotion workflow, agents may dispatch it and open the digest-pinned promotion PR — but merging that PR IS the production deploy. Explicit human approval for that specific promotion PR is required, absolute, no exceptions.

### Workflow dispatch: propose freely, run only with explicit approval

`gh workflow run` (and any `workflow_dispatch` equivalent via the API) is not hard-blocked, but it follows the same approval model as PR merges: **the agent MUST NOT dispatch a workflow autonomously.** Every dispatch requires **explicit human approval for that specific dispatch** — a human names the workflow (or unambiguously refers to the one in front of them) and says to run it. A general "yes" to a plan, or approval given earlier for a different dispatch, does **not** carry over. When a dispatch is warranted, propose the exact command — workflow name, ref, and inputs — and wait for the human to confirm. The harness permission prompt is the enforcement backstop, not a substitute for asking.

Before proposing a dispatch, read the workflow file (or the directives covering it) to know what it does: a workflow named `promote.yml` or `release.yml` may deploy to production or publish artifacts the moment it runs.

### Out-of-context git actions

If the agent is operating in a directory that is not the repo root, or in a multi-repo workspace, it MUST re-resolve which repo each git command targets before running it. `cd`-ing between sibling repos changes which directives apply; carrying assumptions from the previous repo's directives is the most common source of mis-applied rules. When in doubt, run `git rev-parse --show-toplevel` and re-read that repo's directives.
