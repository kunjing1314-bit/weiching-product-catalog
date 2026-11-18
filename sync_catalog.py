#!/usr/bin/env python3
"""Rebuild WEWELDER's public catalog without any CRM credential or write API."""
from __future__ import annotations
import argparse
import concurrent.futures
import hashlib
import html
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ORIGIN = "https://www.weiching-tech.com"
SEARCH = "https://waimao.office.163.com/site/api/pub/product/search"
TOP_CATEGORIES = {
    "MIG Torch": "/category/MIG-Torch.html",
    "MIG Wear Parts": "/category/MIG-Wear-Parts.html",
    "TIG Torch": "/category/TIG-Torch.html",
    "TIG accessories": "/category/TIG-accessories.html",
    "Plasma": "/category/Plasma.html",
    "Aut&Robotic": "/category/AutRobotic.html",
    "WELDER": "/category/WELDER.html",
    "Other Accessories": "/category/Accessories-1.html",
}
PROC = {
    "MIG Torch": "MIG", "MIG Wear Parts": "MIG",
    "TIG Torch": "TIG", "TIG accessories": "TIG",
    "Plasma": "Plasma", "Aut&Robotic": "Auto & Robotic",
    "WELDER": "WELDER", "Other Accessories": "Other",
}
CONSUMABLE_PARTS = {"Contact Tip", "Nozzle", "Liner", "Tip Holder", "Gas diffuser&Insulator"}
BRAND = {
    "ATTC Style": "ATTC", "BND Style": "Bernard", "Binzel Style": "Binzel",
    "ESAB PSF Style": "ESAB PSF", "ESAB Style": "ESAB", "EURO Style": "Euro",
    "Euro Style": "Euro", "Fronius Style": "Fronius", "Kemppi Style": "Kemppi",
    "Lincoln Style": "Lincoln", "Mechafin Style": "Mechafin", "Miller Style": "Miller",
    "OTC Style": "OTC", "Oximig Style": "Oximig", "PANA Style": "Panasonic",
    "Pana Style": "Panasonic", "Profax Style": "Profax", "Sumig Style": "Sumig",
    "TBI Style": "TBi", "Trafimet Style": "Trafimet", "Tregaskiss Style": "Tregaskiss",
    "Tweco Style": "Tweco",
}
PLASMA_AMP = {
    "A81": 80, "A101": 100, "A141": 140, "A151": 150, "AG60": 60,
    "SG51": 60, "SP60": 60, "P80": 80, "PT25": 25, "PT31": 30,
    "PT40": 40, "PT60": 60, "PT80": 80, "PT100": 100, "CB50": 50,
    "CB70": 70, "CB100": 100, "CB150": 150, "S45": 45, "S75": 75,
    "S105": 105,
}
WEAR = re.compile(r"contact tip|nozzle|liner|diffuser|insulator|swan neck|neck\b|collet|cap\b|shield|electrode|\btip\b|holder|adapter|spring|o-ring|consumable", re.I)


def load_json(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def as_json(value, default):
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return default


def request(url: str, attempts: int = 4) -> bytes:
    last = None
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "WEWELDER-Public-Catalog-Sync/1.0",
                "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
                "Accept-Encoding": "identity",
            })
            with urllib.request.urlopen(req, timeout=90) as response:
                return response.read()
        except Exception as exc:
            last = exc
            if attempt + 1 < attempts:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"Request failed after {attempts} attempts: {url}: {last}")


def public_category(label: str, path: str) -> list[dict]:
    rows, page, expected = [], 1, None
    while True:
        query = urllib.parse.urlencode({"url": ORIGIN + path, "pageSize": 1000, "page": page, "languageCode": "en", "searchType": 1})
        payload = json.loads(request(SEARCH + "?" + query).decode("utf-8"))
        data = payload.get("data") or {}
        batch = data.get("productDetailList") or data.get("list") or []
        if not isinstance(batch, list):
            raise RuntimeError(f"Unexpected public product response for {label}")
        rows.extend(batch)
        expected = int(data.get("totalCount") or len(rows))
        if not batch or len(rows) >= expected:
            break
        page += 1
        if page > 20:
            raise RuntimeError(f"Pagination guard tripped for {label}")
    if len(rows) != expected:
        raise RuntimeError(f"Incomplete {label}: got {len(rows)}, expected {expected}")
    print(f"public {label}: {len(rows)}", flush=True)
    return rows


def native_detail(product_id: str) -> dict | None:
    url = f"{ORIGIN}/products/{urllib.parse.quote(product_id)}.html"
    try:
        text = request(url).decode("utf-8", "replace")
    except RuntimeError as exc:
        print(f"supplement {product_id}: unavailable ({exc})", flush=True)
        return None
    pos = text.find("window.g_initialProps")
    if pos < 0:
        return None
    pos = text.find("=", pos) + 1
    source = text[pos:].lstrip().replace(":undefined", ":null")
    try:
        props, _ = json.JSONDecoder().raw_decode(source)
        detail = props["data"]["data"]["productDetail"]
        if str(detail.get("id")) != str(product_id):
            raise ValueError("product ID mismatch")
        return detail
    except (ValueError, KeyError, TypeError) as exc:
        print(f"supplement {product_id}: no active native detail ({exc})", flush=True)
        return None


def display_name(value: str) -> str:
    name = html.unescape(value or "").strip()
    name = re.sub(r"\s*/\s*MAG\b", "", name, flags=re.I)
    name = re.sub(r"\(\s*([^()]*?)\s+Compatible\s*\)", r"(\1 Style)", name, flags=re.I)
    return re.sub(r"\s+", " ", name).strip()


def dimensions(product: dict, old: dict) -> tuple[str, str, str, str, list[str]]:
    path = [str(x.get("label") or "").strip() for x in as_json(product.get("category"), [])]
    if not path:
        return old.get("proc") or "Other", old.get("kind") or "Accessories & Spares", old.get("brand") or "", old.get("part") or "", path
    top = path[0]
    second = path[1] if len(path) > 1 else ""
    third = path[2].strip() if len(path) > 2 else ""
    proc, brand = PROC.get(top, old.get("proc") or "Other"), ""
    if top in ("MIG Torch", "TIG Torch"):
        kind, part = "Torches", "Torches"
        brand = BRAND.get(second, BRAND.get(second.title(), ""))
    elif top == "MIG Wear Parts":
        part = second
        kind = "Consumables & Wear Parts" if second in CONSUMABLE_PARTS else "Accessories & Spares"
        brand = BRAND.get(third, "")
    elif top == "Plasma":
        kind, part = ("Torches" if second == "Plasma Torch" else "Accessories & Spares"), second
    elif top == "Aut&Robotic":
        kind, part = "Torches", second
    else:
        kind, part = "Accessories & Spares", second
    if not second and old:
        kind, part, brand = old.get("kind") or kind, old.get("part") or "", old.get("brand") or ""
    return proc, kind, brand, part, path


def first_image(product: dict) -> str:
    for picture in as_json(product.get("pictures"), []):
        if isinstance(picture, dict) and picture.get("url"):
            return str(picture["url"])
    images = product.get("images") or []
    if isinstance(images, list):
        for image in images:
            if isinstance(image, str) and image:
                return image
            if isinstance(image, dict) and image.get("url"):
                return str(image["url"])
    return ""


def plasma_specs(name: str) -> list[str]:
    if not re.search(r"torch|gun\b", name or "", re.I) or WEAR.search(name or ""):
        return []
    match = re.search(r"\b(A81|A101|A141|A151|AG60|SG51|SP60|P80|PT25|PT31|PT40|PT60|PT80|PT100|CB50|CB70|CB100|CB150|S45|S75|S105)\b", name or "", re.I)
    if not match:
        return []
    return [f"{PLASMA_AMP[match.group(1).upper()]}A Class", "60% Duty Cycle · Air Cooled"]


def build(products: dict[str, dict], verified_specs: dict, old_index: list) -> tuple[list, dict]:
    old_by_id = {str(row["id"]): row for row in old_index}
    output, skipped, category_paths = [], Counter(), Counter()
    for product_id in sorted(products, key=int):
        product = products[product_id]
        status = product.get("status")
        if status != 1:
            skipped[f"status_{status}"] += 1
            continue
        old = old_by_id.get(product_id, {})
        name = display_name(product.get("product_name_en") or product.get("product_name_cn") or "")
        path = product.get("pagePath") or old.get("u") or ""
        if not name or not path:
            skipped["missing_name_or_path"] += 1
            continue
        proc, kind, brand, part, category_path = dimensions(product, old)
        specs = (verified_specs.get(product_id) or {}).get("specs") or [] or plasma_specs(name)
        row = {
            "id": product_id, "n": name, "u": path, "img": first_image(product),
            "proc": proc, "kind": kind, "brand": brand, "part": part,
            "num": product.get("product_number") or old.get("num") or "",
            "ct": product.get("create_time") or old.get("ct") or "", "specs": specs,
        }
        output.append(row)
        category_paths[" > ".join(category_path) or "(uncategorized)"] += 1
    if len(output) < 3000:
        raise RuntimeError(f"Safety guard: only {len(output)} active products; refusing to replace index")
    ids = [row["id"] for row in output]
    if len(ids) != len(set(ids)):
        raise RuntimeError("Safety guard: duplicate product IDs")
    protected = next((row for row in output if row["id"] == "2577985"), None)
    if protected and protected["specs"] != ["305A CO₂ / 270A Mixed Gas", "60% Duty Cycle · Air Cooled", "0.8–1.2mm Wire"]:
        raise RuntimeError("Safety guard: protected PSF 305 display specs changed")
    report = {
        "schema": 1, "active_products": len(output), "source_unique_products": len(products),
        "skipped": dict(sorted(skipped.items())),
        "by_proc": dict(sorted(Counter(row["proc"] for row in output).items())),
        "with_specs": sum(bool(row["specs"]) for row in output),
        "with_image": sum(bool(row["img"]) for row in output),
        "uncategorized_active": category_paths.get("(uncategorized)", 0),
        "category_paths": dict(sorted(category_paths.items())),
        "index_sha256": hashlib.sha256(json.dumps(output, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest(),
    }
    return output, report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline-public")
    parser.add_argument("--offline-details")
    args = parser.parse_args()
    verified_specs = load_json(ROOT / "verified_specs.json", {})
    verified_specs.update(load_json(ROOT / "verified_spec_overrides.json", {}))
    old_index = load_json(ROOT / "legacy_dimensions.json", [])
    supplemental_ids = [str(x) for x in load_json(ROOT / "supplemental_products.json", [])]
    if args.offline_public:
        public_rows = load_json(Path(args.offline_public), [])
    else:
        public_rows = []
        for label, path in TOP_CATEGORIES.items():
            public_rows.extend(public_category(label, path))
    products = {str(row["id"]): row for row in public_rows if row.get("id") is not None}
    public_ids = set(products)
    needed = [product_id for product_id in supplemental_ids if product_id not in public_ids]
    details = {}
    if args.offline_details:
        for product_id in needed:
            detail_path = Path(args.offline_details) / f"{product_id}.json"
            if detail_path.exists():
                props = load_json(detail_path, {})
                details[product_id] = props.get("data", {}).get("data", {}).get("productDetail")
    elif needed:
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            details = dict(zip(needed, pool.map(native_detail, needed)))
    for product_id in needed:
        detail = details.get(product_id)
        if detail and detail.get("status") == 1:
            products[product_id] = detail
            print(f"supplement active: {product_id}", flush=True)
        else:
            print(f"supplement removed/inactive: {product_id}", flush=True)
    index, report = build(products, verified_specs, old_index)
    report["supplemental_seed_count"] = len(supplemental_ids)
    report["supplemental_active_not_in_public"] = len(set(products) - public_ids)
    report["source"] = "NetEase Waimao public category service + native product details"
    (ROOT / "index.json").write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    (ROOT / "catalog-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "category_paths"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
