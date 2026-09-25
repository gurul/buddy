#!/usr/bin/env node
// Runs check.mjs over every model in Buddy/, asking about every theorem each
// file declares: none may use sorry, native_decide or a non-standard axiom.
import { readdirSync, readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
let failed = 0, total = 0;
for (const f of readdirSync(join(here, "Buddy")).filter((f) => f.endsWith(".lean")).sort()) {
  const src = readFileSync(join(here, "Buddy", f), "utf8");
  const ns = (src.match(/^namespace\s+(\S+)/m) || [])[1];
  const names = [...src.matchAll(/^(?:private\s+)?theorem\s+([A-Za-z_0-9']+)/gm)].map((m) => (ns ? `${ns}.${m[1]}` : m[1]));
  if (!names.includes(`${ns}.current_violates`) || !names.includes(`${ns}.fixed_invariant`)) {
    console.error(`FAIL Buddy/${f}: missing current_violates or fixed_invariant`);
    failed++;
    continue;
  }
  const r = spawnSync(process.execPath, [join(here, "check.mjs"), `Buddy/${f}`, ...names], { encoding: "utf8" });
  const out = (r.stdout || "") + (r.stderr || "");
  if (r.status !== 0) { console.error(out); failed++; }
  else { console.log(out.trim().split("\n").pop()); total += names.length; }
}
if (failed) { console.error(`FAIL ${failed} model(s)`); process.exit(1); }
console.log(`ALL_MODELS_OK ${total} theorems`);
