import asyncio

import download_ekt


def test_large_page_checkpoint_ignores_legacy_entries(tmp_path, monkeypatch):
    path = tmp_path / "list_checkpoint.txt"
    path.write_text("1\t1,2\n500:1\t1,2,3\n500:2\t4,5\n")
    monkeypatch.setattr(download_ekt, "LIST_PATH", path)

    assert download_ekt.load_list_pages() == {1: [1, 2, 3], 2: [4, 5]}


def test_collect_pages_uses_500_item_requests_and_small_detail_batches(tmp_path, monkeypatch):
    urls = []
    first_ids = list(range(1, 501))

    async def fake_get_json(_session, url, _sem, _timeout):
        urls.append(url)
        page = int(url.split("page=", 1)[1].split("&", 1)[0])
        ids = [501, 502, 503] if page == 2 else first_ids
        return {"per_page": 500, "items": [{"id": item_id} for item_id in ids]}

    monkeypatch.setattr(download_ekt, "get_json", fake_get_json)
    monkeypatch.setattr(download_ekt, "LIST_CONCURRENCY", 2)
    queue = asyncio.Queue()
    pages = {}
    scheduled = set()
    path = tmp_path / "list_checkpoint.txt"
    with path.open("a") as handle:
        asyncio.run(
            download_ekt.collect_pages(
                None, asyncio.Semaphore(2), pages, handle, queue, scheduled, {1, 2}
            )
        )

    assert pages == {1: first_ids, 2: [501, 502, 503]}
    assert queue.qsize() == 26
    assert all("per_page=500" in url for url in urls)
    assert path.read_text().startswith("500:1\t")
