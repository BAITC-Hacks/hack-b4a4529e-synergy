# EKT prototype acceptance — 2026-09-23

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
