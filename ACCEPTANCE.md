# EKT prototype acceptance — 2026-09-23

## Purchase-unit and quantity-rule verification — 2026-09-23

- **157 automated tests passed**, including 17 purchase-rule regression cases; JavaScript syntax and diff checks passed. Test purchase fixtures now explicitly supply confirmed units, minimums and increments.
- The active catalog remains version `39bc1ec755e043d8bcbabe85b98deecb`, with all 15,037 products. Of these, 853 have recognized selling units, and none have unambiguous minimum/increment fields. All currently require supplier clarification before adding; search, technical details, prices and stock remain available.
- Browser checks on real products passed: `151100015_` displays an unconfirmed selling unit beside price and stock; `050300044_` retains metres and explains its unconfirmed minimum and cut increment. Neither shows a quantity selector or selection button.
- After restarting the local app, direct HTTP purchase attempts for both products returned 409 and left the cart empty. Readiness returned 200 with the unchanged catalog version. The generated result is `data/acceptance/purchase-rules.json`.
- Confirmation and quantity edits recheck current rules. Fixture coverage proves that a documented 0.25-metre increment permits valid fractional amounts and rejects incompatible amounts. Earlier live cart-addition results below predate this stricter eligibility check.

## UX follow-up — 2026-09-23

- **136 automated tests passed** (12.24 s), including 11 new UX/API regression tests. JavaScript syntax checks and `git diff --check` passed.
- **Desktop browser, isolated synthetic catalog:** selected two products across separate searches, refreshed without losing the selection or either answer's cards, edited a quantity, generated a fresh two-line proposal, and confirmed it. The cart increased only after confirmation.
- **Cart browser checks:** increasing a line from 2 to 3 displayed “ещё 1” and the additional cost while retaining quantity 2 until confirmation. Decreasing from 3 to 1 saved directly. Starting a new chat cleared the conversation and selection while retaining the two confirmed cart lines.
- **Attachment review:** a two-line synthetic CSV was processed by the configured live model. A checked row and edited quantity 4 survived an alternatives response and a page refresh. These fixtures remained separate from the real catalog.
- **Mobile, 390×844:** new-chat and stop controls were visually inspected; no horizontal overflow was observed. During a real provider request, typing remained available. Stopping restored the submitted message alongside the next draft and discarded the late response.
- **Real catalog smoke check:** readiness succeeded with 15,037 products at version `27283884a7b14cb58a4860033de83726`. Exact lookup of `151100015_` displayed the new card actions. The in-stock alternatives action was exercised against both the isolated fixture catalog and real data.
- Model history remains bounded to 20 text messages; visible messages retain their product/source snapshots for the lifetime of the anonymous session. “New chat” starts over without archiving old chats. Sessions still expire or clear on restart. Stopping cannot recall a provider call already sent, but cancelled work cannot publish results or mutate proposals/cart state.

The earlier prototype acceptance below remains a historical record of its original catalog snapshot and latency samples; this UX follow-up does not claim a new latency percentile.

## Embedding and retrieval follow-up — 2026-09-23

- Rebuilt all 15,037 real products with `text-embedding-3-small`, 1,536 dimensions and embedding template 1. Active version: `27283884a7b14cb58a4860033de83726`. Verified that every active product's current token input has a matching cache entry. Readiness returned HTTP 200 with this version and product count.
- Text uses an independent allowlist of readable identity and technical fields; operational fields, media IDs and promotional paths are excluded. Token budgets replace character slicing; descriptions follow technical fields. The rebuild used 5,609,754 input tokens, at most 2,049 per product, so no product was truncated. An initial provider rate limit interrupted indexing; resumption reused all 5,056 completed cached vectors. The builder now allows six SDK retries.
- **Passed: 140 automated tests**, plus dependency and diff checks. Coverage includes stock-only cache reuse, token and batch limits, provider response ordering, outdated template rejection, unknown articles, abbreviated luminaires and known model-code lookup.
- **Passed: 21/21 direct live retrieval cases** at cutoff 0.55: 14 positive cases hit a labeled target in the top five, and seven no-match cases returned no products. MRR@5 over the positive cases was 0.929. Results, model, snapshot and label checksum are in ignored `data/acceptance/retrieval.json`; reproduce with `python -m scripts.retrieval_eval`.
- The lower 0.30 cutoff returned unrelated results for food queries. Automotive tire paraphrases also matched electrical busbars at 0.45. The final regression set includes those paraphrases and broad valid catalog requests. A category filter incorrectly rejected the highest-ranked NPP luminaire; it now recognizes the structured luminaire type.
- Full chat checks at 0.55 initially passed **19/20**; the remaining perforator request intermittently returned no products. A bare model code such as `HB-20-24` was treated as an unknown article. Known model codes now resolve from actual product names without weakening explicit article lookup. Both targeted live model regressions subsequently passed **2/2**, with deterministic unit coverage of the tool-query path. The complete chat sample was not rerun after this final correction. Reports: `data/acceptance/embedding-chat-regression-final.json` and `embedding-model-regressions.json`.
- These manually reviewed cases are a small regression set. Validation failures used for fixes are no longer an untouched evaluation set. Positive target IDs are not exhaustive relevance labels; these results do not establish catalog-wide precision/recall or general chat reliability. The earlier acceptance records below describe the previous index.

## Environment and provenance

- Python 3.12 on Ubuntu/WSL, one FastAPI worker, Russian anonymous sessions, local cart.
- Configured chat model: `gpt-5.6-luna`; embeddings: `text-embedding-3-small`, 1,536 dimensions.
- Real catalog: 15,037 products; snapshot `2c9b5ace8a534e6c80a28fa7a2a240d8`, built 2026-09-23 10:52:01 UTC. All indexed rows have source price and stock values; this is not a live availability guarantee. No synthetic fixture products are present in the active snapshot.
- Source coverage: 44 known units; one product with two usable certificate links; zero verified minimum quantities or purchase multiples. The remaining units/rules are explicitly unknown. Only one refreshed detail record has an observation timestamp; the original source update time is unknown.
- Product `21449`, article `010500006_`, has certificate/declaration links recovered from its actual EKT product page. Numeric file identifiers were not converted into guessed URLs.
- Purchase terms are versioned in `app/policies.json` with [EKT's source page](https://ekt.kz/about/information/) and verification date 2026-09-23. Its conflicting Алматы thresholds remain visible.

## Requirement results

1. **Product consultation — passed for available source data.** Real-provider scenarios cover exact articles, descriptions, warehouse follow-ups, specifications and certificate follow-ups. Mobile chat renders both real certificate links for `010500006_`. Missing certificate links and unknown product rules are explicitly reported. **Source-dependent:** most certificate links, units, and all minimum/multiple fields remain unavailable; the prototype cannot supply facts absent from the source.
2. **Alternatives — passed within documented technical coverage.** Real-provider acceptance retrieves an in-stock candidate for an unavailable product. Tests reject conflicting current, voltage, trip curves and known attributes; equivalent units and promotional-category cases are covered. Results contain matching attributes, differences and unknowns. Families without a complete compatibility definition remain candidates requiring review, not verified replacements. Synthetic tests cover complete known compatibility.
3. **Purchase terms — passed.** Payment for individuals/companies, company documents, city-dependent delivery, unspecified city and product minimum questions use structured source-backed results. The browser displays the source link and both conflicting Алматы thresholds. Universal minimum order amounts and ambiguous product rules are not invented.
4. **Confirmed selection — passed.** Existing ownership, stale proposal, stock, concurrency, duplicate-confirmation, session-isolation and atomic-publication tests pass. Browser refusal leaves the cart unchanged. Button and text confirmations each add exactly their displayed selection. Uploads and embedded instructions cannot confirm or prepare cart additions during upload processing. Synthetic fixtures verify minimum and multiple enforcement at proposal and confirmation; **real-source verification of those rules is unavailable**.
5. **Working cart link — passed for the agreed local cart.** Browser navigation and refresh preserve confirmed contents in the same session. Desktop keyboard refusal/button confirmation followed by mobile “да, добавь” resulted in quantity 2 of article `151100015_`, total 21,180 ₸. Live EKT basket integration remains outside scope.

## Attachments and context

- **Passed:** real JPEG, PDF, DOC, DOCX, XLS, XLSX and CSV requests against the configured provider, with expected real product IDs and requested quantity 2 extracted. The eighth scenario is a CSV containing an instruction to bypass confirmation; cart mutation remained blocked.
- **Passed:** a four-row synthetic CSV produced two resolved articles, one unresolved article and one ambiguous description. All four source references and quantities (2, 3, 1, 5) survived review. A later request prepared only the two resolved rows with their original quantities and still required fresh confirmation.
- The mixed specification initially exposed unknown articles being treated as semantic descriptions. The resolver now distinguishes article queries; the repeated live scenario passed. A regression test ensures unknown articles never become semantic substitutes.
- **Passed:** invalid signatures/MIME types, corrupt legacy/Office files, encrypted PDF, spreadsheet/CSV row limits and upload confirmation attacks are covered by tests. Incomplete model extraction rolls back the review and asks the user to split the document. The UI keeps file and message drafts on errors.
- **Observed browser result:** a deliberately invalid PDF showed “Файл не является PDF.” while preserving the filename and message. It did not change the cart. Office image limitations and the provider-processing privacy notice are visible in the information dialog.
- Synthetic documents are stored only in ignored `data/acceptance/`. The legacy DOC fixture is adapted from [Apache POI's test document](https://github.com/apache/poi/blob/trunk/test-data/document/simple.doc), under Apache-2.0, by substituting a synthetic article/quantity string. Tests do not add these fixtures to `data/ekt/`.
- These small representative files establish format support, not perfect extraction of every possible document layout. Users must review source references and quantities; ambiguous matches require explicit choice. Large selections are limited to 50 explicitly chosen lines per proposal.

## Browser and clean setup

- **Passed:** desktop chat → proposal → keyboard refusal → fresh proposal → keyboard button confirmation → cart navigation → refresh.
- **Passed:** mobile 390×844 chat → fresh proposal → Enter-submitted text confirmation → cart → refresh. Chat and cart had `scrollWidth == innerWidth == 390`; the cart screenshot was visually inspected.
- **Passed:** attachment review → explicit row checkbox → batch proposal → cancellation/confirmation, privacy dialog keyboard closing, certificate links, purchase-term source links and failed-upload draft retention.
- **Passed:** 89 automated tests in the working environment (1.53 seconds) and the freshly created virtual environment (1.49 seconds). Clean dependency installation succeeded; `pip check` reported no broken requirements. Tests do not need catalog credentials or model access. Docker/public HTTPS deployment was not independently exercised in this acceptance run.
- **Passed:** readiness reports the active version and product count; unavailable-index behavior is covered by API tests. Timing records use route templates, status and duration, without message text or uploaded data. Raw uploads are not retained in session history; extracted review data and messages remain in anonymous session memory until expiry/restart. Provider retention is governed separately by the provider.

## Live reliability and latency

Measurements are sequential, warmed, local runs with the configured model. Timings include all model/tool rounds and any embedding calls. This is a small engineering sample, not a production percentile guarantee.

- Direct `run_turn` text sample: **28/30 passed**, p50 **4.24 s**, p95 **20.23 s**. Two calls timed out (`APITimeoutError`: semantic socket query and nonexistent-article query). **Failed:** the p95 ≤5 s text target. An earlier run passed 30/30 with p95 6.30 s, also above target; the variability is material.
- Direct attachment sample: **8/8 passed**, p50 **7.31 s**, p95 **16.72 s**. **Passed:** p95 ≤30 s for these small representative attachments. The mixed four-row specification took **23.17 s** for upload processing.
- Full HTTP text sample: p50 **4.45 s**, p95 **10.79 s**, all 30 requests completed. The original structural assertions passed, but manual answer inspection found one incorrect missing-article response with unrelated candidates. This was fixed in both search and chat routing, and the acceptance assertion now requires zero matches for an unknown article. Treat the original run as **29/30 semantically accepted**, not 30/30. **Failed:** the p95 ≤5 s target.
- Full HTTP attachment sample: **8/8 passed**, p50 **7.16 s**, p95 **9.42 s**. These measurements include multipart upload, validation, model/tool processing and response receipt on localhost. **Passed:** the p95 ≤30 s target for the tested small files. Reports are `data/acceptance/http-text.json` and `http-files.json`.
- Final targeted HTTP regression checks: **3/3 passed** against a freshly started process: missing articles (1.36 s, no unrelated candidates), the previously timed-out socket query (6.35 s), and mixed-file status/quantity preservation (10.68 s). All four extracted rows and their exact statuses/quantities passed additional assertions. Reproduce with `scripts.acceptance_regressions`; the report is `data/acceptance/regressions.json`. The complete percentile sample was not rerun after these targeted fixes.

Reproduce with the commands in README. Local JSON reports retain only synthetic acceptance prompts and their responses; normal runtime timing logs contain no such content. To improve ordinary text latency, profile model/tool round trips and remove redundant generation where the final answer is already rendered from structured facts, while preserving compound questions and proposal behavior. Meeting the 5-second target remains open.

## Delivery boundary

The required functionality is implemented, with the explicit source-data and reliability/latency qualifications above. Payment processing, accounts, Kazakh, manager automation and live website/basket integration were excluded by agreement. The future same-origin integration outline in `IMPLEMENTATION_PLAN.md` is a design proposal, not a tested production contract.
