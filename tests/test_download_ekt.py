import asyncio
import json
from urllib.parse import parse_qs, urlparse

import pytest

import download_ekt as download
from app.catalog import load_products, normalize_product
from app.catalog_archive import archived_records, checksum, detail_url


def product(pid=1, **extra):
    return {"id": pid, "name": f"Товар {pid}", "article": f"sku-{pid}", "price": 10, "quantity": 0,
            "stores": [{"id": 13, "name": "Алматы", "quantity": 0, "future": {"a": None}}],
            "offers": [{"id": 900 + pid, "unknown": [None, False, 0, ""]}],
            "properties": {"CML2_TRAITS": ["a | b", "x=y", None, 0], "DLINA_SHNURA": "3 метра"},
            "description": "Строка 1\r\nСтрока 2", "unknown": {"nested": [None, {}, []]}, **extra}


class API:
    def __init__(self, products=None, pages=None):
        self.products = products or {1: product()}
        self.pages = pages or {1: [{"id": pid, "url_api_detail": f"https://ekt.kz/api/products/detail?id={pid}"}
                                  for pid in self.products]}
        self.calls = []
        self.fail = set()
        self.bodies = {}

    async def fetch(self, _session, url, params, _timeout):
        if url.endswith('/detail') or '/detail?' in url:
            pid = int(parse_qs(urlparse(url).query)["id"][0])
            key = f"detail-{pid}"
            data = self.products[pid]
        else:
            page = params["page"]
            key = f"page-{page}"
            items = self.pages[page]
            data = {"page": page, "per_page": 500, "count": len(items), "items": items,
                    "future_pagination": {"value": None}}
        self.calls.append(key)
        if key in self.fail:
            raise ValueError("simulated API failure")
        body = json.dumps(data, ensure_ascii=False, indent=3).encode() + b"\n"
        self.bodies[key] = body
        return body, 200


def run(tmp_path, monkeypatch, api, **kwargs):
    monkeypatch.setattr(download, "fetch_response", api.fetch)
    return asyncio.run(download.download_catalog(None, tmp_path, concurrency=2, **kwargs))


def test_full_responses_preserved_even_when_csv_already_has_product(tmp_path, monkeypatch):
    (tmp_path / "products.csv").write_text("id;name;price;quantity\n1;stale;99;999\n")
    api = API()
    folder = run(tmp_path, monkeypatch, api)
    assert api.calls == ["page-1", "detail-1"]
    assert (folder / "pages/00001.json").read_bytes() == api.bodies["page-1"]
    assert (folder / "details/1.json").read_bytes() == api.bodies["detail-1"]
    manifest = json.loads((folder / "manifest.json").read_text())
    entry = manifest["details"]["1"]
    assert entry["sha256"] == checksum(api.bodies["detail-1"])
    assert entry["fetched_at"] and entry["http_status"] == 200
    assert "authorization" not in json.dumps(manifest).lower()
    raw, source = list(archived_records(tmp_path))[0]
    assert raw == api.products[1] and source["detail_path"].endswith("details/1.json")
    result = load_products(tmp_path)[0]
    assert result["quantity"] == 0 and result["price"] == 10
    assert result["source_fields"] == raw
    assert result["properties"] == raw["properties"]
    assert result["stores"] == raw["stores"] and result["offers"] == raw["offers"]
    assert result["lengths"][0]["metres"] == 3
    report = json.loads((folder / "report.json").read_text())
    assert report["valid_details"] == report["discovered_products"] == 1
    assert report["missing_details"] == [] and report["raw_checksums_verified"]
    assert report["ingested_source_fields_verified"] and report["pipeline_lost_fields"] == 0
    assert report["raw_top_level_field_counts"]["unknown"] == 1
    assert report["source_field_presence"]["quantity"] == {"present": 1}
    assert report["source_field_presence"]["unit"] == {"absent": 1}
    assert json.loads((folder / "products.json").read_text())[0]["source_fields"] == raw


def test_failed_download_preserves_active_and_resumes_only_missing_detail(tmp_path, monkeypatch):
    original = run(tmp_path, monkeypatch, API())
    active = (tmp_path / "current.json").read_bytes()
    api = API({1: product(1), 2: product(2)})
    api.fail.add("detail-2")
    with pytest.raises(ValueError, match="details failed"):
        run(tmp_path, monkeypatch, api, new=True)
    assert (tmp_path / "current.json").read_bytes() == active
    assert len(load_products(tmp_path)) == 1
    api.calls.clear(); api.fail.clear()
    repaired = run(tmp_path, monkeypatch, api)
    assert api.calls == ["detail-2"]
    assert repaired != original and len(load_products(tmp_path)) == 2
    assert json.loads((original / "manifest.json").read_text())["status"] == "complete"


def test_resume_refetches_corrupt_raw_and_ignores_orphan_file(tmp_path, monkeypatch):
    api = API({1: product(1), 2: product(2)})
    api.fail.add("detail-2")
    with pytest.raises(ValueError): run(tmp_path, monkeypatch, api)
    run_id = json.loads((tmp_path / "download.json").read_text())["run_id"]
    folder = tmp_path / "raw" / run_id
    (folder / "details/1.json").write_bytes(b"corrupt")
    (folder / "details/2.json").write_text(json.dumps(product(2)))
    api.fail.clear(); api.calls.clear()
    run(tmp_path, monkeypatch, api)
    assert set(api.calls) == {"detail-1", "detail-2"}


def test_completed_archive_corruption_fails_closed(tmp_path, monkeypatch):
    api = API(); folder = run(tmp_path, monkeypatch, api)
    (folder / "details/1.json").write_text('{}')
    with pytest.raises(ValueError, match="checksum"):
        load_products(tmp_path)
    api.calls.clear()
    with pytest.raises(ValueError, match="checksum"):
        run(tmp_path, monkeypatch, api)
    assert not api.calls


def test_short_final_page_stops_before_wrapping_page(tmp_path, monkeypatch):
    products = {i: product(i) for i in range(1, 502)}
    pages = {1: [{"id": i} for i in range(1, 501)], 2: [{"id": 501}]}
    api = API(products, pages)
    folder = run(tmp_path, monkeypatch, api)
    assert json.loads((folder / "manifest.json").read_text())["last_page"] == 2
    assert "page-3" not in api.calls


def test_repeated_full_page_cannot_publish(tmp_path, monkeypatch):
    items = [{"id": i} for i in range(1, 501)]
    api = API(pages={1: items, 2: items})
    with pytest.raises(ValueError, match="Repeated listing"):
        run(tmp_path, monkeypatch, api)
    assert not (tmp_path / "current.json").exists()
    assert api.calls == ["page-1", "page-2"]


def test_empty_terminal_page_after_full_page(tmp_path, monkeypatch):
    products = {i: product(i) for i in range(1, 501)}
    api = API(products, {1: [{"id": i} for i in products], 2: []})
    folder = run(tmp_path, monkeypatch, api)
    assert json.loads((folder / "report.json").read_text())["discovered_products"] == 500


@pytest.mark.parametrize("url", ["https://evil.example/api/products/detail?id=1", "http://ekt.kz/api/products/detail?id=1",
    "https://user:pass@ekt.kz/api/products/detail?id=1", "https://ekt.kz/api/products/detail?id=2",
    "https://ekt.kz/api/products/detail?id=1&extra=2"])
def test_untrusted_detail_urls_rejected(url):
    with pytest.raises(ValueError): detail_url({"id": 1, "url_api_detail": url})


def test_invalid_detail_body_is_saved_but_not_published(tmp_path, monkeypatch):
    api = API({1: product(2)})
    with pytest.raises(ValueError, match="details failed"):
        run(tmp_path, monkeypatch, api)
    run_id = json.loads((tmp_path / "download.json").read_text())["run_id"]
    folder = tmp_path / "raw" / run_id
    assert (folder / "details/1.json").read_bytes() == api.bodies["detail-1"]
    assert json.loads((folder / "manifest.json").read_text())["details"]["1"]["status"] == "failed"
    assert not (tmp_path / "current.json").exists()


def test_lengths_embedded_specs_and_units_do_not_guess_packaging():
    original = '{&quot;addin&quot;:{&quot;Комплектация&quot;:[{&quot;value&quot;:&quot;Кабель 1 метр&quot;}],&quot;Длина кабеля&quot;:[]}}'
    raw = product(properties={"WB_SPECIFICATIONS": original, "DLINA_RULONA": "20 метров", "DLINA_SHNURA": "250 см",
                              "DLINA": "unknown", "METRAZHNYY_TOVAR": "Да", "KRATNOST_MAKS": "300"})
    result = normalize_product(raw)
    assert result["unit"] == "м" and result["unit_source"] == "properties.METRAZHNYY_TOVAR"
    assert {x['kind']: x['metres'] for x in result['lengths']} == {'roll': 20, 'cord': 2.5, 'length': None}
    assert result['properties']['WB_SPECIFICATIONS'] == original
    assert result['embedded_specifications']['WB_SPECIFICATIONS']['value']['addin']['Длина кабеля'] == []
    assert result['min_quantity'] is None and result['quantity_step'] is None
    without = normalize_product(product(name="Кабель (300)", properties={"KRATNOST_MAKS": "300"}))
    assert without['lengths'] == [] and without['unit_known'] is False


def test_complete_resume_does_not_request_network(tmp_path, monkeypatch):
    api = API(); folder = run(tmp_path, monkeypatch, api)
    api.calls.clear()
    assert run(tmp_path, monkeypatch, api) == folder and api.calls == []


def test_fetch_retries_and_disables_redirects(monkeypatch):
    calls = []
    class Response:
        def __init__(self, status): self.status = status
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def read(self): return b'{ "id": 1 }\n'
    class Session:
        def get(self, url, **kwargs):
            calls.append(kwargs)
            return Response(503 if len(calls) == 1 else 200)
    async def no_sleep(*args): pass
    monkeypatch.setattr(download.asyncio, "sleep", no_sleep)
    body, status = asyncio.run(download.fetch_response(Session(), "https://ekt.kz/api/products", {}))
    assert body == b'{ "id": 1 }\n' and status == 200 and len(calls) == 2
    assert all(c['allow_redirects'] is False for c in calls)


def test_interruption_after_validation_before_publication_recovers(tmp_path, monkeypatch):
    api = API(); folder = run(tmp_path, monkeypatch, api)
    (tmp_path / "current.json").unlink()
    (folder / "report.json").unlink()
    api.calls.clear()
    run(tmp_path, monkeypatch, api)
    assert not api.calls
    assert (tmp_path / "current.json").exists() and (folder / "report.json").exists()


def test_archive_rebuild_keeps_source_and_offer_data(tmp_path, monkeypatch):
    from types import SimpleNamespace as Obj
    from app.index_build import build_index
    from app.search import CatalogIndex, product_detail
    from tests.test_index import Embeddings
    data = tmp_path / "catalog"; data.mkdir()
    api = API(); run(data, monkeypatch, api)
    index_root = tmp_path / "index"
    build_index(data, index_root, client=Obj(embeddings=Embeddings()))
    index = CatalogIndex.load(index_root)
    p = index.get(1)
    assert p["source_fields"] == api.products[1]
    assert p["offers"] == api.products[1]["offers"]
    assert p["stores"][0]["id"] == 13 and p["properties"]["CML2_TRAITS"] == ["a | b", "x=y", None, 0]
    assert product_detail(p)["lengths"][0]["metres"] == 3
    assert index.metadata["source_run_id"] == p["source_response"]["run_id"]
    pointer = (index_root / "current.json").read_bytes()
    # A bad archive must fail before embedding or replacing the working index.
    path = data / p["source_response"]["detail_path"]
    path.write_bytes(b"{}")
    with pytest.raises(ValueError, match="checksum"):
        build_index(data, index_root, client=Obj(embeddings=Embeddings(fail=True)))
    assert (index_root / "current.json").read_bytes() == pointer


def test_failed_listing_body_is_archived(tmp_path, monkeypatch):
    async def invalid(*args): return b'{ broken JSON', 200
    monkeypatch.setattr(download, "fetch_response", invalid)
    with pytest.raises(ValueError):
        asyncio.run(download.download_catalog(None, tmp_path))
    rid = json.loads((tmp_path / "download.json").read_text())["run_id"]
    folder = tmp_path / "raw" / rid
    assert (folder / "pages/00001.json").read_bytes() == b'{ broken JSON'
    assert json.loads((folder / 'manifest.json').read_text())["pages"]["1"]["status"] == "failed"


def test_deleted_product_is_reported_and_stale_nulls_not_filled(tmp_path, monkeypatch):
    run(tmp_path, monkeypatch, API({1: product(1), 2: product(2)}))
    raw = product(1, price=None, quantity=None, stores=[], offers=[], properties={})
    folder = run(tmp_path, monkeypatch, API({1: raw}), new=True)
    report = json.loads((folder / "report.json").read_text())
    assert report["previous_ids_not_listed"] == [2]
    assert report["source_field_presence"]["price"] == {"null": 1}
    p = load_products(tmp_path)[0]
    assert p["price"] is None and p["quantity"] is None and p["stores"] == [] and p["properties"] == {}


def test_supplier_quantity_is_not_warehouse_stock_and_description_is_complete():
    from app.search import product_hit
    from app.cart import Session, purchase_options
    description = "Полное описание характеристик. " * 30
    raw = product(520709, article="410993_", description=description, quantity=0,
                  properties={"KOL_VO_U_POSTAVSHCHIKA": "6", "KOL_VO_CHASOV_DOSTAVKI_OT_POSTAVSHCHIKA": "72 часа",
                              "KOLICHESTVO_POLYUSOV": "1", "NOMINALNYY_TOK": "16 А"})
    p = normalize_product(raw); hit = product_hit(p)
    assert hit["description"] == description.strip() and len(hit["spec_snippet"]) == 280
    assert p["source_fields"]["description"] == description
    assert hit["supplier_availability"]["quantity"] == 6
    assert hit["supplier_availability"]["lead_time_hours"] == 72
    assert hit["properties"]["Количество у поставщика"] == "6"
    assert hit["properties"]["Срок поступления от поставщика"] == "72 часа"
    assert hit["quantity"] == 0 and hit["availability"] == "out_of_stock"
    assert not purchase_options(Session("supplier"), p)["can_add"]
    from app.answers import catalog_answer
    answer = catalog_answer("Сколько у поставщика и когда поступление?", [hit])
    assert "6" in answer and "72 часа" in answer and "не является сроком доставки" in answer


def test_http_failure_body_and_status_are_preserved(tmp_path, monkeypatch):
    async def failed(*args): return b'{"error":"temporarily unavailable"}', 503
    monkeypatch.setattr(download, "fetch_response", failed)
    with pytest.raises(ValueError, match="HTTP 503"):
        asyncio.run(download.download_catalog(None, tmp_path))
    rid = json.loads((tmp_path / "download.json").read_text())["run_id"]
    folder = tmp_path / "raw" / rid
    manifest = json.loads((folder / "manifest.json").read_text())
    assert manifest["pages"]["1"]["http_status"] == 503
    assert (folder / "pages/00001.json").read_bytes() == b'{"error":"temporarily unavailable"}'
