# Open tasks: generalisation / overfitting

Status as of 2026-11-06.

The perturbation test copied the corpus into another common layout (`services/`, `libs/`, `knowledge-base/`, `contracts/`, `tickets/`, CODEOWNERS at the repo root, no `config/ports.json`) and ran the deterministic ETL. The result:

| | Original layout | Perturbed layout |
|---|---|---|
| Sources | 212 | 37 |
| Entities | 241 | 36 |
| Verified service-to-service calls | 14 | 0 |
| Tables | 4 | 0 |
| Security risks | 17 | 0 |
| Ownership | found | lost |

## To do

- [ ] **T1: Content-based routing instead of path-based** (`etl_pipeline/router.py`).
  - Detect OpenAPI by its `"openapi":` key.
  - Detect migrations from a `migrations/` folder at any depth.
  - Accept CODEOWNERS in all three GitHub locations.
  - Treat any folder with a manifest as a component: package.json, pyproject, requirements.txt, pom.xml, go.mod or a Dockerfile.
  - Get the doc kind from headings and filename patterns, or from one batched LLM classification call.
  - Remove `"Northstar"` and the `·`-specific assumptions from the metadata parser.
- [ ] **T2: Service resolution without a port registry.**
  - Resolve URLs to services from compose service names and ports, k8s Services, Dockerfile `EXPOSE`, and `PORT=` in `.env`.
  - Keep `ports.json` as an optional extra source.
- [ ] **T3: Pair specs with services by content.**
  - Use the OpenAPI `servers` URL or `info.title`, plus the endpoint overlap with the code routes.
  - Stop relying on the `api-specs/<service>.md` filename convention (`run_etl.py`).
- [ ] **T4: Detect shared libraries from the workspace definition.**
  - Read `pnpm-workspace.yaml`, npm/yarn `workspaces`, and Python path dependencies.
  - Stop relying on the `@repo/` prefix (`parsers/code.py`, `security_rules.py`, `hybrid_retriever.py`).
- [ ] **T5: Replace the heuristic Java parser.**
  - The `parts[i].equals(...)` router is specific to this repo.
  - Use tree-sitter queries, and fall back to LLM extraction.
- [ ] **T6: De-specialise the security rules.**
  - The idempotency rule is tied to the name `idempotency_key`. The IDOR rule is tied to `WHERE id = ?` and to the word `identity`.
  - Generalise them, or move them into semgrep-style rule packs with tests.
- [ ] **T7: Held-out evaluation.**
  - Plant known defects into a different open-source microservice repo with a script: a stale doc, a dropped column, a missing auth check, an undocumented call, an ownership change.
  - Evaluate on that repo without tuning against it.
- [ ] **T8: Perturbation regression test.**
  - Add the restructure experiment above to `eval/`.
  - Results must stay identical whatever the layout.
- [ ] **T9: Recalibrate on held-out data.**
  - The policy-linker threshold (0.73) and the name-resolution scores were chosen on this corpus.
  - Re-check them on T7 data, then report both the tuned and the held-out metrics.
- [ ] **T10: Document generalisation in DESIGN.md.**
  - Cover the perturbation result, the eval-leakage caveat, and the plan above.

## Done

- [x] Contradiction lookup walks up from a column to its table and then its service. A parent's risks are passed down only when they mention the entity (`hybrid_retriever._risks_with_lineage`).
- [x] `negated` assertion mode plus a negation-drift check. A doc saying "no column yet" is now compared against migrations.
- [x] LLM entity-resolution agent for runtime entities that appear only in docs. It is conservative: decoys stay separate.
- [x] Paid-tier run: 9/10 golden recall, 0 decoy false links, 7/7 probes, $1.36 per full build.

## Open evaluation item
- [ ] **G5.** Decide whether "doc A says doc B needs an update" should become a claim. ADR-005 says this about the gateway page. If so, extract it as `record -MENTIONS(stale)-> doc` and flag the target doc. Validate this on held-out data (T7), not on G5 alone.
