# EKT catalog consultant

A Russian-language shopping assistant for the [ekt.kz](https://ekt.kz) catalog. It searches a downloaded catalog by article or description, shows product details and stock from that snapshot, and prepares a local cart. Adding items always requires explicit confirmation. This is a hackathon prototype; it does not place an order or reserve stock.

## Reviewer quick start

The automated tests need **no API keys, catalog download, or network access** after dependencies are installed:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pytest -q
```

Use Python 3.12. On Windows, run these commands in WSL/Ubuntu: the catalog downloader uses Unix file locking. CI runs the same test command on every push and pull request.

To try the live UI, you need an OpenAI API key and EKT catalog API credentials. The latter are needed only to obtain the catalog snapshot; tests use fixtures. Credentials and downloaded data are not committed.

## Run the live demo locally

```bash
cp .env.example .env
# Fill OPENAI_API_KEY, EKT_API_USER, and EKT_API_PASSWORD in .env.
python download_ekt.py
python -m app.index_build --limit 500
python main.py
```

Run these commands after installing dependencies as above. Open [http://localhost:8000](http://localhost:8000). The cart is at `/cart`. `GET /api/ready` returns HTTP 200 with the catalog version and product count when the index is usable, or HTTP 503 when it is missing. The sample index command embeds 500 downloaded products; omit `--limit 500` to index the full snapshot. Indexing calls the embedding API and may incur cost. Re-running the build reuses cached embeddings.

The downloader resumes interrupted downloads. If you already have `data/ekt/products.csv`, you can skip it. Change `EMBED_MODEL` only if you also rebuild the index. Set `CHAT_MODEL` to a model available to your API account.

## Run with Docker Compose

This is a repeatable deployment path for a host with Docker Compose. Copy the repository and create a `.env` file as above, then run:

```bash
docker compose build
docker compose run --rm app python download_ekt.py
docker compose run --rm app python -m app.index_build --limit 500
docker compose up -d
curl -f http://localhost:8000/api/ready
```

Open `http://localhost:8000` on the Docker host, or set `PORT` in your shell before `docker compose up` to change the local port. Compose binds to loopback so traffic reaches a public deployment through your reverse proxy. It keeps the downloaded catalog, embedding cache, and active index in the `catalog_data` volume, so rebuilding the image does not erase them. Run one app worker: sessions and carts are stored in process memory. To update the snapshot, rerun the download and index commands, then check `/api/ready`.

For a public URL, put the app behind an HTTPS reverse proxy, forward the original host and scheme, and set `SECURE_COOKIES=1` in `.env` before restarting. Keep the API keys and catalog volume on the server. The app needs persistent storage at `/app/data`; an ephemeral container filesystem loses the catalog on restart. Do not send payment details through the demo.

## Five-minute review flow

1. Ask for an article, product name, or description, such as “Автоматический выключатель 16 А”.
2. Request a quantity for an in-stock item or use its **Добавить в корзину** button.
3. Decline the proposal; the cart should remain empty.
4. Request it again, confirm with **Добавить** or “да, добавь”, then open `/cart`.
5. Refresh the cart: confirmed items remain in the same browser session.

Exact article matches avoid an embedding call. Natural-language searches use the local embedding index. The model can search and prepare a proposal, but only the server's confirmation handler changes the cart. A proposal expires after 10 minutes; prices and quantities are checked again on confirmation.

## Scope and checks

`app/` contains the FastAPI API, catalog search, model tools, and cart rules. `static/` contains the browser UI. `download_ekt.py` saves catalog records under `data/ekt/`; `python -m app.index_build` publishes a validated index under `data/index/`. These generated directories and `.env` are ignored by Git.

```bash
python -m pytest -q
```

The tests cover search, index publication, model tool restrictions, cart proposals and confirmation, stock checks, session ownership, concurrent or duplicate confirmation, and API behavior. They mock external services.

There is no login. An anonymous cookie connects chat and cart, and a server restart clears those sessions. Catalog prices and stock are snapshot values. Missing price or stock prevents additions. The attachment control accepts images, PDF, Word, Excel, and CSV within the UI's size limits; provider support for those file types is required. The core text flow was verified manually; attachment handling is not a substitute for checking the source file.
