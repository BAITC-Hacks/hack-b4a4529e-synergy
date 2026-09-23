# EKT catalog consultant

A Russian-language shopping assistant for the [ekt.kz](https://ekt.kz) catalog. It searches a downloaded catalog by article or description, shows product details and stock from that snapshot, and prepares a local cart. Adding items always requires explicit confirmation. This is a hackathon prototype; it does not place an order or reserve stock.

The app uses a snapshot because semantic search needs a prebuilt embedding index for the catalog. It calls the EKT API when downloading or refreshing data, not for each chat request. This makes searches independent of EKT API availability during a demo, but prices and stock can become stale.

## Reviewer quick start

The automated tests need **no API keys, catalog download, or network access** after dependencies and the public tokenizer vocabulary are cached:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt -c constraints.txt
python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"
python -m pytest -q
```

Use Python 3.12. On Windows, run these commands in WSL/Ubuntu: the catalog downloader uses Unix file locking. CI runs the same test command on every push and pull request.

To try the live UI, you need an OpenAI API key and EKT catalog API credentials. The latter are needed only to obtain the catalog snapshot; tests use fixtures. Credentials and downloaded data are not committed.

## Run the live demo locally

```bash
cp .env.example .env
# Fill OPENAI_API_KEY, EKT_API_USER, and EKT_API_PASSWORD in .env.
python download_ekt.py
python -m app.index_build
python main.py
```

Run these commands after installing dependencies as above. Open [http://localhost:8000](http://localhost:8000). The cart is at `/cart`. `GET /api/ready` returns HTTP 200 with the catalog version and product count when the index is usable, or HTTP 503 when it is missing. The index command embeds all downloaded products. Indexing calls the embedding API and may incur cost. Re-running the build reuses cached embeddings.

The downloader resumes interrupted raw-response downloads. Existing CSV rows do not count as complete: run the downloader once to backfill the full API data. Use `python download_ekt.py --new` for a fresh snapshot. Change `EMBED_MODEL` only if you also rebuild the index. Set `CHAT_MODEL` to a model available to your API account.

## Run with Docker Compose

This is a repeatable deployment path for a host with Docker Compose. Copy the repository and create a `.env` file as above, then run:

```bash
docker compose build
docker compose run --rm app python download_ekt.py
docker compose run --rm app python -m app.index_build
docker compose up -d
curl -f http://localhost:8000/api/ready
```

Open `http://localhost:8000` on the Docker host, or set `PORT` in your shell before `docker compose up` to change the local port. Compose binds to loopback so traffic reaches a public deployment through your reverse proxy. It keeps the downloaded catalog, embedding cache, and active index in the `catalog_data` volume, so rebuilding the image does not erase them. Run one app worker: sessions and carts are stored in process memory. To update the snapshot, rerun the download and index commands, then check `/api/ready`.

For a public URL, put the app behind an HTTPS reverse proxy, forward the original host and scheme, and set `SECURE_COOKIES=1` in `.env` before restarting. Keep the API keys and catalog volume on the server. The app needs persistent storage at `/app/data`; an ephemeral container filesystem loses the catalog on restart. Do not send payment details through the demo.

## Five-minute review flow

1. Ask for an article, product name, or description, such as “Автоматический выключатель 16 А”.
2. Use **Выбрать** on two available products. Both stay in the selection while you search or refresh. Edit quantities if needed.
3. Click **Проверить и добавить выбранные**, then decline the proposal; the cart should remain empty.
4. Prepare the selection again, confirm with **Добавить в корзину** or “да, добавь”, then open `/cart`.
5. Refresh the cart: confirmed items remain in the same browser session.

Exact article matches avoid an embedding call. Natural-language searches use the local embedding index. The model can search and prepare a proposal, but only the server's confirmation handler changes the cart. A proposal expires after 10 minutes; prices and quantities are checked again on confirmation.

The product card's quantity control uses server-calculated minimum, multiple, and remaining stock. Missing requested technical specifications are marked for clarification. Monetary API values are decimal strings; the displayed price and totals come from server-formatted labels.

Adding a product requires a confirmed selling unit and explicit positive minimum quantity and purchase increment. Unknown units are displayed as “единица продажи не подтверждена”. A metre-based selling unit does not authorize arbitrary cut lengths. Missing terms block selection, proposals, confirmation and quantity edits on the server; products remain searchable. `KRATNOST_MIN`, `KRATNOST_MAKS`, package lengths and numbers in names are not substitutes for documented purchase rules. The current API archive does not contain unambiguous minimum/increment fields, so its products require supplier clarification before adding. Confirmed terms must come from catalog ingestion, not chat assertions or browser parameters.

### Everyday chat controls

- **Новый чат** clears the conversation, pending proposal, selection and attachment review while keeping confirmed cart items. It also invalidates an in-flight response. It starts a fresh conversation; it does not archive previous conversations.
- Each answer retains its own product cards and source links. Visible history lasts for the anonymous session; only the last 20 text messages are sent as model history. Sessions still expire after inactivity and are cleared on server restart.
- **Остановить** discards a pending response and restores the submitted draft. You can type the next message while waiting. A provider call already in progress may finish, but its late results cannot change the chat, proposal or cart.
- **Показать аналоги** works on available and unavailable products and provides matching attributes, differences and unknowns. The same read-only capability is available to the assistant.
- Cart quantities are editable. Decreases are validated and saved; increases show the additional quantity and cost and require confirmation.
- Specification checkboxes, candidate choices and edited quantities survive rerenders. Review drafts also survive refresh in the same tab via session storage; original file bytes are never saved there.

## Scope and checks

`app/` contains the FastAPI API, catalog search, model tools, and cart rules. `static/` contains the browser UI. `download_ekt.py` saves catalog records under `data/ekt/`; `python -m app.index_build` publishes a validated index under `data/index/`. These generated directories and `.env` are ignored by Git.

After a raw archive is published, the index builder reads only that archive and verifies every response checksum. Older CSV/detail files cannot overwrite its values or fill in obsolete stock. Legacy file precedence is retained only for old installations and test fixtures without an archive pointer. Fetch timestamps identify when responses were observed; the provider's own stock update time remains unknown.

```bash
python -m pytest -q
```

The tests cover search, index publication, model tool restrictions, cart proposals and confirmation, stock checks, session ownership, concurrent or duplicate confirmation, and API behavior. They mock external services.

There is no login. An anonymous cookie connects chat and cart, and a server restart clears those sessions. Localhost ports use separate session cookies so simultaneous demos do not interfere. Catalog prices and stock are snapshot values. Missing price or stock prevents additions. See [acceptance results](ACCEPTANCE.md) for real-provider/browser checks, measured latency and source-data limitations.

## Consultation and specification workflow

- Payment and delivery answers use `app/policies.json`, verified against [EKT's published information](https://ekt.kz/about/information/) on 2026-09-23. Overlapping Алматы delivery thresholds are reported for supplier confirmation. The demo does not accept payment.
- Alternatives compare technical families and electrical/mechanical attributes. Conflicting known attributes exclude candidates; missing compatibility information stays visible. These comparisons do not certify suitability for an installation.
- Upload JPEG, PDF, DOC/DOCX, XLS/XLSX or CSV: at most five files, 10 MB per file and 20 MB total. Spreadsheets/CSV are limited to 1,000 rows per sheet; split larger inputs. PDF is limited to 100 pages. Encrypted, corrupt and inconsistent file types are rejected before model submission. Office embedded images require PDF export or separate images.
- Review extracted rows, source references and quantities. Exact, ambiguous and unresolved matches have distinct statuses. Check the desired rows and prepare a batch of at most 50. Every batch requires fresh button or text confirmation.
- Uploaded bytes are forwarded to the configured model provider with `store=False`; the application does not save those bytes or log file contents. Session memory retains conversation text and extracted review information. This does not override the provider's retention policy. Failed processing keeps the browser draft for retry. Do not upload payment details or confidential documents.

## Data provenance and refresh

Each download run lives in `data/ekt/raw/<run-id>/`. `pages/` and `details/` contain unchanged response bodies; `manifest.json` holds request parameters, fetch times, HTTP statuses and SHA-256 checksums separately. All JSON fields are retained, including unknown fields, nested arrays, offers, warehouse IDs, nulls and embedded specifications. Linked files are preserved as URLs; their binary contents are not downloaded.

The downloader uses 500 records per page and stops at a short or empty page. Repeated IDs/pages fail the run, since the upstream API can wrap back to its first page. Detail URLs must use the configured EKT API endpoint; redirects are disabled. Downloads resume from verified raw responses, never CSV IDs. `--concurrency` controls simultaneous requests (default 16).

`data/ekt/download.json` points to the resumable run. `data/ekt/current.json` changes only after every listed product has a valid detail response. `report.json` records completeness, field coverage and IDs no longer listed. Derived `products.json` and `products.csv` live inside the run; CSV nested cells use JSON instead of delimiter flattening. The existing application index remains active until a new index build succeeds:

```bash
python download_ekt.py --new
# If interrupted, resume without --new:
python download_ekt.py
python -m app.index_build
```

Normalized products retain `source_fields` and `source_response` for provenance, complete properties and offers, and warehouse IDs. Explicit cord/roll lengths are exposed in `lengths` with original values, source fields and parsed metres. `embedded_specifications` decodes recognized embedded JSON without changing the original string. A positive `METRAZHNYY_TOVAR` flag supplies the selling unit `м` when an explicit unit is unavailable. Supplier quantity and lead time are exposed separately in `supplier_availability`; they never increase catalog stock or promise delivery to the customer. Expanded product cards show the full description. Package information, stock, length and purchase increments remain distinct; numbers in product names and `KRATNOST_MAKS` are not guessed to be lengths.

Index publication is atomic. Metadata records the source run, field coverage, model, checksum and build version. Certificate IDs such as `FILES_CERTIFICATES` are not links. `KRATNOST_MIN` alone remains supplier-unconfirmed; known purchase rules are enforced when proposing and confirming. The legacy targeted refresh helper refuses to modify published raw archives: use a fresh complete run instead. Synthetic fixtures stay in tests and are never mixed into the real catalog.

Configure `EKT_API_USER` and `EKT_API_PASSWORD` through the environment or ignored `.env`, not source code. `docs/` is ignored because local client documents can contain credentials. Runtime timing logs record request ID, method, route template, status and elapsed seconds.

## Embedding text and retrieval checks

The active vectors are `data/index/<version>/embeddings.npy`, with product records and checksummed metadata alongside them. `current.json` selects the version; `embedding-cache.sqlite3` reuses vectors for identical model/token inputs. Query vectors use a bounded in-process cache. Docker keeps the catalog and index in `catalog_data`.

`app/embedding_text.py` owns an explicit allowlist of readable identity and technical fields, independently of UI labels. It omits stock, supplier lead times, price-display flags, media IDs, barcodes, promotional category paths and unknown properties. Original fields remain in product metadata. Stable fields are sorted, HTML is removed, and descriptions come last. Each product uses at most 8,192 tokens; requests contain at most 64 inputs and 300,000 tokens. Overlong search queries are rejected instead of silently losing customer requirements.

Increment `EMBEDDING_TEMPLATE_VERSION` when changing field selection, labels, cleanup, ordering or truncation, then rebuild before starting the updated app. An index with an absent or different template version is rejected. Index builds reuse cached vectors and only replace the active pointer after successful validation. The Docker image and CI cache the tokenizer vocabulary during setup, so first-use tokenization needs no download there.

Run the labeled Russian retrieval regression independently of the chat model:

```bash
python -m scripts.retrieval_eval
# Diagnose thresholds; calibration and validation groups are reported separately:
python -m scripts.retrieval_eval --thresholds 0.30 0.45 0.50 0.55
```

These commands call the embedding API. Reports go to ignored `data/acceptance/retrieval.json` and record the snapshot, label hash, hit@5, MRR@5 and no-match accuracy. The default run exits nonzero on any failed case. Labels in `scripts/retrieval_cases.json` are manually selected real product IDs; missing products fail explicitly and require label review. Positive targets are not exhaustive relevance judgments, so the report does not claim precision or recall over the entire catalog. This small regression set is not a production quality guarantee. Full chat acceptance also requires these queries to retrieve the labeled products or return no products for negative cases.

The current cutoff is 0.55 for `text-embedding-3-small` with template 1. Lower cutoffs returned unrelated products for out-of-catalog requests, including automotive tire queries matching electrical busbars. The regression set includes paraphrases and broad valid searches to check this precision/recall tradeoff. Recheck the cutoff when changing the model, text template or catalog. Validation cases used to diagnose a failure become regression cases after the fix; broader independent evaluation is still needed before claiming general search quality.

Bare model codes already present in product names (for example `HB-20-24`) are looked up directly and marked `model_match`. They are not mislabeled as article matches; real article matches take priority, explicit article requests still only use article fields, and multiple products with the same model code require clarification. Structured luminaire types also prevent abbreviated product names from being rejected by category filtering.

## Reproduce live acceptance

These commands call the configured OpenAI models and require a built catalog. They generate clearly synthetic requests under ignored `data/acceptance/`; selected product records remain real. Development dependencies are only needed for fixture generation.

```bash
python -m pip install -r requirements-dev.txt -c constraints.txt
python -m scripts.acceptance --text-only
python -m scripts.acceptance --files-only
python -m scripts.acceptance_specification
# Measure complete HTTP request/response latency with the application running:
python -m scripts.acceptance --text-only --http-url http://localhost:8000
python -m scripts.acceptance --files-only --http-url http://localhost:8000
python -m scripts.acceptance_regressions --http-url http://localhost:8000
```

The legacy DOC fixture derives from Apache POI's Apache-2.0 test document and substitutes a synthetic article/quantity request; its source is documented in the script. Results include model, snapshot, behavior checks and end-to-end `run_turn` timings. HTTP timing middleware separately measures complete API requests. See [implementation record and future same-origin integration](IMPLEMENTATION_PLAN.md) for architecture, contracts and delivery boundaries.
