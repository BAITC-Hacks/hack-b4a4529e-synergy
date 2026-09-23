# EKT catalog consultant

Russian chat prototype for [ekt.kz](https://ekt.kz): catalog Q&A from a downloaded snapshot, then a virtual cart after an explicit confirmation.

## Architecture

```
browser  →  FastAPI  →  gpt-5.6-luna (Responses API, tools)
                │
                ├─ search_products / get_product  →  NumPy index (OpenAI embeddings)
                └─ propose_add_to_cart / add_to_cart  →  in-memory session cart
```

- Catalog files: `data/ekt/products.csv` (semicolon, from the partner dump), plus optional `data/ekt/pages/*.json` and `data/ekt/details/{id}.json`.
- Index: `data/index/products.json` + `data/index/embeddings.npy` (`text-embedding-3-small`, cosine via a normalized dot product).
- Price and stock come from the snapshot. If a product has no `quantity`, the assistant reports unknown availability and will not add it.
- Cart is virtual. `/cart` shows the session cart. Nothing is written to the live ekt.kz basket.
- Photos go to Luna as images. PDF / Word / Excel / CSV go as file inputs. Files are used for that turn only.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # put the OpenAI key in OPENAI_API_KEY (or API_KEY)
```

Build the index after the catalog dump is on disk:

```bash
python -m app.index_build
```

Run the app:

```bash
python main.py
```

Open `http://127.0.0.1:8000`. Cart: `http://127.0.0.1:8000/cart`.

## Checks

```bash
pytest
```

Manual: ask for an article that exists in the snapshot, attach a photo, confirm the packing slip, try a quantity above stock.
