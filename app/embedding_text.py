"""Stable catalog text and token budgets, independent of UI property labels."""
import html
import re
from functools import lru_cache

import tiktoken

# Bump when text selection, labels, ordering, cleanup, or truncation changes.
EMBEDDING_TEMPLATE_VERSION = 1
MAX_INPUT_TOKENS = 8192
MAX_BATCH_TOKENS = 300_000
MAX_BATCH_ITEMS = 64

# Deliberate allowlist: unknown, commercial, stock and media fields stay metadata.
PROPERTY_LABELS = {
    "TORGOVAYA_MARKA": "Бренд",
    "ARTIKULPOSTAVSHCHIKA": "Артикул поставщика",
    "KATEGORIYA": "Категория",
    "KATEGORIYA_SVETILNIKA": "Категория светильника",
    "KATEGORIYA_SHCHITOVOGO_OBORUDOVANIYA": "Категория щитового оборудования",
    "KATEGORIYA_KABELNOY_ARMATURY": "Категория кабельной арматуры",
    "SERIYA": "Серия",
    "MODEL_ILI_ISPOLNENIE": "Модель / исполнение",
    "NAZNACHENIE": "Назначение",
    "OBLAST_PRIMENENIYA": "Область применения",
    "TIP_USTROYSTVA": "Тип устройства",
    "TIP_PRODUKTA": "Тип продукта",
    "NOMINALNYY_TOK": "Номинальный ток",
    "NOMINALNY_TOK": "Номинальный ток",
    "NOMINALNYY_TOK_A": "Номинальный ток, А",
    "NOMIN_TOK_A": "Номинальный ток, А",
    "NOMINALNYY_TOK_PREDOKHRANITELYA": "Номинальный ток предохранителя",
    "NOMINALNOE_NAPRYAZHENIE": "Номинальное напряжение",
    "NAPRYAZHENIE": "Напряжение",
    "NAPRYAZHENIE_PITANIYA_V": "Напряжение питания, В",
    "VYKHODNOE_NAPRYAZHENIE": "Выходное напряжение",
    "NOMIN_RABOCHEE_NAPRYAZHENIE_V": "Рабочее напряжение, В",
    "NOMIN_RAB_NAPRYAZHENIE_V": "Рабочее напряжение, В",
    "NOMINALNOE_NAPRYAZHENIE_PITANIYA_KATUSHKI": "Напряжение питания катушки",
    "NOMINALNAYA_OTKLYUCHAYUSHCHAYA_SPOSOBNOST": "Отключающая способность",
    "NOMINALNYY_OTKLYUCHAYUSHCHIY_DIFFERENTSIALNYY_TOK": "Дифференциальный ток отключения",
    "KHARAKTERISTIKA_SRABATYVANIYA": "Характеристика срабатывания",
    "KOLICHESTVO_POLYUSOV": "Количество полюсов",
    "KOLICHESTVO_POLYUSOV_NOMINALNOGO_TOKA": "Количество полюсов",
    "KOLICHESTVO_FAZ": "Количество фаз",
    "TIP_TOKA": "Тип тока",
    "TIP_TOKA_UTECHKI": "Тип тока утечки",
    "REGULIRUEMYY_DIAPAZON_TOKA": "Регулируемый диапазон тока",
    "TIP_USTANOVKI": "Тип установки",
    "SPOSOB_USTANOVKI": "Способ установки",
    "SPOSOB_MONTAZHA": "Способ монтажа",
    "TIP_MONTAZHA": "Тип монтажа",
    "SPOSOB_PODKLYUCHENIYA": "Способ подключения",
    "TIP_KONTAKTOV": "Тип контактов",
    "KOLICHESTVO_NORMALNO_RAZOMKNUTYKH_KONTAKTOV": "Нормально разомкнутые контакты",
    "KOLICHESTVO_NORMALNO_ZAMKNUTYKH_KONTAKTOV": "Нормально замкнутые контакты",
    "KOLICHESTVO_MONTAZHNYKH_MODULEY": "Количество модулей",
    "KOLICHESTVO_RYADOV": "Количество рядов",
    "KOLICHESTVO_ROZETOCHNYKH_POSTOV": "Количество розеточных постов",
    "NALICHIE_ZAZEMLENIYA": "Заземление",
    "ZASHCHITNYE_SHTORKI": "Защитные шторки",
    "NALICHIE_ZASHCHITNOY_KRYSHKI": "Защитная крышка",
    "NALICHIE_PODSVETKI": "Подсветка",
    "STEPEN_ZASHCHITY_IP": "Степень защиты IP",
    "STEPEN_ZASHCHITY": "Степень защиты",
    "DOPOLNITELNAYA_ZASHCHITA": "Дополнительная защита",
    "MOSHCHNOST": "Мощность",
    "MOSHCHNOST_W": "Мощность, Вт",
    "MOSHCHNOST_VT": "Мощность, Вт",
    "NOMINALNAYA_MOSHCHNOST": "Номинальная мощность",
    "MOSHCHNOST_DVIGATELYA_KVT": "Мощность двигателя, кВт",
    "TIP_TSOKOLYA": "Тип цоколя",
    "TIP_LAMPY": "Тип лампы",
    "FORMA_LAMPY": "Форма лампы",
    "TIP_ISTOCHNIKA": "Тип источника света",
    "TIP_SVETILNIKA": "Тип светильника",
    "TIP_RASSEIVATELYA": "Тип рассеивателя",
    "TSVETOVAYA_TEMPERATURA": "Цветовая температура",
    "SVETOVOY_POTOK_LM": "Световой поток, лм",
    "KATEGORIYA_TSVETNOSTI_SVETA": "Цветность света",
    "KOLICHESTVO_ZHIL": "Количество жил",
    "SECHENIE_MM2": "Сечение, мм²",
    "SECHENIE_ZHILY_MM": "Сечение жилы, мм²",
    "PLOSHCHAD_POPERECHNOGO_SECHENIYA": "Площадь поперечного сечения",
    "SECHENIE_PODKLYUCHAEMOGO_PROVODA": "Сечение подключаемого провода",
    "MATERIAL_ZHILY": "Материал жилы",
    "MATERIAL_IZOLYATSII_I_OBOLOCHKI": "Материал изоляции и оболочки",
    "NALICHIE_METALLICHESKOY_BRONI": "Металлическая броня",
    "GIBKOST": "Гибкость",
    "GOST": "ГОСТ",
    "IZOLYATSIYA": "Изоляция",
    "OBOLOCHKA": "Оболочка",
    "MATERIAL": "Материал",
    "MATERIAL_KORPUSA": "Материал корпуса",
    "MATERIAL_IZOLIRUYUSHCHEY_CHASTI": "Материал изолирующей части",
    "TSVET": "Цвет",
    "TSVET_KORPUSA": "Цвет корпуса",
    "FORMA": "Форма",
    "DLINA": "Длина",
    "DLINA_SHNURA": "Длина шнура",
    "DLINA_RULONA": "Длина рулона",
    "DLINA_KABELYA": "Длина кабеля",
    "SHIRINA": "Ширина",
    "VYSOTA": "Высота",
    "GLUBINA": "Глубина",
    "DIAMETR": "Диаметр",
    "DIAMETR_MM": "Диаметр, мм",
    "RAZMER": "Размер",
    "RAZMERY": "Размеры",
    "TIP_RUCHNOGO_INSTRUMENTA": "Тип ручного инструмента",
    "TIP_IZMERITELNOGO_INSTRUMENTA": "Тип измерительного инструмента",
    "TIP_KREPEZHNOGO_IZDELIYA": "Тип крепежа",
    "TIP_SOEDINITELYA": "Тип соединителя",
}
CATEGORY_LABELS = {
    "rozetki_vyklyuchateli_korobki": "Розетки, выключатели, коробки",
    "nizkovoltnaya_apparatura": "Низковольтная аппаратура",
    "izdeliya_dlya_montazha_i_instrument": "Изделия для монтажа и инструмент",
    "svetilniki_lampy": "Светильники и лампы",
    "kabelenesushchie_sistemy": "Кабеленесущие системы",
    "kabel_provod": "Кабели и провода",
    "shkafy_shchity": "Шкафы и щиты",
    "avtomatizatsiya": "Автоматизация",
    "videonablyudenie_skud_signalizatsiya": "Видеонаблюдение, контроль доступа, сигнализация",
    "instrument_kip": "Контрольно-измерительные приборы",
}


def clean_text(value) -> str:
    text = html.unescape("" if value is None else str(value))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def embed_text(product: dict) -> str:
    parts = [f"Название: {clean_text(product.get('name'))}"]
    if product.get("article"):
        parts.append(f"Артикул: {clean_text(product['article'])}")
    category = (product.get("category") or "").split(" / ")[0]
    category = CATEGORY_LABELS.get(category, "")
    if category:
        parts.append(f"Категория: {category}")
    properties = product.get("properties") or {}
    for key in sorted(properties):
        # EKT duplicates technical fields with numeric suffixes and trailing '_'.
        label = PROPERTY_LABELS.get(re.sub(r"_\d+$", "", key.rstrip("_")))
        value = properties[key]
        if not label or value is None or isinstance(value, dict):
            continue
        if isinstance(value, list):
            value = ", ".join(str(v) for v in value if isinstance(v, (str, int, float)))
        value = clean_text(value)
        line = f"{label}: {value}"
        if value and line not in parts:
            parts.append(line)
    # Put variable-length prose last so truncation preserves identity and specs.
    description = clean_text(product.get("description"))
    if description:
        parts.append(f"Описание: {description}")
    return "\n".join(parts)


@lru_cache(maxsize=8)
def encoding(model: str):
    # Fail explicitly for unknown models rather than assuming a tokenizer.
    return tiktoken.encoding_for_model(model)


def input_tokens(text: str, model: str, *, truncate: bool = False) -> list[int]:
    tokens = encoding(model).encode(text, disallowed_special=())
    if not tokens:
        raise ValueError("Embedding input must not be empty")
    if len(tokens) > MAX_INPUT_TOKENS:
        if not truncate:
            raise ValueError("Embedding input exceeds token limit")
        return tokens[:MAX_INPUT_TOKENS]
    return tokens


def token_batches(inputs: list[list[int]]):
    batch, size = [], 0
    for tokens in inputs:
        if not tokens or len(tokens) > MAX_INPUT_TOKENS:
            raise ValueError("Invalid embedding token count")
        if batch and (len(batch) >= MAX_BATCH_ITEMS or size + len(tokens) > MAX_BATCH_TOKENS):
            yield batch
            batch, size = [], 0
        batch.append(tokens)
        size += len(tokens)
    if batch:
        yield batch
