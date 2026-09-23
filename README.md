# EKT catalog consultant

A Russian-language shopping assistant for the [ekt.kz](https://ekt.kz) catalog. It searches a downloaded catalog by article or description, shows product details and stock from that snapshot, and prepares a local cart. Adding items always requires explicit confirmation. This is a hackathon prototype; it does not place an order or reserve stock.

## Reviewer quick start

The automated tests need **no API keys, catalog download, or network access** after dependencies are installed:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt -c constraints.txt
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
2. Request a quantity for an in-stock item or use its **Выбрать** button.
3. Decline the proposal; the cart should remain empty.
4. Request it again, confirm with **Добавить в корзину** or “да, добавь”, then open `/cart`.
5. Refresh the cart: confirmed items remain in the same browser session.

Exact article matches avoid an embedding call. Natural-language searches use the local embedding index. The model can search and prepare a proposal, but only the server's confirmation handler changes the cart. A proposal expires after 10 minutes; prices and quantities are checked again on confirmation.

The product card's quantity control uses server-calculated minimum, multiple, and remaining stock. Missing requested technical specifications are marked for clarification. Monetary API values are decimal strings; the displayed price and totals come from server-formatted labels.

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

Normalized products retain `source_fields` and `source_response` for provenance, complete properties and offers, and warehouse IDs. Explicit cord/roll lengths are exposed in `lengths` with original values, source fields and parsed metres. `embedded_specifications` decodes recognized embedded JSON without changing the original string. A positive `METRAZHNYY_TOVAR` flag supplies the selling unit `м` when an explicit unit is unavailable. Package information, stock, length and purchase increments remain distinct; numbers in product names and `KRATNOST_MAKS` are not guessed to be lengths.

Index publication is atomic. Metadata records the source run, field coverage, model, checksum and build version. Certificate IDs such as `FILES_CERTIFICATES` are not links. `KRATNOST_MIN` alone remains supplier-unconfirmed; known purchase rules are enforced when proposing and confirming. The legacy targeted refresh helper refuses to modify published raw archives: use a fresh complete run instead. Synthetic fixtures stay in tests and are never mixed into the real catalog.

Configure `EKT_API_USER` and `EKT_API_PASSWORD` through the environment or ignored `.env`, not source code. `docs/` is ignored because local client documents can contain credentials. Runtime timing logs record request ID, method, route template, status and elapsed seconds.

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
