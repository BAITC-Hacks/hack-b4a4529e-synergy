"""Targeted, lossless metadata refresh. Does not touch an active CSV download."""
import argparse
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
import os
from urllib.parse import urljoin, urlparse

import httpx

from .catalog import load_products, safe_url, certificate_links
from .config import DATA_DIR


class CertificateAnchors(HTMLParser):
    def __init__(self, base):
        super().__init__()
        self.base, self.links, self.current = base, [], None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "a":
            self.current = [attributes.get("href", ""), " ".join(attributes.get(k, "") for k in ("title", "class", "download"))]

    def handle_data(self, data):
        if self.current:
            self.current[1] += data

    def handle_endtag(self, tag):
        if tag == "a" and self.current:
            href, label = self.current
            parsed = urlparse(urljoin(self.base, href))
            if parsed.path.startswith("/upload/") and any(s in label.lower() for s in ("сертифик", "certificate", "декларац")):
                if link := safe_url(parsed.geturl()):
                    self.links.append(link)
            self.current = None


def refresh(ids=None, limit=40):
    from download_ekt import save_detail
    products = load_products()
    if ids:
        selected = [p for p in products if p["id"] in ids]
    else:
        selected = [p for p in products if p.get("certificate_references") or
                    "\ufffd" in str((p.get("properties") or {}).get("CML2_BASE_UNIT", ""))][:limit]
    user, password = os.getenv("EKT_API_USER"), os.getenv("EKT_API_PASSWORD")
    if not user or not password:
        raise SystemExit("Set EKT_API_USER and EKT_API_PASSWORD in .env")
    outcomes = []
    with httpx.Client(timeout=25) as client:
        for product in selected:
            result = {"id": product["id"]}
            try:
                response = client.get("https://ekt.kz/api/products/detail", params={"id": product["id"]}, auth=(user, password))
                response.raise_for_status()
                detail = response.json()
                detail["_source_refreshed_at"] = datetime.now(timezone.utc).isoformat()
                links = certificate_links(detail)
                url = safe_url(detail.get("url"))
                if product.get("certificate_references") and not links and url and urlparse(url).hostname == "ekt.kz":
                    try:
                        page = client.get(url)
                        page.raise_for_status()
                        parser = CertificateAnchors(url)
                        parser.feed(page.text)
                        links = list(dict.fromkeys(parser.links))
                    except httpx.HTTPError:
                        result["certificate_page"] = "unavailable"
                if links:
                    detail["certificates"] = links
                save_detail(detail, DATA_DIR)
                result.update(status="refreshed", certificates=len(links),
                              unit_damaged="\ufffd" in str(detail.get("properties", {}).get("CML2_BASE_UNIT", "")))
            except (httpx.HTTPError, ValueError, TypeError):
                result["status"] = "source_unavailable"
            outcomes.append(result)
            print(json.dumps(result), flush=True)
    return outcomes


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ids", type=int, nargs="+")
    parser.add_argument("--limit", type=int, default=40)
    arguments = parser.parse_args()
    refresh(arguments.ids, arguments.limit)
