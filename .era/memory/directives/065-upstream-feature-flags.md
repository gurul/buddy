---
category: upstream-feature-flags
title: Feature Flags & Kill Switches
order: 65
---

## Feature Flags & Kill Switches

Era runs feature flags and operational kill switches on **Flipt v2**, fronted by **OpenFeature** and the shared **`era-flags-toolkit`** (TS `@era-laboratories/flags-toolkit`, Python `era-flags`). There is **no central flag registry** — each **deployable** declares its own flags in a `flags.json`, and the shared machinery (provider wiring, kill-switch enforcement, CI validation, auto-create, drift checking) lives in the toolkit so no service hand-rolls it. Kill switches are the load-bearing case: they fail **closed to the safe path** and are enforced **server-side**. Read this before adding a flag, wiring a kill switch, or flipping anything during an incident.

> **First: is this deployable even flag-wired?** Not every Era repo is on the flag platform yet — the fleet pre-wire is still rolling out (ERA-6855), and many deployables have no flags at all. Before adding, reading, or reasoning about a flag here, confirm this deployable is connected: it has a **`flags.json`** at its root (or `services/<svc>/flags.json`), it **depends on the toolkit** (`@era-laboratories/flags-toolkit` in `package.json` / `era-flags` in `pyproject.toml`/`requirements`), and it **initializes the provider at boot** (`setupFlags` / `setup_flags`). **era-hub-api** and **era-ingress** are the wired, enforcing reference implementations. **If none of that is present, this deployable is not on the platform** — adding a flag is not a one-line manifest edit; it needs the full pre-wire first (see [Wiring the toolkit into a service](#wiring-the-toolkit-into-a-service) → new backend service), which is the ERA-6855 task. Say so rather than assuming flags work here.

### Declaring a flag

Flags are declared **per deployable, in a `flags.json` at the deployable's root** — an OpenFeature CLI manifest (`$schema` = the open-feature/cli flag-manifest). "Per deployable, not per repo":

- **Single-service repo** (era-hub-api, era-ingress): one `flags.json` at the repo root.
- **Multi-deployable monorepo** (era-spindles: `services/ananke`, `services/spinner`, … each independently built and promoted): **one `flags.json` per service**, at that service's root (`services/<svc>/flags.json`) — never a single repo-root manifest. A service that declares flags scopes everything to itself: its `Dockerfile COPY`, its auto-create Job, its drift-checker entry, and — if it declares a kill switch — its own `services/<svc>/openapi.json` for the routes check (never a repo-root one). A flag belongs to exactly one deployable.

Each flag carries the standard `flagType` / `defaultValue` / `description`, plus an **`x-era`** extension block:

| `x-era` field | Required | What it does |
|---|---|---|
| `owner` | always | Attribution — the owning service/team. Also how the drift checker blames a finding. |
| `fail_direction` | always | `release` or `kill_switch`. Declares behaviour when Flipt is unreachable (see below). `kill_switch` forces the flag boolean **and** `defaultValue: false` at CI time. |
| `client_exposed` | always | `true` → the flag is projected to frontends/iOS via `GET /users/me/flags`. `false` → backend-only, never leaves the server. |
| `routes` | any server-side `kill_switch` (see note) | List of `"METHOD /path"` strings the switch turns off, **copied verbatim from that deployable's committed `openapi.json`**. This is the enforcement binding for **every** kill switch — the route index keys off `fail_direction: kill_switch` + non-empty `routes`, never `client_exposed`, so a server-side kill needs it whether or not it's client-exposed (a kill switch without routes enforces nothing). What `client_exposed: true` changes is only that CI **also mandates + verifies** the routes (see kill switches). |

Example `flags.json` — one release gate and one client-exposed kill switch:

```jsonc
{
  "$schema": "https://raw.githubusercontent.com/open-feature/cli/refs/heads/main/schema/v0/flag-manifest.json",
  "flags": {
    "checkout_v2_enabled": {
      "flagType": "boolean",
      "defaultValue": false,               // release: fails open to THIS value
      "description": "ERA-XXXX rollout gate for the v2 checkout flow.",
      "x-era": { "owner": "era-hub-api", "fail_direction": "release", "client_exposed": false }
    },
    "payments_kill_switch": {
      "flagType": "boolean",
      "defaultValue": false,               // kill_switch: MUST be false (CI-enforced)
      "description": "ERA-XXXX permanent kill switch for the paid payments routes.",
      "x-era": {
        "owner": "era-hub-api",
        "fail_direction": "kill_switch",
        "client_exposed": true,
        "routes": ["POST /payments/charge", "POST /payments/refund"]
      }
    }
  }
}
```

The `x-era` block is **metadata only — it leaves zero trace in the generated client**; codegen emits just the key, type, and default. After editing `flags.json`, **regenerate the typed accessors** (`gen-flags-codegen.sh`) — codegen is drift-gated, so a stale generated client red-lights CI. Ensure the deployable's `Dockerfile` **`COPY flags.json`** into the image; a missing copy is a runtime crashloop (ERA-6894 / ERA-6969), because the toolkit reads the manifest at boot.

The **flag-declaration CI gate** (toolkit ≥ v0.4.0, via meta-ci `@v0`) validates every flag: `owner` present, `fail_direction` valid, `kill_switch` default is `false`, `client_exposed` a bool, and — for a client-exposed kill switch — that every `routes` entry exists in `openapi.json`. It fails at **error** severity.

### Wiring the toolkit into a service

Declaring a flag does not evaluate it. A backend service reads flags through the toolkit's OpenFeature provider, initialized once at boot. (Frontends/iOS do none of this — they call `GET /users/me/flags`; this is the backend path.)

**1. Initialize the provider once at startup — fail-open, so a Flipt outage at boot never crashes the service.** `setupFlags` / `setup_flags` register `EraLocalProvider` (a local Flipt engine holding a full snapshot, refreshed streaming ~1s / polling ~30s). `ERA_FLAGS_URL` is the **bare Flipt root** — leave it unset in code and set it in the deployment env (`.platform-managed` repos author it in era-core-platform); an `/ofrep` suffix makes setup throw.

```ts
// TS — @era-laboratories/flags-toolkit, at service start
import { setupFlags } from '@era-laboratories/flags-toolkit';
try {
  await setupFlags();                 // registers EraLocalProvider as the OpenFeature provider
} catch (err) {
  logger.error({ err }, 'flags: provider init failed — evaluations will serve code defaults');
}
// on shutdown: await OpenFeature.close();
```

```python
# Python — era_flags, in the FastAPI lifespan
from era_flags import setup_flags
setup_flags()                          # $ERA_FLAGS_URL; error_strategy="fallback" (default) = fail-open
# on shutdown: await api.shutdown()
```

**2. Read a release flag in a handler.** Build the context (`targetingKey` = hub `users.id` — see Evaluation context) and evaluate. **The default you pass IS the fail-open value**, so it must equal the manifest `defaultValue`:

```ts
import { OpenFeature } from '@openfeature/server-sdk';
import { buildEvaluationContext } from '@era-laboratories/flags-toolkit';
const ctx = buildEvaluationContext({ userId, orgIds });
const on = await OpenFeature.getClient().getBooleanValue('checkout_v2_enabled', false, ctx);
```

```python
from openfeature import api
from era_flags import build_evaluation_context
ctx = build_evaluation_context(user_id, org_ids=org_ids)
on = api.get_client().get_boolean_value("checkout_v2_enabled", False, ctx)
```

Prefer the **generated typed accessors** (`gen-flags-codegen.sh`) over string keys where you can — they're drift-gated, so a renamed flag breaks the build, not production.

**3. Wire a kill switch — different from a release flag.** A kill switch is enforced by **middleware bound to `routes`**, not read inline (see Kill switches for the model + fail-closed semantics). The hookup consumes the toolkit and injects only the resolve-principal + render-503 policies — never hand-roll it:

- **TS:** `buildKillSwitchRouteIndex(manifest)` → `createKillSwitchMiddleware(flagKey, { evaluate, onEngaged })` spliced **first** on each bound route (hub-api does this in its `createOpenApiHono` registrar) → `verifyKillSwitchBindings(index, consumed)` once after all routes register.
- **Python:** `kill_switch_gate(index, context_reader)` as an app-wide FastAPI dependency (raises `FeatureKilledError` → your 503 handler) → `verify_bindings(app, index)` at boot. The `context_reader` is what decides targeting: era-ingress passes the **service-scoped** context (`service_evaluation_context()`), so its kills are global-only.

**New backend service? Pre-wire once (ERA-6855):** dependency + provider init (above) + a `flags.json` (start empty) + the flag-declaration CI caller + a release-shape `_wiring_canary` flag proving the path end-to-end. After that, every new flag is just a manifest entry plus a read.

### Auto-create — boolean flags reach Flipt on deploy

For **boolean** flags you do **not** hand-create anything in Flipt. Declaring the flag in `flags.json` is enough: on the next real deploy, a **PostSync Job** (ERA-6824, distributed as the shared kustomize component ERA-6994) seeds any missing boolean manifest flag into Flipt at `enabled = defaultValue`. It is **create-only** — it never updates or deletes an existing flag. So a newly declared boolean flag self-heals into Flipt on deploy; a flag removed from the manifest is **not** removed from Flipt (that stays a deliberate manual step — see lifecycle). **Non-boolean (variant/string/object) flags are NOT auto-created** — their variants/rules can't be derived from a manifest default, so the Job skips them with a warning and you create them by hand in the Flipt UI. (Kill switches and every live fleet flag are boolean, so in practice auto-create covers them.)

### Fail direction — the rule that bites

- **`release`** flags **fail open** to the code/manifest default. A normal rollout/enablement gate; a Flipt outage leaves the coded default in force.
- **`kill_switch`** flags **fail closed** to the safe path. The manifest `defaultValue` **MUST be `false`** (CI-enforced) so an unreachable Flipt — or a pod cold-starting mid-outage — can **never self-trip** the kill. An already-engaged switch stays engaged through an outage via the toolkit's last-good snapshot; only cold-start-mid-outage falls back to the not-engaged default. This is decision **DL-2026-07-20** (MP §600) — do not invert it.

### Kill switches — permanent, server-side, toolkit-enforced

A kill switch is a `"switch engaged?"` boolean that lets on-call instantly refuse a route during a cost runaway, data-corruption risk, or upstream incident (MP §592, Uber pattern). Kill switches are **permanent and cleanup-exempt** (ERA-5966) — you don't garbage-collect them.

- **Enforcement is server-side and lives in the toolkit — never reimplement it.** The kill-switch core (route-index build, fail-open decision middleware, dead-switch boot check) is `@era-laboratories/flags-toolkit/killswitch` (TS, ERA-6981) / `era_flags.killswitch` (Python, ERA-6969). A service wires it thinly and injects only two per-service policies: **how it resolves the principal + evaluates the flag**, and **how it renders the refusal**. Do not copy-paste the enforcement logic between services.
- **The `routes` list is the binding — for every kill switch, `client_exposed` or not.** At boot the toolkit builds a `"METHOD /path" → flagKey` index from `flags.json` and splices a kill-switch middleware onto **exactly those routes**, as the **first** handler — after auth, before request validation and RBAC. The index keys off `fail_direction: kill_switch` + non-empty `routes` and ignores `client_exposed` (both the TS `buildKillSwitchRouteIndex` and Python `build_route_index`), so a purely server-side kill (`client_exposed: false`, e.g. `ingress_inference_passthrough_kill`) is bound and enforced exactly the same way. Binding a route is a **manifest edit, not a code change**.
- **CI verifies `routes` only for the `client_exposed: true` class.** The flag-declaration gate mandates a non-empty `routes` and checks each entry exists in `openapi.json` (error severity) **only** when `kill_switch` + `client_exposed: true`. For a `client_exposed: false` kill, routes is still functional and still required to enforce, but CI does **not** verify the strings — so a typo won't be caught by the routes gate. Get the entries right against `openapi.json` yourself.
- **Dead-switch protection.** After all routes register, the service calls the toolkit's bindings check once — `verifyKillSwitchBindings()` in TS (hub-api, `app.ts`), `verify_bindings(app, index)` in Python (`era_flags.killswitch`, wrapped as `verify_kill_switch_bindings(app)` in era-ingress `router.py`). A `routes` entry that no registered route matches — a typo or a renamed path — throws at boot regardless of `client_exposed`, turning a silently dead switch into a loud deploy crash. (Confirm your service actually calls it — it is the only backstop for the non-client-exposed routes CI does not verify.)
- **Refusal shape:** `503` problem+json, `retryable: false`, `reason: "feature_killed"`. `retryable:false` because an auto-retry only re-hits the kill.
- **Client flags only hide UI.** A `client_exposed` flag lets a frontend hide a button, but the real kill is the backend refusal. If blast radius is server-side (cost, data, paid/privileged access), it MUST be enforced server-side — the UI hint is not the control.

### Evaluation context & targeting

Flags are **never evaluated context-free** (Amendment B — an empty context silently matches no targeting rule). The context is `{ targetingKey, org_id?, ...traits }`:

- **`targetingKey` = the hub `users.id`**, for every principal in every service (ERA-6846). It is Flipt's **entity**: it drives percentage-rollout bucketing and per-user targeting (a segment matching the id, e.g. `isoneof [...]`). It is **not** a matchable property — the provider maps it to the entity and strips it from the constraint map. Services holding only a Kratos identity (e.g. era-ingress) must resolve Kratos id → hub `users.id` **before** evaluating; never send a raw Kratos id. Bucketing is a **deterministic hash of the targetingKey**: a user lands in the **same bucket in every service** (this is *why* the one-identity rule matters — divergent ids would bucket the same human differently per service) and stays there across evaluations, and raising a rollout percentage only **adds** users rather than reshuffling them.
- **`org_id`** is the only standard attribute, and it is set **only when the caller resolves to exactly one owning org**. Zero orgs and **multi-org** callers both omit it — so an `org_id` segment **silently skips multi-org users** (they fall through to the default; ERA-7023). Global kills must never depend on targeting: an unresolved principal skips evaluation and is **NOT killed**.
- **Need to target on anything else** (staff-only, plan tier, cohort)? Add a **trait**. `buildEvaluationContext` passes any extra trait straight through — no allowlist, no manifest, invisible to codegen and drift. Compute a **derived scalar** at the call site (Flipt's context is a flat string map, so send a clean boolean, not a raw set), then author a segment matching it. **Mind the API shape — it differs by language** (TS takes a nested `traits` object; Python takes keyword args):

  ```ts
  // TS — traits is a nested object property of EraContextInput
  const ctx = buildEvaluationContext({ userId, orgIds, traits: { is_platform_staff: isStaff } });
  ```

  ```python
  # Python — traits are keyword args, NOT a dict
  ctx = build_evaluation_context(user_id, org_ids=org_ids, is_platform_staff=is_staff)
  ```

  Values are stringified for you (booleans → `"true"`/`"false"`), so a Flipt segment then constrains `is_platform_staff == true`. Because nothing cross-checks trait keys against segment constraints, a producer/segment key mismatch fails silently to the default — **pin the trait key with a test** so the two halves can't drift.

### Targeting rules & segments live in Flipt, not in the repo

`flags.json` has **no representation of segments or targeting rules** — it declares flags and their defaults only. Segments and rollouts are authored in the **Flipt UI**, and are **reusable across flags**. The drift checker deliberately does **not** reconcile targeting rules — it surfaces them, never enforces them — so a segment you add is invisible to CI and codegen. Keep global on/off flags as plain booleans; reach for a segment only when you genuinely need to scope to specific users/orgs/cohorts.

**Segments belong on release flags, never on kill switches.** A segment only matches against the context a service actually sends — and services send **different** context. hub-api sends the real user identity (auth already resolved it before the kill middleware runs, so it's free), so a user segment would match there. era-ingress deliberately sends only a **service-scoped** context (`service_evaluation_context()`, `lib/kill_switch.py` — a global switch needs no identity lookup), so the same user segment matches **no one** there, **silently**. The identical switch would then behave differently per service mid-incident — exactly when you can least afford surprise. So kill switches flip **globally** (the Flipt default value, never a targeting rule); segments live on release flags. This is currently a **convention, not machine-enforced** (a candidate drift-checker rule, not yet filed) — so it is on you to honor it.

### Flipping a flag during an incident

**Flip flags in the Flipt UI — never by editing the era-flags repo.** The era-flags repo is Flipt v2's storage backend (state persisted as git files, `staging` branch for staging, `production` for prod), but the UI is the only sanctioned path: it goes through Flipt's validation, auth/RBAC, and audit trail. A raw commit/push to era-flags bypasses all three and is not how flags are operated. A flip is a one-toggle change in the UI for the target environment. Because the flip happens in Flipt itself, Flipt has the new value immediately; what remains is each service refreshing its snapshot from Flipt — so propagation is **~30s worst case** (the TS poll floor), and **language-asymmetric**:

- **Python** services (streaming transport) propagate in **~1s**.
- **TS** services (`flipt-client-js`, **polling-only, no streaming**) propagate in **up to ~30s per pod** — a TS-served route can keep serving for one poll interval after the flip. This is a dependency floor, expected, and safe (an unreachable Flipt still can't self-trip a kill).

**Drill / verify a flip with a differential probe:** send the same request twice around the flip and confirm the status changes (e.g. nominal → `503`). A clean log scan alone proves nothing on low-traffic staging — drive the request.

### Flag lifecycle — creating vs removing

- **Create:** declare in `flags.json` → regenerate codegen → (for a client-exposed kill switch) add `routes` from `openapi.json` → PR. On merge + deploy, auto-create seeds it into Flipt at its default.
- **Remove:** order matters. **Remove from code → deploy → then delete from Flipt.** The reverse order produces transitional drift the nightly checker catches. Auto-create never deletes, so dropping a flag from the manifest does **not** remove it from Flipt — that deletion is a deliberate manual act (and kill switches are permanent — don't delete them at all).

### Frontends & iOS never enforce

Frontends and iOS **do not embed the toolkit and never enforce** — enforcement lives in the backend they call. They consume resolved values from **`GET /users/me/flags`** (ERA-5965): hub-api evaluates server-side with the real `users.id`, and the response is projected over the `client_exposed: true` allowlist only. A client-side flag hides UI; it is never the security control.

### Anti-patterns

1. **Reimplementing kill-switch enforcement per repo.** Consume `@era-laboratories/flags-toolkit/killswitch` / `era_flags.killswitch` and inject the two policies. Hand-rolled copies drift.
2. **A `kill_switch` with a non-`false` default.** Fails open — an unreachable Flipt self-trips the kill. CI blocks it; never override.
3. **Scoping a global kill to a segment.** Unresolved principals skip evaluation and are NOT killed — everyone outside the segment keeps the feature live. Global kills are plain booleans.
4. **Editing `flags.json` without regenerating codegen, or without a `Dockerfile COPY`.** The first red-lights the drift gate; the second crashloops the pod at boot.
5. **Assuming org/richer targeting works out of the box.** Only `org_id` (single-org) and per-user/entity targeting are wired. Multi-org users don't match an `org_id` segment; anything staff/tier/cohort-based needs a derived trait added at the call site first.
6. **Deleting a flag from Flipt before the code stops reading it.** Remove from code and deploy first; delete in Flipt last.
7. **Editing the era-flags repo directly to flip or author a flag.** It is Flipt's storage backend, but operating it by hand bypasses Flipt's validation, RBAC, and audit trail. Flip and author segments in the **Flipt UI**, always.

### Verify before acting

- New flag: `flags.json` valid, codegen regenerated, `Dockerfile` copies `flags.json`, flag-declaration gate green.
- New/edited kill switch: `fail_direction: kill_switch`, `defaultValue: false`, `routes` entries exist verbatim in `openapi.json`, enforcement consumed from the toolkit, boot-time binding check present.
- Incident flip: done in the **Flipt UI** for the target environment (never by editing the era-flags repo), then a differential probe on the live service (account for the ~30s TS poll floor) — don't trust logs alone.
