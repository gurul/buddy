---
category: upstream-deployment
title: Branch and Deployment Topology
order: 60
---

## Branch and Deployment Topology

The Era fleet runs on a **trunk model**. The wave cutover of the deployables (ERA-6735 / ERA-6736 / ERA-6737) completed 2026-07-17; the staging ApplicationSet `branchMatch` flip to `^main$` (ERA-5960, era-core-platform PR #1580) merged 2026-07-19. `main` is the only persistent branch on every deployable. A merge to `main` deploys the **staging** overlay; production moves **only** through a human-merged promotion PR. Era runs three independent ArgoCD instances, each reconciling only its own cluster. Verify the live wiring before changing branches, manifests, or ArgoCD config — never assume.

### Branch model — `main` only

- Feature work branches off `main`, opens a PR, and merges back to `main`. **The PR base is always `main`.**
- There is no `develop` branch. A `develop` branch existing on a deployable is an **anomaly to flag**, not a base to target — the staging ApplicationSet scans default branches only and requires the default branch to be `main` (`branchMatch: ^main$`), so a repo still on `develop` silently drops out of staging. Report it; do not PR features to it.
- `git ls-remote origin develop` returning a ref on a deployable means that repo has not finished (or has regressed) its trunk cutover — surface it.
- **A PR is a deployable unit.** Merge = staging deploy, so every PR must be one coherent, releasable change — small deployments, one feature. Do not bundle unrelated changes; do not merge half a feature. Unfinished user-facing behavior stays unmerged (or ships behind a feature flag once the flag platform is available) — never merged-but-broken.

### The three clusters

| Cluster | ArgoCD install path | Branch | Overlay | Image registry (pull source) |
|---|---|---|---|---|
| `era-core-staging` | `era-core-platform/argocd/install/overlays/staging/` | `main` | `k8s/overlays/staging/` | `us-central1-docker.pkg.dev/era-core-prod/...` |
| `era-core-prod` | `era-core-platform/argocd/install/overlays/production/` | `main` | `k8s/overlays/production/` | `us-central1-docker.pkg.dev/era-core-prod/...` |
| `era-labs-tools` | `era-tools-platform/argocd/install/overlays/production/` | `main` | `k8s/overlays/tools/` | `us-central1-docker.pkg.dev/era-labs-tools/era-tools/...` |

Every `era-apps-*` ApplicationSet sets `destination.server: https://kubernetes.default.svc` — each ArgoCD only deploys into its own cluster. Cluster routing is determined by **which overlay name a repo declares**, not by any central registry. Under the single-registry model (ERA-5950 / ERA-5951) both core clusters pull images from the `era-core-prod` registry — CD builds each image once and pushes it there; only tools-cluster images live in `era-labs-tools`. The `argocd/clusters/{staging,production}.yaml` cluster secrets are aspirational hub-spoke templates with placeholder values; not load-bearing.

### Repo topology by overlay set

A repo's `k8s/overlays/` listing reveals where it deploys:

- `staging` + `production` → core app; staging reconciled from `main` by era-core-staging, production by era-core-prod (promotion-only)
- `tools` only → tools-resident, deploys to era-labs-tools from `main`
- `production` only → typically `.platform-managed` (manifests authored in `era-core-platform`)
- `staging` + `tools` **or** `production` + `staging` + `tools` → **legacy shape being eliminated** (ERA-3264). Present today on `era-decision` (staging+tools) and `era-company-org` (production+staging+tools). Caution: if such a repo has `k8s/base/kustomization.yaml` plus a staging overlay on `main`, it now matches the flipped staging ApplicationSet's filters (`branchMatch: ^main$` + `pathsExist`) and will **zombie-deploy** into era-core-staging. Collapse these to a single canonical overlay set as part of ERA-3264; do not add new repos in this shape.

Tools-only repos must NOT have `k8s/overlays/production/` — it generates dead `era-*-production` Applications in core-prod ArgoCD (ERA-3113).

### Marker files

- **`.platform-managed`** at repo root → manifests are authored in `era-core-platform`; do NOT add or edit `k8s/` here. ApplicationSets skip these repos via `pathsDoNotExist: [.platform-managed]`.
- **`k8s/overlays/<env>/kustomization.yaml`** → required for ApplicationSet discovery via `pathsExist`. The staging appset additionally requires `k8s/base/kustomization.yaml`; a repo without a staging overlay on `main` has opted out of staging.
- **`.spec-versions/<pkg>.pin`** → repo consumes Era spec channels. Pins are **single-line, `main:`-keyed** (ERA-6681): one channel line per package, keyed to the `main` trunk. Renovate-managed, do not hand-edit.

### Deploy model — merge deploys staging, promotion deploys production

A merge to `main` never touches production. CD builds once and bumps the **staging** overlay (core) or the **tools** overlay (tools cluster). Production advances only through the promotion workflow.

| Event | Cluster | What happens |
|---|---|---|
| Merge to `main` — **core repo** | era-core-staging | `cd-standard-service.yaml@v0` (trunk-only routing is its sole mode, ERA-6738 — there is no `trunk-mode` input) builds the image once → pushes to the `era-core-prod` registry → runs `kustomize edit set image` on `k8s/overlays/staging` and commits the pin back to `main`. The staging ApplicationSet reconciles. Two core repos (`era-maker`, `era-safety-classifier`) hand-roll an equivalent staging-only CD instead of calling the reusable. |
| Merge to `main` — **tools repo** | era-labs-tools | Tools repos also call `cd-standard-service.yaml@v0`, which auto-detects `main` → `k8s/overlays/tools` (or takes an explicit `k8s-overlay-path`, e.g. era-decision's three callers), builds the image once → pushes to the `era-labs-tools` registry → bumps the tools overlay on `main`. |
| `promote.yaml` `workflow_dispatch` — **core repo** | era-core-prod | `promote-to-production.yaml@v0` opens a **promotion PR** that moves `k8s/overlays/production` to an already-staging-tested image digest (a tag move inside the one `era-core-prod` registry). **A human merges the PR** — that merge is the production deploy (ERA-5955). |

**CD never writes a `production` overlay — no exceptions.** The reusable rejects any non-`main` branch and any `k8s-overlay-path` override whose basename is not `staging` or `tools` — a `…/production` path can never route through CD, so production is promotion-only by construction (ERA-5953 / ERA-5955). (The historical exception — `era-decision`'s api/web overlays *named* `production` that targeted the tools cluster, ERA-3828 — was renamed to `tools` on 2026-07-20; era-decision PR #27.) Always pin the reusables to `@v0` (moving major-version tag), never a SHA. Not every repo has automated CD: `era-push-service` has none. (`era-decision` runs three `cd-*` callers of the reusable, and `era-code-manager` has its own `cd.yaml` image-bump-PR CD — both are automated.)

### Promotion discipline — promote small, promote often

- **Same-day promotion is the norm.** After your merge is verified on staging, promote it. Staging running ahead of production is the old disease re-forming; a growing commit range in the promotion PR body is a warning sign. Agents should **surface** staging-ahead-of-prod drift when they see it, never add to it.
- **The agent contract for promotions:** agents MAY run `promote.yaml` (`workflow_dispatch`) and open the resulting promotion PR. The confirmation step is: **validate the change on staging first, then confirm the candidate digest is the exact staging-validated image** — the default candidate is the current staging overlay pin, and the PR body shows the commit range plus spec-gate annotations; check them. **Merging a promotion PR requires explicit human approval — absolute, no exceptions.** This is the standing never-merge-autonomously boundary (git-discipline directive), restated for promotions because a promotion merge IS the production deploy.
- **Rollback = promote the previous digest.** Open a promotion PR pinning the last known-good digest. Never cherry-pick, never hotfix-branch, never hand-edit the production overlay.
- Always promote an explicit **`@sha256` digest**, never a floating tag.

### Base edits deploy both environments at once

Both ArgoCDs render from the same `main`: staging syncs `overlays/staging`, prod syncs `overlays/production`, and **both include `base/`**. Only the image digest has a promotion gate — manifests do not. The old `develop` buffer in front of prod manifest changes is gone; PR review is the only gate.

- **Env-divergent config lives in the overlay, never base** — replicas, resource limits, hostnames, env vars.
- **Treat any `k8s/base/**` edit (or shared platform resource in era-core-platform) as a production change.** It applies to staging AND prod on the same sync. Call it out in the PR body; it deserves production-overlay-level scrutiny.
- **Staging-overlay-first for risky base changes:** land the change as an `overlays/staging` patch → verify live on staging → then move it into `base/` (optionally with a temporary prod-overlay counter-patch whose removal is the "promotion"). Never ship a scary probe/resource/RBAC change straight into base.
- **Reverting a base edit is also a dual-env deploy** — plan the rollback with the same care as the rollout.

### Image tag and spec channel mechanics

CD writes the 7-char short_sha into the staging (or tools) kustomize overlay via `kustomize edit set image`, in one atomic commit per run, then commits to `main` with concurrency group `cd-${{ github.ref_name }}`. A promotion writes the corresponding production-registry digest into the production overlay in a single PR. The `:main` branch alias also exists on GHCR but is not consumed by manifests.

Spec channels:

- Publish-side tags: `latest` (newest publish, any channel), `stable` (newest stable publish only)
- Deploy-side tags: `staging` (moved on a staging rollout from `main`), `production` (moved on a production rollout) — retagged by `meta-ci-actions/.github/workflows/post-rollout-retag.yaml` after ArgoCD fires the `rollout-succeeded` repository_dispatch on Healthy. (ERA-6683 will retire the `:production` deploy-side tag; treat its presence as transitional.)

There is **no `tools` channel** — tools-cluster apps don't participate in spec channels. Channel-tag invariant (ERA-3019): `:staging` ⇒ `*-staging.<unix>` shape only; `:production`/`:stable` ⇒ stable semver shape. Never conflate.

### Anti-patterns

1. **ApplicationSet element removal cascades.** Pruning a list-generator element prunes the entire Application plus finalizer-gated children, including Namespaces. ERA-2898 cascade-deleted tools-cluster Traefik this way. Grep the source overlay for `kind: Namespace` before removing any ApplicationSet element.
2. **Tools-only repos must not have a `production` overlay.** Creates dead `era-*-production` duplicates in core-prod ArgoCD (ERA-3113).
3. **`commonLabels` on Service selectors breaks Endpoints.** Adds labels to selector but not to pod template — silent 502s on era-ingress. Removed from `applicationset-era-apps-staging.yaml` and platform ApplicationSets for this reason.
4. **GKE Autopilot drift.** Every Deployment/StatefulSet needs `ignoreDifferences` for `autopilot.gke.io/resource-adjustment` and `autopilot.gke.io/warden-version` on both metadata and pod template. Without these, perpetual OutOfSync. HPA `replicas` and ExternalSecret `/status` likewise need ignoreDifferences entries.
5. **SSA field-manager conflicts on tools cluster.** Tools ApplicationSet requires `Force=true` syncOption (era-apps-tools L98), added 2026-05-07 after an IngressRoute/gallery incident. When `kubectl apply` from a one-off creates a foreign field manager, ArgoCD's ServerSideApply leaves the field alone, returns empty diff, and reports Synced while live state diverges from git.
6. **`argocd-cm` cannot be configMapGenerator-managed.** Hash-suffixed CM names break argocd-server's literal-name lookup via the K8s API. Use strategic-merge patches only. UI outage 2026-05-06 for context; tracking ERA-2964.
7. **`external-secrets` chart upgrade requires `Replace=true`.** cert-manager owns the `caBundle` subfield, leaving CRDs in invalid intermediate state otherwise (ERA-2030/2032). Conditional in `applicationset-platform-helm-staging.yaml:100` via Go-template ternary; quoted-scalar form is required because list-item directives at indent are invalid YAML.
8. **Empty-carrier-commit channel-tag stagnation.** A `force-publish-spec` promote that doesn't move the producer image produces a kustomize commit with no manifest change; ArgoCD won't fire `rollout-succeeded` for an empty diff, leaving `:production` channel tag stale. Mitigation: `retag-after-carrier-commit` dispatch path in `post-rollout-retag.yaml` (ERA-3016).
9. **Missing `pathsDoNotExist: [.platform-managed]` filter.** Without it, SCM-Provider ApplicationSets generate Applications for repos whose manifests are authored in `era-core-platform`, producing drift between in-repo and platform manifests.
10. **Recreating a `develop` branch or PRing features to a `develop` base.** The fleet is trunk-only (ERA-5960 / ERA-6737); `develop` is retired. A `develop` branch on a deployable makes the staging appset (`branchMatch: ^main$`) skip the repo, so it stops deploying to staging while looking healthy. PR every feature to `main`; treat any surviving `develop` as an anomaly to remove, not a base to target.
11. **Editing a production overlay outside a promotion PR.** `k8s/overlays/production` is owned by `promote.yaml` (ERA-5955). A hand-edit — or routing CD at it — bypasses the staging-tested-digest gate and the human merge that constitutes the production deploy. Production changes go through a promotion PR, always.
12. **Removing a repo's staging overlay while its CD auto-detects.** `cd-standard-service.yaml` resolves `k8s/overlays/staging` first, then `k8s/overlays/tools`, and hard-errors when neither exists — failing every merge to `main`. Delete a staging overlay only together with the CD wiring that depends on it.

### Verify before acting

When a task touches branches, k8s manifests, ApplicationSets, or CD workflows:

- Confirm the repo's **default branch is `main`** (`gh repo view --json defaultBranchRef` or `git symbolic-ref refs/remotes/origin/HEAD`); a `develop` branch on a deployable is an anomaly to flag, never a PR base.
- Check `.platform-managed` at repo root before editing `k8s/`.
- Read the ApplicationSet (not just the overlay name) to confirm cluster + branch filter + `pathsExist`/`pathsDoNotExist` — do not infer wiring from filenames.
- If introducing a new overlay name, update or add the matching ApplicationSet in the same change.
- For changes to platform Helm/resources lists, grep the source for `kind: Namespace` before pruning any element.
- Cross-check that the image registry in your overlay's `images.newName` matches the cluster destination: core staging and production overlays → `era-core-prod`; tools overlay → `era-labs-tools`.
- Never route CD at a `production` overlay; production moves only through `promote.yaml` (ERA-5955).
