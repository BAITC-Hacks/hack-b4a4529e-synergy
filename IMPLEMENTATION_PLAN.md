# Complete the EKT client prototype

## Agreed delivery

Russian consultation and purchase selection using FastAPI, the static frontend, a downloaded EKT catalog, embedding search, anonymous sessions and the existing local cart. One application worker is required. Live website/basket integration, payment processing, accounts, Kazakh and automated manager handoff are outside this delivery.

## Implemented sequence

1. **Catalog provenance and normalization.** The downloader preserves complete detail JSON alongside the unchanged CSV schema. Certificate file IDs remain distinct from usable links. `app.refresh_catalog` retrieves selected details and actual product-page certificate links without constructing URLs. Ambiguous `KRATNOST_MIN` stays unknown; verified minimum and step fields are enforced at proposal and confirmation. Atomic index publication reports price, stock, certificate, unit and purchase-rule coverage.
2. **Purchase terms.** `app/policies.json` versions EKT's published conditions with verification date and source. The read-only `get_purchase_terms` tool answers payment, delivery and product minimum questions. The overlapping Алматы delivery thresholds are explained; sources are returned structurally and rendered in chat.
3. **Product answers and alternatives.** Technical families and normalized attributes drive comparisons. Conflicting known attributes are rejected. Structured matches, differences and unknowns distinguish supported alternatives from candidates requiring review. Product references persist for follow-ups. Missing certificates, specifications or stock are reported as missing data.
4. **Attachments.** Signature/MIME/parser validation precedes submission. Images use image inputs; PDF, DOC/DOCX, XLS/XLSX and CSV use native Responses file inputs. Limits: five files, 10 MB each, 20 MB total, 1,000 rows per sheet/CSV, 100 PDF pages. Corrupt/encrypted files receive clear errors. Office image limitations are shown. Structured rows preserve source references, quantities, candidate IDs and resolution state; original bytes are not retained in session history. Users explicitly select batches of at most 50 lines; all cart additions require fresh confirmation.
5. **UX and delivery evidence.** Chat shows certificate/policy links, snapshot information, attachment review/issues and a privacy notice. Failed submission retains the browser draft. `/api/ready` reports catalog availability/version/count. Timing logs contain route/method/status/duration/request ID, without chat text, uploads or credentials. Desktop/mobile confirmation and navigation checks and real-provider acceptance are recorded in `ACCEPTANCE.md`.

## Contracts

Existing chat, cart, anonymous session and proposal-ID routes remain. Additions: `get_purchase_terms`, `review_attachment_items`, structured source/review fields, richer product comparisons/certificates, `POST /api/cart/propose-items`, and read-only `GET /api/ready`. The model has no cart mutation tool. Server confirmation revalidates ownership, expiry, revision, catalog version, quantities, price and stock under the session lock.

## Acceptance and remaining dependencies

### Delivered UX follow-up — 2026-09-23

The header now offers a new chat while preserving the cart. Visible conversation history retains each answer's product cards independently of the model's limited context. Product selections accumulate across searches; users can edit quantities and confirm a combined proposal. Cart decreases validate and save directly; increases require confirmation of the added quantity. Users can type the next message during a response or stop processing and recover their draft. Every product offers an explicit alternatives action with factual comparison or an honest no-match answer. Attachment review selections and quantity drafts survive rerenders and refresh within the same browser tab and chat.

Added routes: `POST /api/chat/new`, `POST /api/chat/stop`, `PUT /api/selection`, `POST /api/products/{id}/alternatives`, and `POST /api/cart/quantity`. The read-only `get_alternatives` model tool supports explicit follow-ups. Model turns run against isolated session copies and publish only while their request, chat and cart revision remain current; stopped or superseded results cannot change the cart or conversation. An already-sent provider request can still finish remotely.

Verification: 136 automated tests passed, including 11 focused UX tests. Desktop and mobile browser journeys covered selection, both quantity directions, confirmation, refresh, new chat, explicit alternatives, attachment draft retention, typing during a response and cancellation. Detailed evidence and source-data limitations remain in `ACCEPTANCE.md`.

See `ACCEPTANCE.md` for dated results, measured latency and reproducible commands. Automated tests alone do not establish client acceptance. Real source examples currently cover certificate retrieval; verified minimum/step enforcement is tested with explicitly synthetic fixtures, because those source fields are unavailable. No production compatibility is claimed.

## Future same-origin website integration

Serve the widget and its backend under EKT's origin, for example `/assistant/` and `/assistant/api/`, behind the website's HTTPS proxy. Replace the local cart adapter with an EKT-owned server API after documenting authentication/session mapping, CSRF, product IDs, live price/stock checks and idempotency. Keep proposal IDs bound to the website session and require confirmation after any changed quote. Return the actual site's cart URL only after its API acknowledges the addition. Validate staging contracts, concurrency, expiry, duplicate submissions and rollback before rollout. This approach is a future design; the prototype has not been tested against a production basket API.
