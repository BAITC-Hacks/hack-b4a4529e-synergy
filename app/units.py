"""Explicit unit equivalence only; no package or length conversion guesses."""


def unit_key(value):
    value = str(value or "").strip().casefold().rstrip(".")
    return {"метр": "м", "метры": "м", "метров": "м", "m": "м",
            "штука": "шт", "штук": "шт", "штуки": "шт", "pcs": "шт",
            "kg": "кг", "килограмм": "кг", "упаковка": "уп", "упак": "уп"}.get(value, value)
