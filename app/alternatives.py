"""Conservative comparisons. Catalog similarities are not engineering approval."""
import re

ALIASES = {
    "ток": ("NOMINALNYY_TOK", "NOMINALNY_TOK", "NOMINALNYY_TOK_A_1", "NOMIN_TOK_A"),
    "напряжение": ("NOMINALNOE_NAPRYAZHENIE", "NAPRYAZHENIE", "NOMIN_RABOCHEE_NAPRYAZHENIE_V", "NOMINALNOE_NAPRYAZHENIE_PITANIYA_KATUSHKI"),
    "полюса": ("KOLICHESTVO_POLYUSOV", "KOLICHESTVO_POLYUSOV_NOMINALNOGO_TOKA"),
    "отключающая способность": ("NOMINALNAYA_OTKLYUCHAYUSHCHAYA_SPOSOBNOST",),
    "характеристика": ("KHARAKTERISTIKA_SRABATYVANIYA",),
    "монтаж": ("TIP_USTANOVKI", "SPOSOB_MONTAZHA"),
    "сечение": ("SECHENIE", "SECHENIE_ZHILY"),
    "число жил": ("KOLICHESTVO_ZHIL",),
    "материал жилы": ("MATERIAL_ZHILY",),
    "изоляция": ("MATERIAL_IZOLYATSII", "TIP_IZOLYATSII"),
    "цоколь": ("TIP_TSOKOLYA", "TSOKOL"),
    "мощность": ("MOSHCHNOST_W", "MOSHCHNOST"),
    "температура света": ("TSVETOVAYA_TEMPERATURA",),
    "защита": ("STEPEN_ZASHCHITY_IP", "STEPEN_ZASHCHITY"),
    "тип лампы": ("TIP_LAMPY",),
}
ESSENTIAL = {
    "breaker": {"ток", "полюса", "напряжение", "характеристика", "отключающая способность", "монтаж"},
    "cable": {"число жил", "сечение", "материал жилы", "напряжение", "изоляция"},
    "lamp": {"цоколь", "напряжение", "мощность", "тип лампы"},
    "luminaire": {"напряжение", "мощность", "защита", "монтаж"},
}


def family(product):
    name = product.get("name", "").casefold()
    description = (product.get("description") or "").casefold()
    category = (product.get("category") or "").casefold()
    if re.search(r"автомат(?:ический)?\b|выключатель.*(?:drx|dx3)|\bmcb\b", name + " " + description) or "avtomaticheskie_vyklyuchateli" in category:
        return "breaker"
    if re.search(r"(?:кабель|провод)\b", name) and not re.search(r"канал|держатель|наконечник|ввод", name):
        return "cable"
    if "светильник" in name:
        return "luminaire"
    if "лампа" in name:
        return "lamp"
    if category and not any(part in category for part in ("spets_predlozhenie", "novinki", "aktsiya")):
        return "category:" + category
    return None


def normalized(label, value):
    value = re.sub(r"\s+", "", str(value).casefold().replace(",", ".")).replace("²", "2")
    if label in {"ток", "отключающая способность", "напряжение", "мощность", "сечение", "температура света", "полюса", "число жил"}:
        m = re.fullmatch(r"(\d+(?:\.\d+)?)([a-zа-яё.0-9]*)", value)
        if m:
            number, suffix = float(m[1]), m[2]
            if suffix in {"ka", "ка", "kv", "кв", "kw", "квт"}:
                number *= 1000
            return f"{number:g}"
    return value.translate(str.maketrans("авкхс", "abkxc"))


def specs(product):
    props = product.get("properties") or {}
    values = {}
    for label, keys in ALIASES.items():
        for key in keys:
            if props.get(key) not in (None, ""):
                values[label] = str(props[key])
                break
    name = product.get("name", "")
    patterns = {
        "ток": r"(?<![\w.])(\d+(?:[.,]\d+)?)\s*[аa](?![a-zа-я])",
        "полюса": r"(?<!\w)([1-4])\s*[pр](?![a-zа-я])",
        "отключающая способность": r"(\d+(?:[.,]\d+)?)\s*[кk][аa]",
        "напряжение": r"(?<![\w.])(\d+(?:[.,]\d+)?)\s*[вv](?![a-zа-я])",
        "защита": r"\bIP\s*(\d{2})",
        "температура света": r"(\d{4})\s*[kк]\b",
        "цоколь": r"\b(E\d{2}|GU\d+(?:\.\d+)?|G\d+(?:\.\d+)?)\b",
    }
    for label, pattern in patterns.items():
        if label not in values and (match := re.search(pattern, name, re.I)):
            values[label] = match.group(1) + (" kA" if label == "отключающая способность" else "")
    if family(product) == "cable":
        if match := re.search(r"\b(\d+)\s*[xх×]\s*(\d+(?:[.,]\d+)?)", name, re.I):
            values.setdefault("число жил", match.group(1))
            values.setdefault("сечение", match.group(2))
    return values


def compare(source, candidate):
    kind = family(source)
    if not kind or kind != family(candidate):
        return None
    original, other = specs(source), specs(candidate)
    common = original.keys() & other.keys()
    if not common or original.keys() - other.keys():
        return None
    matches, differences = [], []
    for key in sorted(common):
        field = {"attribute": key, "source": original[key], "candidate": other[key]}
        (matches if normalized(key, original[key]) == normalized(key, other[key]) else differences).append(field)
    # All represented electrical/mechanical differences require an explicit new specification.
    if differences:
        return None
    unknowns = sorted((ESSENTIAL.get(kind, set()) | original.keys() | other.keys()) - common)
    brand_a = (source.get("properties") or {}).get("TORGOVAYA_MARKA")
    brand_b = (candidate.get("properties") or {}).get("TORGOVAYA_MARKA")
    if brand_a and brand_b and brand_a != brand_b:
        differences.append({"attribute": "марка", "source": brand_a, "candidate": brand_b})
    supported = kind in ESSENTIAL and not unknowns
    reason = "Та же техническая категория; совпадают " + ", ".join(f"{v['attribute']}: {v['source']}" for v in matches) + "."
    if differences:
        reason += " Отличается " + ", ".join(f"{v['attribute']}: {v['source']} → {v['candidate']}" for v in differences) + "."
    if unknowns:
        reason += " Не подтверждены: " + ", ".join(unknowns) + "."
    reason += " Перед заменой проверьте условия применения."
    return {"family": kind, "matches": matches, "differences": differences, "unknowns": unknowns,
            "compatibility": "supported_alternative" if supported else "candidate_requires_review", "reason": reason}
