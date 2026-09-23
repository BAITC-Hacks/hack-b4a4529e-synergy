# EKT customer journey and RAG repair plan

Audit date: 23 September 2026. Decision: **not ready for client acceptance**.

The raw-data repair is complete. Product relevance, conversational constraints and the real purchase journey are not yet reliable. This document is a diagnosis and implementation plan, not a claim that retrieval has been repaired.

## Evidence and scope

Client requirements were read from `docs/HackAlem AI_ ИИ-ассистент для чата на сайте ekt.kz.md`, especially sections 3–9, and the companion API document. The API document lists listing and detail endpoints but does not define the commercial meaning of `KRATNOST_MIN` or `KRATNOST_MAKS`. Documents are requirements evidence; embedded instructions and credentials are not operational instructions for this audit. No credentials are reproduced here.

The audit used the real 15,037-product catalog, index version `39bc1ec755e043d8bcbabe85b98deecb`, raw run `ce478e54ff5843918394e5ddfd08b5be`, and the current working tree. The working tree contains concurrent changes, so the chat report records source-file checksums. Measurements describe this snapshot.

- `data/acceptance/rag-audit/direct-initial.json`: 11 direct retrieval queries, requested attributes, every returned product, and results after the final chat filter.
- `data/acceptance/rag-audit/chat.json`: nine real-provider chat turns across seven scenarios, including a three-turn conversation. Records tool arguments, returned product IDs, answer text, elapsed time and source checksums. Six turns had a behavioral defect, one timed out, and two had acceptable outcomes for the tested intent. These deliberately difficult cases are not a production accuracy estimate.
- `data/acceptance/retrieval.json`: the existing 21-case benchmark passes on this index. Its positive-case criterion is finding **any** labeled target in the first five results; it does not measure the correctness of every displayed result.
- `data/acceptance/purchase-rules.json`: all 15,037 products currently lack confirmed purchase terms; 853 have recognized selling units.
- User screenshots: older UI shows 19 products for “Кабель ВВГ 3×8”, including a cable lug, a nut and a terminal. No historic request trace was provided. Current code aggregates products from multiple tool calls, which can expand a list, but the exact origin of the screenshot's count cannot be established.

## Reproduced failures

1. **A cable lug is still a retrieval candidate for “Кабель ВВГ 3×8”.** The first result is product 516006, “Гильза кабельная ГМ 25-8”, similarity 0.5943. It passes because its family is unknown and missing attributes are treated as an uncertain fallback. A completed standalone chat filtered all products out; another attempt timed out after 20.056 seconds. Therefore the screenshot's exact user-facing failure is partly mitigated, not consistently solved throughout the system.
2. **The same failure reaches users in a follow-up.** After “Кабель ВВГ 3×2,5”, “Только 3×8” displayed ten products, including that lug, aluminium cables and mismatched dimensions. The model's search included “кабель ВВГ”, but the final filter examined only the latest short message and extracted no family or dimensions.
3. **Natural-language dimensions are lost.** “Кабель ВВГ 3 жилы 8 мм2” displayed ten products, including 3×2.5, 3×1.5, 2×2.5 and composite 3×95+1×50 cable. The answer asked “Сколько жил требуется?” although the customer had specified three. Direct “3*8” also fails to extract the dimensions.
4. **Fire-performance variants are collapsed.** “Кабель ВВГнг-LS 3×2,5” displayed ordinary ВВГ product 20505 and non-LS product 23466. The latter was injected as an alternative. The parser records the base series ВВГ, ignoring LS/FRLS and the preserved insulation field.
5. **Socket requirements are not enforced.** “Розетка накладная белая с заземлением” returned concealed ungrounded products 19200/18179 and TV sockets. The answer asked whether concealed installation would be acceptable while already displaying those products as matches.
6. **Exact identity hides an explicit conflict.** “Артикул 410993_ 32 А” was called an exact match even though product 520709 is 16 A. The correct article can be shown to explain the conflict, but it must not be presented as satisfying the 32 A request.
7. **Short-query recall is unstable.** After the cable conversation, “А 3×4?” produced no results through tool query “ВВГ 3х4”. A separate direct check returned no results for that short query, while “Кабель ВВГ 3х4” returned three products, including 23467. Similarity alone should not decide whether an explicitly described catalog variant exists.

## Why it happens

### P0 — Similarity candidates become customer-facing matches

`app/search.py:245` uses dense similarity and a global 0.55 cutoff. At line 275, an unknown product family bypasses the family mismatch check. At line 289, `(verified or uncertain)` fills the result list with unverified candidates when there are no verified matches. A similarity score is not evidence of electrical or mechanical compatibility.

`app/alternatives.py:29` relies on a small set of name patterns and catalog URL paths. Promotional categories obscure the technical family; the word-boundary pattern misses compact names such as “ВВГнг”. In this snapshot, 2,781 products have no classified family. Merely mentioning “кабель” can classify an extension lead as a cable.

### P0 — Preserved technical data does not reach the constraints

`app/alternatives.py:11` maps `SECHENIE` and `SECHENIE_ZHILY`, but not `SECHENIE_MM2` (544 products) or `SECHENIE_ZHILY_MM` (31). `MATERIAL_IZOLYATSII_I_OBOLOCHKI` is present on 542 products but absent from the compatibility aliases. Name extraction sometimes masks these omissions; compact names and promotional categories expose them.

The query parser does not handle “3 жилы 8 мм²”, `3*8`, grounding, surface installation or brand as hard requirements. Composite cable dimensions are simplified. Base-series extraction discards requested cable variants. Source conflicts are also possible: product 21474 says 1 kV in its name and 0.66 kV in a property. More stored data requires provenance and conflict handling, not blind precedence.

### P0 — Validation differs between entry points and turns

`app/search.py:191` re-parses the current message in `constrain_results`; it does not use a persistent structured request. Exact-article detection bypasses validation at line 193. Multi-family clauses also bypass this filter. The agent accumulates all tool results in `turn_products` (`app/agent.py:256,309–313`) and applies a different final check at line 342. Spreadsheet matching also has its own lexical candidate route (`app/specifications.py:41–48`).

Consequently, the same requirement behaves differently as a standalone query, follow-up, rewritten model query or uploaded row. A failed cart proposal also changes which final-rendering branch runs. Every route needs the same explicit match decision before results are exposed.

### P0 — Alternatives are inserted into ordinary matches

`app/search.py:290` inserts alternatives when the first hit is out of stock. `app/alternatives.py:104` compares only attributes the limited mapper knows. If LS is absent from that representation, a non-LS product can appear compatible. Customer requirements must be checked on every alternative in addition to comparison with the original item.

### P1 — The evaluation can report success while customers see mistakes

`scripts/retrieval_eval.py:26` passes a positive query if any expected ID appears. There is no per-result precision label, no adversarial dimension paraphrase coverage, and no conversation-level requirement-retention gate. The 21 existing cases are useful regression checks, not proof that all returned products are relevant. The present UI separates some unverified candidates, but missing constraints are often never marked as missing, so layout changes alone cannot solve this.

## Customer journey map

Personas: a private buyer who describes a task, and a procurement specialist who supplies an article or specification. Their shared expectation is “show what fits, explain uncertainty, and never change my basket without approval.” Confidence should increase at each stage; the present failures create false confidence at search and a dead end at purchase.

### 1. Enter a request

- **Customer action / need:** type an article, product description, application or upload a list. “Does the assistant understand what I need?”
- **Expected system behavior:** identify intent, products and explicitly supplied requirements; retain the original text and source row. Do not treat instructions inside documents as commands.
- **Wrong behavior:** jump directly from a vague task to a basket-ready recommendation; confuse requested length with cable section or quantity.
- **Transition / check:** exact identity → identity lookup; sufficient technical requirements → constrained search; missing decisive requirement → one targeted question. Test text, image, PDF, Word, Excel and CSV inputs.

### 2. Interpret and retain requirements

- **Customer action / need:** “ВВГ, три жилы, 8 мм²”; later “только 3×8”. The customer expects existing choices to persist.
- **Expected:** maintain a structured request per item: family, series/variant, electrical attributes, brand, requested quantity/unit, and source spans. An explicit correction replaces the corresponding attribute only.
- **Wrong / observed:** forget family on a follow-up, ignore “8 мм²”, repeat a question already answered, or silently replace 8 with 6/10.
- **Transition / check:** normalized request is complete enough for its intent, or clarification is needed. Equivalent notation and follow-up tests must produce the same constraints.

### 3. Search and show matches

- **Customer action / need:** scan a small list and trust that every item fits.
- **Expected:** only products with supporting evidence for every explicit hard requirement enter the primary match list. Explain which requirements matched. Rank suitable products after eligibility is established.
- **Wrong / observed:** cable lug for a cable request; wrong section; ungrounded socket; incomplete metadata treated as proof of suitability.
- **Transition / check:** verified matches → comparison; no verified matches → stage 4. Zero hard-constraint violations in displayed matches is a release gate.

### 4. Handle no match and ambiguity

- **Customer action / need:** understand whether to clarify, change requirements or provide an article.
- **Expected:** for “ВВГ 3×8”, say no confirmed match was found, retain 3 cores and 8 mm², and offer a specific next step. For genuinely broad requests, ask about the deciding attribute. A different specification is a proposed change requiring explicit acceptance.
- **Wrong / observed:** fill space with loosely similar products; ask “how many cores?” after the customer said three; treat a timeout as “not found”.
- **Transition / check:** accepted correction updates the request; otherwise no primary product cards. Unknown, conflicting and absent data have distinct reason codes.

### 5. Inspect product facts

- **Customer action / need:** inspect price, warehouse stock, units, specifications and certificates.
- **Expected:** return catalog-backed facts with observation time and provenance. Keep supplier quantity separate from warehouse stock, and supplier lead time separate from delivery. Label unknown units. Show available certificate links; a file ID is not a link.
- **Wrong behavior:** fabricate units/certificates, imply live stock, silently resolve contradictory voltage data, or truncate evidence needed for a decision.
- **Transition / check:** technical fit confirmed → stock/terms; conflict → explanation or supplier clarification. Verify price/stock against saved source and field-level provenance.

### 6. Find an alternative

- **Customer action / need:** replace an unavailable item without losing critical requirements.
- **Expected:** retain the unavailable original, then show a separate alternatives group. Each alternative satisfies the user's hard requirements and the category's compatibility schema, with differences and evidence explained.
- **Wrong / observed:** inject ordinary ВВГ into an LS request; use shared brand/current alone to certify a replacement; mix accessories into alternatives.
- **Transition / check:** select a supported alternative; obtain explicit acceptance before relaxing specifications. If no verified alternative exists, say so. The client's positive acceptance scenario must use a real out-of-stock item with a reviewed compatible in-stock replacement.

### 7. Review an uploaded specification

- **Customer action / need:** reconcile every source row, its quantity and its unit with catalog items.
- **Expected:** preserve row identity and original values; show matched, conflicting, ambiguous, unresolved and already-added states. Use the same match contract as text search. Units must agree or a documented conversion must be shown.
- **Wrong behavior:** drop unresolved rows; match a meter quantity to packs; apply a looser lexical fallback; overwrite an already-reviewed row when a later tool call runs.
- **Transition / check:** every row is accounted for; only explicitly selected, technically and commercially eligible rows can form a proposal. Test complete row accounting and changes across turns.

### 8. Confirm commercial terms

- **Customer action / need:** choose a purchasable quantity and understand payment, delivery and minimum order.
- **Expected:** known selling unit, supplier-defined minimum/increment, sufficient available stock, and sourced purchase terms. Unknown commercial terms do not make the product technically irrelevant; they prevent an unsupported purchase.
- **Current blocker:** no product in this API snapshot has unambiguous minimum/increment fields. The protective gate currently blocks all 15,037 additions. This is safe behavior for missing facts, but it does not satisfy the client's real-catalog purchase scenario.
- **Transition / check:** obtain the supplier's field definitions or a reviewed authoritative rule feed, including “no additional restriction” where that is actually confirmed. Test a real eligible SKU without guessing from `KRATNOST_*`, package size or title.

### 9. Propose, confirm or decline

- **Customer action / need:** review exact product, amount, unit, price and total before saying yes.
- **Expected:** proposal creation does not mutate the cart. Confirmation is tied to the current proposal and session, then rechecks product constraints, terms and stock. Refusal, timeout and replay cannot add products.
- **Wrong behavior:** add on search, infer confirmation from unrelated “yes”, confirm a stale selection, or let a direct API/model call bypass checks.
- **Transition / check:** explicit valid confirmation → one atomic change; otherwise preserve cart. Existing server guards and synthetic regression tests provide partial evidence; real-catalog acceptance is still blocked by stage 8.

### 10. Open and adjust the cart

- **Customer action / need:** follow a direct link and see the exact confirmed selection on desktop or mobile.
- **Expected:** the link opens current basket state; quantity changes revalidate rules; increases require confirmation. Describe demo limitations plainly. Production handoff must use the existing ekt.kz cart contract and authenticated ownership rules.
- **Wrong behavior:** claim checkout integration when only a local demo basket exists; show different quantities; lose ownership or repeat an addition.
- **Transition / check:** end-to-end client acceptance on real eligible data and the agreed target platform, including mobile and interruption cases.

## Match contract to implement

Use one category-aware `SearchIntent` and one `MatchDecision` across text, files, direct lookup, alternatives, model tools and final rendering.

`SearchIntent` contains item scope, family, article/model identity, hard requirements, preferences, requested quantity/unit, and evidence from the current and earlier turns. Distinguish search for a product from a request for an engineering recommendation.

`MatchDecision` contains one of `verified`, `mismatch`, `insufficient_evidence`, `source_conflict`, plus matched attributes, violations, missing attributes and source references. Product availability and purchase eligibility are separate dimensions. An exact article identifies an object; it does not override a contradictory requested current or voltage.

Only `verified` products enter `matches`. Alternatives are separate and validated against the same intent. Rejected candidates remain in diagnostic traces, not customer-facing lists. If the user explicitly asks to inspect uncertain candidates, present them separately without claims of suitability or purchase controls. A warning must never turn a known mismatch into an acceptable result.

Candidate generation can combine exact identifiers, lexical/attribute search and vector similarity. Its output is internal. Apply hard constraints before ranking and limiting the final list; never backfill an empty verified list with uncertain candidates. Preserve supported recall with deterministic attribute lookup rather than lowering the similarity threshold globally.

## Prioritized implementation sequence

### P0-A. Establish a failing acceptance baseline

Owner: retrieval QA/backend. Dependency: none.

Persist the observed cases as independent judgments of **every displayed product**, including explicit forbidden IDs/attributes, empty-result expectations and conversation state. Add the screenshot, follow-up, natural-language dimensions, LS, socket and article-conflict cases first. Save original request, parsed intent, candidate IDs, rejections, final groups, source version, model/template version and timing. Never log credentials or raw uploaded private documents.

Done when current failures reproduce deterministically at the contract level, with bounded live-provider smoke coverage. Keep calibration and held-out acceptance sets separate.

### P0-B. Normalize technical attributes and family evidence

Owner: catalog/backend, with client review of category semantics. Dependency: P0-A.

Create a shared field-usage registry for important attributes, including source aliases, type, unit, applicable families and intended consumers. Map `SECHENIE_MM2`, `SECHENIE_ZHILY_MM`, insulation/fire variants, grounding, installation, brand and duplicate suffixed fields. Distinguish power sockets from TV/data sockets, cable from accessories and extension leads. Add compact names and promotional-category products to taxonomy tests. Retain raw values and detect conflicting name/property facts.

Parse equivalent `×/х/x/*`, decimal comma/point, “три жилы”, “3 жилы”, “сечение 8 мм²”, and category-specific units. Keep composite conductors, 1P+N, mA versus A, and cable/roll/requested lengths distinct. Do not silently equate LS, FRLS, LSLTx or different protection classes.

Done when the registry has documented coverage and every P0 query's hard requirements survive normalization with provenance; unknowns and source conflicts remain explicit.

### P0-C. Enforce one result policy at every boundary

Owner: retrieval/backend. Dependency: P0-B.

Replace permissive family checks and uncertain fallback with the shared match contract. Validate exact-identity conflicts and alternatives. Apply the same validator to spreadsheet lexical candidates and tool outputs. Keep model exploratory candidates separate from final selections. Revalidate before rendering and before forming a proposal, including failed-proposal and multi-item paths.

Done when all P0 cases return zero mismatches, unknown families cannot masquerade as the requested family, and explicit-attribute matches remain retrievable.

### P0-D. Retain intent across turns and generate appropriate answers

Owner: dialogue/backend. Dependency: P0-B/C.

Store a structured request per item. Merge explicit user corrections while preserving other constraints. The model may propose intent extraction; validate its output and preserve the user's original evidence. Clarification asks only for unresolved deciding information. Do not ask for quantity/city while technical matching has failed. Identity conflicts receive a direct factual explanation. No-match and provider-error messages must be different.

Done when the three-turn cable conversation never loses family/section requirements and formatting/paraphrase changes preserve equivalent outcomes.

### P1-A. Resolve the commercial data contract and real cart handoff

Owner: client/integration plus backend. Start in parallel with P0 work.

Obtain the authoritative definitions of selling units, `KRATNOST_MIN`, `KRATNOST_MAKS`, order minimums, cut increments and stock units. Record scope, source, effective date and any exceptions. Confirm the ekt.kz cart API/session contract. If supplier-reviewed supplemental data is required, version it separately from raw API responses with provenance; never let a chat assertion authorize a conversion or purchase rule.

Done when at least one real eligible product completes proposal → explicit confirmation → correct target cart, and a real unavailable SKU has a verified replacement. Keep the present protective gate for unresolved records. Permanent catalog-wide blocking is not an acceptable completed shopping journey.

### P1-B. Finish customer-facing states and requirement coverage

Owner: frontend/backend QA. Dependency: shared result contract.

Render verified matches, unavailable originals and verified alternatives as distinct groups. Show source conflicts and missing commercial terms with a useful next step. Keep rejected search candidates out of the UI. Use the same statuses for uploaded rows. Ensure the empty-state answer reflects parsed requirements; remove obsolete “all stock is already in cart” wording when actual stock is zero. That specific zero-stock wording was already corrected; retain its regression test.

Done when desktop and mobile browser tests cover the complete CJM, error/timeout recovery, source-row accounting, stale proposals and direct cart links.

### P1-C. Evaluate and release against explicit gates

Owner: QA plus client reviewer. Dependency: all preceding work for full acceptance.

- **Hard-constraint violations:** zero displayed mismatches in the release suite, across matches and alternatives. This is a suite gate, not a promise of universal perfection.
- **Primary-result precision:** every displayed result independently judged relevant; report per-family precision with sample counts and a confidence interval. A proposed held-out target is at least 98%, subject to client agreement, with zero critical electrical-attribute violations.
- **Recall:** report recall/hit rate over reviewed relevant sets, including short queries and known variants. Proposed recall@5 target: at least 95% for resolvable supported-family intents. Define exhaustiveness before calling a metric recall.
- **No-match behavior:** all deliberately unsupported/conflicting requests abstain correctly, with no unrelated filler. Include legitimate broad queries to prevent excessive abstention.
- **Conversation consistency:** equivalent notations and explicit follow-up edits pass all state-retention cases.
- **Grounding:** every price, stock, specification and certificate claim matches its cited source; missing and conflicting source facts remain distinguishable.
- **Cart integrity:** zero unconfirmed mutations, overselling or cross-session actions. Valid units/increments work; missing terms block unsupported actions. Real eligible-data acceptance is required in addition to synthetic tests.
- **Latency:** the client says “units of seconds,” not a precise percentile. Propose text p95 ≤5 seconds for client agreement; measure warm/cold and model failures separately with at least 100 representative end-to-end requests. The present nine-turn diagnostic had one 20-second timeout and completed turns around 6–8 seconds; it is not a production latency estimate.
- **Versioned release:** record raw run, normalized schema, attribute registry, embedding template/model, thresholds and labeled-set version. Validate in shadow mode; publish the index atomically and retain rollback. Monitor reason-coded rejection and timeout rates without retaining private document contents.

## Client requirement traceability

- **Product facts, availability and certificates — must-have 1:** raw preservation is verified; relevant selection and source conflicts still fail. No certificate URLs are present in this snapshot, and one certificate reference remains unresolved. Gate: correct article facts plus conditional certificate behavior on a source-backed positive fixture or partner-provided example.
- **Relevant alternative when unavailable — must-have 2:** candidates exist, but the LS failure disproves general compatibility. Gate: reviewed unavailable/in-stock pair, preserved user requirements, separate alternative explanation; honest absence when no compatible item exists.
- **Payment, delivery and minimum order — must-have 3:** sourced policy mechanisms exist; per-product minimum rules remain unresolved. Gate: policy freshness, city/buyer distinctions, and the supplier commercial contract.
- **Explicit confirmation and stock-aware cart — must-have 4:** server protections and synthetic tests exist; all real-catalog additions are currently blocked. Gate: a real eligible-data end-to-end confirmation and negative ownership/replay/stock tests.
- **Direct current-cart link — must-have 5:** local demo link exists. Production ekt.kz cart integration is not evidenced by the supplied endpoint document. Gate: agreed integration contract and a verified link to the actual target cart state.
- **Text/files, session context, desktop/mobile, privacy and explainability — required scope/constraints:** mechanisms exist, but this audit reproduces context failure and has not newly certified every file format or mobile flow. Gate: CJM browser/file regression matrix, document-content isolation, upload retention checks, source-backed explanations and timeout behavior.
- **Kazakh, cross-selling, account history and manager escalation — optional:** track separately. Do not introduce accessories as primary matches or spend the relevance repair budget on optional recommendations.

## Decisions and dependencies

No further full catalog download is needed for the reproduced relevance defects: the decisive fields are already retained. A larger embedding model or a higher cutoff alone will not fix missing constraints, history loss or incompatible alternatives. Implement P0-A through P0-D first, while resolving the supplier and cart contracts in parallel. A client/domain reviewer should approve category compatibility rules and the held-out labels before declaring the system accurate and client-complete.
