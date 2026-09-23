const log = document.getElementById("log");
const welcome = document.getElementById("welcome");
const form = document.getElementById("composer");
const input = document.getElementById("message");
const sendBtn = document.getElementById("send");
const fileInput = document.getElementById("file");
const attachBtn = document.getElementById("attach");
const chips = document.getElementById("chips");
const statusEl = document.getElementById("status");
const announcement = document.getElementById("announcement");
const cartCount = document.getElementById("cart-count");
const newChatBtn = document.getElementById("new-chat");
const stopBtn = document.getElementById("stop-response");
let activeChat = null;
let viewGeneration = 0;
const reviewDrafts = new Map();
const selectionEdits = new Map();
const retiredChats = new Set();

let localSelectionInputs = null;
let recoveredRequest = null;
let recoveryTimer;
let browseResults = null;
let browseQuery = "";
let reviewFilter = "all";
const compareIds = new Set();
const selectionDock = document.getElementById("selection-dock");

function saveDrafts() {
  if (!currentState.chat_id) return;
  try { sessionStorage.setItem("ekt-work-draft", JSON.stringify({chat: currentState.chat_id, rows: [...reviewDrafts],
    edits: [...selectionEdits], quantities: [...draftQuantities], message: input.value, localSelectionInputs})); } catch {}
}

function selectedInputs() {
  const items = localSelectionInputs || currentState.selection_inputs || (currentState.selection || []).map(item => ({product_id:item.product_id, quantity:item.quantity}));
  return items.map(item => ({...item, quantity: selectionEdits.get(Workflow.key(item)) ?? item.quantity}));
}

function mergeSelection(items) { return Workflow.merge(selectedInputs(), items); }

function knownProduct(id) {
  return (browseResults?.results || []).find(p => p.id === id)
    || (currentState.messages || []).flatMap(m => m.products || []).find(p => p.id === id)
    || (currentState.attachment_review || []).flatMap(r => r.candidates || []).find(p => p.id === id)
    || (currentState.selection || []).find(p => p.product_id === id);
}

async function updateSelection(items, prepare = false) {
  if (busy && (activeChat || recoveredRequest) && !prepare) {
    localSelectionInputs = items; saveDrafts(); renderSelection();
    showStatus("Выбор сохранён в черновике. Проверим его после завершения ответа.");
    return;
  }
  if (busy) return;
  setBusy(true);
  try {
    const response = await fetch(prepare ? "/api/cart/propose-items" : "/api/selection", {
      method: prepare ? "POST" : "PUT",
      headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf, "X-Chat-ID": currentState.chat_id || ""},
      body: JSON.stringify({items})
    });
    const data = await response.json();
    if (response.ok || data.item_errors) { selectionEdits.clear(); localSelectionInputs = null; }
    if (data.cart) renderState(data, {focus: prepare && response.ok ? "proposal" : "preserve"});
    showStatus(response.ok ? "" : formatErrors(data));
    saveDrafts();
  } catch { localSelectionInputs = items; saveDrafts(); showStatus("Нет связи. Выбор сохранён в черновике. Повторите проверку."); }
  finally { setBusy(false); }
}

function formatErrors(data) {
  if (data.item_errors?.length) return data.item_errors.map(error => {
    const row = currentState.attachment_review?.find(r => r.row_id === error.source_id);
    const name = row ? row.filename + " · " + row.source_reference : knownProduct(error.product_id)?.name || "Товар " + error.product_id;
    return name + ": " + error.error;
  }).join("\n");
  return data.error || data.detail && "Проверьте количество и выбранные строки." || "Не удалось выполнить действие.";
}

function renderSelection() {
  const expanded = selectionDock.querySelector("details")?.open || false;
  selectionDock.replaceChildren();
  const items = selectedInputs();
  selectionDock.hidden = !items.length;
  if (!items.length) return;
  const section = el("details", "selection-panel"); section.open = expanded;
  const total = items.reduce((sum, item) => sum + Number(knownProduct(item.product_id)?.unit_price ?? knownProduct(item.product_id)?.price ?? 0) * Number(item.quantity || 0), 0);
  section.append(el("summary", null, "Выбрано: " + new Set(items.map(i => i.product_id)).size + " · ориентировочно " + money(total)));
  const body = el("div", "selection-body");
  body.append(el("p", "article", "Черновик. Неизвестные цены не включены в итог. Проверьте условия перед добавлением."));
  for (const item of items) {
    const product = knownProduct(item.product_id);
    const row = el("div", "selection-row");
    const key = Workflow.key(item);
    const source = currentState.attachment_review?.find(r => r.row_id === item.source_id);
    const name = product?.name || "Товар " + item.product_id;
    const label = el("label", null, name + (source ? " · " + source.filename + " · " + source.source_reference : ""));
    const quantity = el("input", "quantity-input");
    quantity.type = "number"; quantity.min = "0.000001"; quantity.step = "any"; quantity.value = item.quantity;
    quantity.setAttribute("aria-label", "Выбрано — количество для " + name + (source ? " · " + source.source_reference : ""));
    quantity.addEventListener("input", () => {
      selectionEdits.set(key, quantity.value); saveDrafts();
      if (proposal) { log.querySelector(".slip")?.remove(); proposal = null; currentState.proposal = null; clearTimeout(expiryTimer); }
      prepare.hidden = false;
    });
    label.append(quantity, document.createTextNode(" " + (item.source_unit || product?.unit || "единица не подтверждена")));
    const remove = el("button", "ghost", "Убрать"); remove.type = "button";
    remove.addEventListener("click", () => { selectionEdits.delete(key); updateSelection(selectedInputs().filter(x => Workflow.key(x) !== key)); });
    row.append(label, remove);
    for (const issue of product?.validation_errors || []) row.append(el("p", "field-error", issue));
    body.append(row);
  }
  section.append(body);
  const prepare = el("button", "primary", "Проверить и добавить"); prepare.type = "button";
  prepare.hidden = Boolean(currentState.proposal) && !selectionEdits.size;
  prepare.disabled = busy;
  prepare.addEventListener("click", () => updateSelection(selectedInputs(), true));
  selectionDock.append(section, prepare);
}

async function loadAlternatives(productId) {
  if (busy) return;
  setBusy(true); showStatus("Сравниваем характеристики…");
  try {
    const response = await fetch("/api/products/" + productId + "/alternatives", {method: "POST", headers: {"X-CSRF-Token": csrf, "X-Chat-ID": currentState.chat_id || ""}});
    const data = await response.json();
    if (data.cart) renderState(data);
    showStatus(response.ok ? "" : data.error || "Не удалось найти аналоги.");
    if (response.ok) announcement.textContent = data.text || "Сравнение готово.";
  } catch { showStatus("Нет связи. Попробуйте ещё раз."); }
  finally { setBusy(false); }
}

document.querySelectorAll(".suggestion").forEach(button => {
  button.addEventListener("click", () => {
    input.value = button.firstChild.textContent.trim();
    resizeMessage();
    input.focus();
  });
});

const infoDialog = document.getElementById("info-dialog");
document.getElementById("open-info").addEventListener("click", () => infoDialog.showModal());

let files = [];
let csrf = "";
let proposal = null;
let busy = true;
let expiryTimer;
const draftQuantities = new Map();
const shownResults = new Map();
let currentState = { history: [], products: [], proposal: null, cart: { count: 0 } };

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function money(value) {
  return value == null ? "—" : Number(value).toLocaleString("ru-RU", { maximumFractionDigits: 2 }) + " ₸";
}

function showStatus(text) {
  statusEl.textContent = text;
  statusEl.hidden = !text;
  if (text) announcement.textContent = text;
}

function setBusy(value) {
  busy = value;
  log.setAttribute("aria-busy", String(value));
  sendBtn.disabled = value;
  const answering = Boolean(activeChat || recoveredRequest);
  sendBtn.hidden = answering;
  attachBtn.disabled = value;
  input.disabled = !csrf;
  newChatBtn.disabled = !csrf || (value && !answering);
  stopBtn.hidden = !answering;
  log.querySelectorAll("button").forEach(button => { button.disabled = button.dataset.unavailable === "true" || (value && !(answering && button.dataset.local === "true")); });
  log.querySelectorAll("input, select").forEach(field => { field.disabled = value && !answering; });
  selectionDock.querySelectorAll("input").forEach(field => { field.disabled = value && !answering; });
  selectionDock.querySelectorAll("button").forEach(button => { button.disabled = value; });
}

function safeLink(label, value) {
  try {
    const url = new URL(value, location.origin);
    if (!["http:", "https:"].includes(url.protocol)) return null;
    const link = el("a", null, label);
    link.href = url.href;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    return link;
  } catch { return null; }
}

function bubble(role, text) {
  const node = el("article", role === "user" ? "bubble user" : "bubble");
  node.append(el("p", "meta-line", role === "user" ? "Вы" : "Консультант"), el("p", "message-text", text));
  log.append(node);
  return node;
}

function renderHits(items, snapshot = currentState.snapshot) {
  if (!items?.length) return;
  const verified = items.filter(item => !item.unverified_specs?.length);
  const uncertain = items.filter(item => item.unverified_specs?.length);
  const section = el("section", "results");
  const heading = el("div", "results-heading");
  heading.append(el("h2", null, verified.length ? "Найдено в каталоге" : "Нужно уточнить характеристики"));
  if (verified.length) heading.append(el("span", "article", String(verified.length)));
  heading.append(snapshotDetails(snapshot));
  section.append(heading);
  if (verified.length) section.append(productList(verified));
  if (uncertain.length) {
    const review = el("details", "uncertain-results");
    review.open = !verified.length;
    review.append(el("summary", null, "Характеристики не подтверждены · " + uncertain.length),
      el("p", "results-note", "Проверьте характеристики у поставщика или уточните артикул в поиске."),
      productList(uncertain));
    section.append(review);
  }
  log.append(section);
}

function snapshotDetails(snapshot) {
  const details = el("details", "snapshot-note");
  details.append(el("summary", null, "О ценах и наличии"));
  const date = new Date(snapshot?.indexed_at);
  const stamp = snapshot?.indexed_at && !Number.isNaN(date.getTime())
    ? "Каталог собран " + date.toLocaleDateString("ru-RU") + ". " : "";
  details.append(el("p", null, stamp + "Цены и остатки — из снимка каталога. Дата обновления данных поставщиком не подтверждена."));
  return details;
}

function productList(items) {
  const list = el("div", "product-list");
  const wrap = el("div", "hits");
  let shown = 0;
  const key = items.map(item => item.id).join(",");
  const more = el("button", "show-more");
  more.type = "button";
  const count = el("p", "results-count");
  const footer = el("div", "results-footer");
  footer.append(count, more);
  const appendPage = (focus = false) => {
    const next = items.slice(shown, shown + (focus ? 5 : shownResults.get(key) || 5)).map(renderProduct);
    wrap.append(...next);
    shown += next.length;
    shownResults.set(key, shown);
    count.textContent = "Показано " + shown + " из " + items.length;
    more.textContent = "Показать ещё " + Math.min(5, items.length - shown);
    more.hidden = shown >= items.length;
    if (focus && next.length) {
      const title = next[0].querySelector("h3");
      title.tabIndex = -1;
      title.focus({ preventScroll: true });
      next[0].scrollIntoView({ block: "start" });
    }
  };
  more.addEventListener("click", () => appendPage(true));
  appendPage();
  list.append(wrap);
  if (items.length > 5) list.append(footer);
  return list;
}

function renderProduct(item) {
  const row = el("article", "hit");
  if (currentState.selection?.some(line => line.product_id === item.id)) row.classList.add("is-selected");
  const thumb = el("div", "hit-thumb");
  if (item.image) {
    const image = el("img");
    image.alt = "";
    image.addEventListener("error", () => thumb.remove());
    thumb.append(image);
    image.src = item.image;
  }
  const body = el("div", "hit-main");
  const title = el("h3");
  const dimension = item.name.match(/\d+\s*[xх×]\s*\d+(?:[,.]\d+)?/i);
  if (dimension) {
    title.append(document.createTextNode(item.name.slice(0, dimension.index)),
      el("strong", "product-dimension", dimension[0].replace(/\s*[xх×]\s*/i, " × ")),
      document.createTextNode(item.name.slice(dimension.index + dimension[0].length)));
  } else title.textContent = item.name;
  body.append(title);
  const compareLabel = el("label", "compare-choice");
  const compare = el("input"); compare.type = "checkbox"; compare.checked = compareIds.has(item.id);
  compareLabel.append(compare, document.createTextNode("Сравнить"));
  compare.addEventListener("change", () => {
    if (compare.checked && compareIds.size >= 3) { compare.checked = false; showStatus("Для сравнения выберите не больше трёх товаров."); return; }
    compare.checked ? compareIds.add(item.id) : compareIds.delete(item.id);
    updateCompareButton();
  });
  body.append(compareLabel);
  if (item.analog_reason) body.append(el("p", "article", item.analog_reason));
  if (item.comparison) {
    const comparison = el("details", "product-details");
    comparison.append(el("summary", null, "Сравнение с исходным товаром"));
    for (const field of item.comparison.matches || []) comparison.append(el("p", null, "Совпадает — " + field.attribute + ": " + field.candidate));
    for (const field of item.comparison.differences || []) comparison.append(el("p", null, field.attribute + ": " + field.source + " → " + field.candidate));
    if (item.comparison.unknowns?.length) comparison.append(el("p", null, "Нужно уточнить: " + item.comparison.unknowns.join(", ")));
    body.append(comparison);
  }
  const alternatives = el("button", "text-button alternatives-button", "Показать аналоги");
  alternatives.type = "button";
  alternatives.setAttribute("aria-label", "Показать аналоги для " + item.name);
  alternatives.addEventListener("click", () => loadAlternatives(item.id));
  body.append(alternatives);
  const summary = el("div", "hit-summary");
  const price = el("span", "price-group");
  const unitLabel = item.unit_known === true ? item.unit : "единица продажи не подтверждена";
  price.append(el("strong", "hit-price", item.price_label || money(item.price)),
    el("span", "price-unit", item.unit_known === true ? " / " + unitLabel : " · " + unitLabel));
  summary.append(price);
  const options = item.purchase_options || { can_add: false, reason: "Обновите страницу для проверки количества." };
  const stock = item.quantity == null ? "Остаток неизвестен" : item.quantity <= 0 ? "Нет по снимку каталога"
    : "Остаток: " + Number(item.quantity).toLocaleString("ru-RU", { maximumFractionDigits: 6 })
      + (item.unit_known === true ? " " + unitLabel : " (единица не подтверждена)");
  summary.append(el("span", item.quantity === 0 ? "stock out" : "stock", stock));
  body.append(summary);
  if (item.min_quantity) body.append(el("p", "article", "Минимальная партия: " + item.min_quantity + " " + unitLabel));
  if (item.quantity_step) body.append(el("p", "article", "Кратность: " + item.quantity_step + " " + unitLabel));
  for (const length of item.lengths || []) body.append(el("p", "article", (length.label || "Длина") + ": " + (length.raw || length.value || length.metres || "неизвестна") + " · не является количеством упаковок"));
  const meta = el("div", "hit-meta");
  const details = el("details", "product-details");
  details.append(el("summary", null, "Подробнее"), el("p", null, "Артикул " + (item.article || "ID " + item.id)));
  if (item.source_observed_at) {
    const observed = new Date(item.source_observed_at);
    if (!Number.isNaN(observed.getTime()))
      details.append(el("p", null, "Данные товара получены " + observed.toLocaleString("ru-RU")));
  }
  if (item.purchase_rule_note) details.append(el("p", null,
    item.purchase_rule_note.startsWith("Исходное поле KRATNOST_MIN:")
      ? "Минимальную партию и кратность уточните у поставщика."
      : item.purchase_rule_note));
  if (item.description || item.spec_snippet) details.append(el("p", null, item.description || item.spec_snippet));
  for (const [key, value] of Object.entries(item.properties || {})) details.append(el("p", null, key + ": " + value));
  if (item.supplier_availability) details.append(el("p", "article", item.supplier_availability.note));
  for (const store of item.stores || []) {
    if (store.quantity != null && store.quantity > 0) details.append(el("p", null, store.name + ": " + store.quantity + " " + unitLabel));
  }
  if (details.childElementCount > 1) meta.append(details);
  const links = el("div", "product-links");
  const certificates = [...new Set([item.certificate, ...(item.certificates || [])].filter(Boolean))];
  for (const [label, url] of [["Открыть на ekt.kz", item.url], ...certificates.map((url, i) => ["Сертификат " + (i + 1), url])]) {
    if (url) { const link = safeLink(label, url); if (link) links.append(link); }
  }
  if (links.childNodes.length) details.append(links);
  if (meta.childNodes.length) body.append(meta);
  if (item.unverified_specs?.length) {
    body.append(el("p", "cart-limit", "Не подтверждено: " + item.unverified_specs.join(", ") + "."));
  }
  if (options.can_add && item.unit_known === true && item.purchase_rules_confirmed === true && !item.unverified_specs?.length) {
    const controls = el("div", "hit-actions");
    if (Number(options.existing)) controls.append(el("p", "in-cart", "В корзине: " + Number(options.existing).toLocaleString("ru-RU", { maximumFractionDigits: 6 }) + " " + (item.unit || "ед.")));
    const label = el("label", "quantity-label");
    label.append(el("span", "quantity-caption", "Количество"));
    const quantity = el("input", "quantity-input");
    quantity.type = "number";
    quantity.inputMode = "decimal";
    quantity.min = options.suggested_quantity;
    quantity.step = options.step;
    quantity.value = draftQuantities.get(item.id) || options.suggested_quantity;
    quantity.max = options.remaining;
    quantity.setAttribute("aria-label", "Количество для " + item.name);
    quantity.addEventListener("input", () => { draftQuantities.set(item.id, quantity.value); saveDrafts(); });
    label.append(quantity, el("span", "quantity-unit", item.unit || "ед."));
    const add = el("button", "add-product", "Выбрать");
    add.type = "button";
    add.dataset.local = "true";
    add.setAttribute("aria-label", "Выбрать " + item.name);
    add.addEventListener("click", () => {
      const value = Number(quantity.value);
      if (!quantity.value || !Number.isFinite(value) || value <= 0) {
        quantity.focus();
        showStatus("Укажите количество больше нуля.");
        return;
      }
      if (quantity.validity.rangeOverflow) {
        quantity.focus();
        showStatus("Доступно для добавления: " + options.remaining + " " + (item.unit || "ед."));
        return;
      }
      if (!quantity.checkValidity()) {
        quantity.focus();
        showStatus("Проверьте количество товара.");
        return;
      }
      prepareProduct(item.id, quantity.value);
    });
    const direct = el("button", "text-button", "Добавить в корзину…"); direct.type = "button";
    direct.setAttribute("aria-label", "Проверить и добавить " + item.name);
    direct.addEventListener("click", () => { if (quantity.reportValidity()) updateSelection(mergeSelection([{product_id:item.id, quantity:quantity.value}]), true); });
    controls.append(label, add, direct);
    if (item.image) row.append(thumb);
    row.append(body, controls);
  } else {
    if (item.image) row.append(thumb);
    row.append(body);
    if (!item.unverified_specs?.length && !(item.quantity != null && Number(item.quantity) <= 0))
      body.append(el("p", "cart-limit", options.reason || "Условия покупки не подтверждены. Уточните их у поставщика."));
  }
  return row;
}

async function prepareProduct(productId, quantity) {
  return updateSelection(mergeSelection([{product_id:productId, quantity}]));
}

function renderProposal(value) {
  proposal = value;
  clearTimeout(expiryTimer);
  if (!value) return;
  const remaining = value.expires_at * 1000 - Date.now();
  if (remaining <= 0) { expireProposal(); return; }
  const slip = el("aside", "slip");
  slip.dataset.proposal = value.id;
  slip.setAttribute("aria-labelledby", "proposal-heading");
  const body = el("div", "slip-body");
  const heading = el("h2", null, "Проверьте выбор");
  heading.id = "proposal-heading";
  heading.tabIndex = -1;
  body.append(heading);
  const lines = el("div", "proposal-lines");
  for (const item of value.items) {
    const line = el("div", "proposal-line");
    line.append(el("p", "proposal-name", item.name), el("p", "article",
      Number(item.quantity).toLocaleString("ru-RU", { maximumFractionDigits: 6 }) + " " + item.unit + " × " + item.price_label), el("strong", "proposal-price", item.total_label));
    lines.append(line);
  }
  body.append(lines);
  const edit = el("button", "text-button", "Изменить количество"); edit.type = "button";
  edit.addEventListener("click", () => {
    slip.remove(); proposal = null; currentState.proposal = null; clearTimeout(expiryTimer);
    renderSelection(); const details = selectionDock.querySelector("details"); if (details) details.open = true;
    selectionDock.querySelector("input")?.focus();
  });
  body.append(edit);
  for (const item of value.source_inputs || []) {
    const source = currentState.attachment_review?.find(row => row.row_id === item.source_id);
    if (source) body.append(el("p", "article", source.filename + " · " + source.source_reference + ": " + item.quantity + " " + (source.source_unit || "")));
  }
  const footer = el("div", "proposal-footer");
  const total = el("p", "proposal-total");
  total.append(el("span", null, "Итого"), el("strong", null, value.total_label));
  footer.append(total);
  const actions = el("div", "actions");
  for (const [name, label, className] of [["cancel", "Отмена", "ghost"], ["confirm", "Добавить в корзину", "primary"]]) {
    const button = el("button", className, label);
    button.type = "button";
    button.addEventListener("click", () => changeCart(name, value.id));
    actions.append(button);
  }
  footer.append(actions);
  body.append(footer);
  slip.append(body);
  log.append(slip);
  expiryTimer = setTimeout(() => {
    const hadFocus = slip.contains(document.activeElement);
    slip.remove();
    if (proposal?.id === value.id) proposal = null;
    currentState.proposal = null;
    expireProposal();
    if (hadFocus) selectionDock.querySelector("button")?.focus();
  }, remaining);
}

function expireProposal() {
  proposal = null; currentState.proposal = null;
  renderSelection();
  const button = selectionDock.querySelector("button.primary");
  if (button) { button.hidden = false; button.textContent = "Обновить предложение"; }
  showStatus("Предложение истекло. Выбор сохранён — обновите стоимость и подтвердите снова.");
}

function renderState(data, { focus } = {}) {
  if (retiredChats.has(data.chat_id)) return;
  if (data.chat_id && currentState.chat_id && data.chat_id !== currentState.chat_id) {
    draftQuantities.clear(); reviewDrafts.clear(); selectionEdits.clear(); shownResults.clear(); localSelectionInputs = null; browseResults = null;
  }
  const scrollTop = log.scrollTop;
  const focusedLabel = document.activeElement?.getAttribute("aria-label");
  currentState = { ...currentState, ...data };
  if (["added", "already_added"].includes(data.status)) { draftQuantities.clear(); selectionEdits.clear(); localSelectionInputs = null; }
  if (selectionEdits.size || localSelectionInputs) currentState.proposal = null;
  for (const item of currentState.proposal?.items || []) {
    if (!draftQuantities.has(item.product_id)) draftQuantities.set(item.product_id, String(item.quantity));
  }
  if (data.csrf_token) csrf = data.csrf_token;
  cartCount.textContent = String(currentState.cart?.items?.length || 0);
  cartCount.hidden = !currentState.cart?.items?.length;
  log.replaceChildren();
  const messages = currentState.messages?.length ? currentState.messages : currentState.history || [];
  if (!messages.length && !currentState.products?.length && !currentState.proposal) log.append(welcome);
  for (const item of messages) {
    bubble(item.role, item.content);
    if (item.sources?.length) renderConsultation({sources: item.sources});
    renderHits(item.products, item.snapshot);
  }
  renderConsultation({...currentState, sources: [], snapshot: null});
  if (!currentState.messages?.length) renderHits(currentState.products);
  renderSelection();
  renderProposal(currentState.proposal);
  renderBrowseResults();
  if (["added", "already_added"].includes(data.status)) {
    const added = el("div", "added");
    added.setAttribute("role", "status");
    added.tabIndex = -1;
    added.append(el("span", null, "Добавлено в корзину"));
    const link = el("a", null, "Открыть корзину");
    link.href = "/cart";
    added.append(link);
    log.append(added);
  }
  setBusy(busy);
  const replies = log.querySelectorAll(".bubble:not(.user)");
  const target = log.querySelector(".added") || log.querySelector(".slip") || replies[replies.length - 1]
    || log.querySelector(".spec-review") || log.querySelector(".results")
    || log.lastElementChild;
  if (focus === "preserve") {
    log.scrollTop = scrollTop;
    if (focusedLabel) [...log.querySelectorAll("[aria-label]")].find(el => el.getAttribute("aria-label") === focusedLabel)?.focus({preventScroll:true});
  } else if (target && target !== welcome) target.scrollIntoView({ block: "start" });
  if (focus === "proposal") document.getElementById("proposal-heading")?.focus({ preventScroll: true });
  if (focus === "selection") document.getElementById("selection-heading")?.focus();
  if (focus === "input") requestAnimationFrame(() => input.focus({ preventScroll: true }));
  if (focus === "result") requestAnimationFrame(() => (log.querySelector(".added") || input).focus({ preventScroll: true }));
}

function renderConsultation(state) {
  const sources = el("div", "consultation-sources");
  for (const source of state.sources || []) {
    const link = safeLink(source.title || "Источник", source.url);
    if (link) sources.append(link);
  }
  if (sources.childNodes.length) log.append(sources);
  if (state.snapshot?.indexed_at && !state.products?.length) log.append(snapshotDetails(state.snapshot));
  for (const issue of state.attachment_issues || []) log.append(el("p", "attachment-issue", issue));
  if (state.attachment_review?.length) renderReview(state);
}

async function reviewAction(item, action, values = {}) {
  if (busy) return;
  setBusy(true);
  try {
    const response = await fetch("/api/review/" + item.row_id, {method:"POST",
      headers:{"Content-Type":"application/json", "X-CSRF-Token":csrf, "X-Chat-ID":currentState.chat_id},
      body:JSON.stringify({action,...values})});
    const data = await response.json();
    if (!response.ok) { showStatus(formatErrors(data)); return; }
    reviewDrafts.delete(item.row_id); saveDrafts(); renderState(data, {focus:"preserve"});
    showStatus(action === "reopen" ? "Строка открыта повторно. Новое добавление потребует подтверждения." : "Строка обновлена.");
  } catch { showStatus("Нет связи. Исправления сохранены на экране, повторите действие."); }
  finally { setBusy(false); }
}

function rowDraft(item) {
  const saved = reviewDrafts.get(item.row_id);
  const product = saved?.product && item.candidate_product_ids.includes(Number(saved.product)) ? saved.product
    : item.status === "resolved" ? String(item.candidates[0]?.id || "") : "";
  return {...saved, product, quantity:saved?.quantity ?? item.quantity ?? "", checked:!["added","excluded"].includes(item.completion) && Boolean(saved?.checked)};
}

let reviewPage = 0;
function renderReview(state) {
  const review = el("section", "spec-review"); review.id = "spec-review";
  const rows = state.attachment_review;
  const counts = {added:0, excluded:0, ready:0, unresolved:0};
  for (const row of rows) {
    const draft = rowDraft(row); const product = row.candidates.find(p => p.id === Number(draft.product));
    counts[["added","excluded"].includes(row.completion) ? row.completion : Workflow.eligible(row,product,draft.quantity) ? "ready" : "unresolved"]++;
  }
  review.append(el("h2", null, "Разбор спецификации"), el("p", "review-summary", `${rows.length} строк · готово ${counts.ready} · уточнить ${counts.unresolved} · добавлено ${counts.added} · исключено ${counts.excluded}`));
  for (const doc of state.document_summary || []) review.append(el("p", "article", `${doc.filename}: сверено ${doc.reviewed_rows} из ${doc.source_rows} непустых строк данных.`));
  const tools = el("div", "tool-row");
  const filter = el("select"); filter.setAttribute("aria-label","Фильтр строк");
  for (const [value,label] of [["all","Все строки"],["issues","Нужно уточнить"],["ready","Готовые"],["added","Добавленные"],["excluded","Исключённые"]]) filter.append(new Option(label,value));
  filter.value = reviewFilter;
  filter.addEventListener("change", () => {reviewFilter=filter.value;reviewPage=0;renderState(currentState,{focus:"preserve"});});
  const selectAll = el("button","ghost","Выбрать все готовые"); selectAll.type="button";
  selectAll.addEventListener("click", () => {
    for (const row of rows) {
      const draft=rowDraft(row); const product=row.candidates.find(p=>p.id===Number(draft.product));
      if (Workflow.eligible(row,product,draft.quantity)) reviewDrafts.set(row.row_id,{...draft,checked:true});
    }
    saveDrafts();renderState(currentState,{focus:"preserve"});
  });
  const clear = el("button","ghost","Снять выбор строк"); clear.type="button";
  clear.addEventListener("click",()=>{for(const row of rows) reviewDrafts.set(row.row_id,{...rowDraft(row),checked:false});saveDrafts();renderState(currentState,{focus:"preserve"});});
  tools.append(filter,selectAll,clear); review.append(tools);
  const filtered = rows.filter(row => {
    const draft=rowDraft(row); const ready=Workflow.eligible(row,row.candidates.find(p=>p.id===Number(draft.product)),draft.quantity);
    return reviewFilter==="all" || (reviewFilter==="ready" && ready) || (reviewFilter==="issues" && !ready && !["added","excluded"].includes(row.completion)) || row.completion===reviewFilter;
  });
  reviewPage=Math.min(reviewPage,Math.max(0,Math.ceil(filtered.length/50)-1));
  const labels={resolved:"Найден по артикулу",ambiguous:"Выберите совпадение",unresolved:"Требуется уточнение",quantity_required:"Укажите количество"};
  for (const item of filtered.slice(reviewPage*50,reviewPage*50+50)) {
    const row=el("div","spec-row"); row.dataset.rowId=item.row_id;
    const draft=rowDraft(item); const done=["added","excluded"].includes(item.completion);
    row.append(el("p","article",item.filename+" · "+item.source_reference),el("p",null,item.query||item.source_text||"Нечитаемая строка"),
      el("p","article",done ? (item.completion==="added" ? "Добавлено в корзину" : "Исключено: "+item.exclude_reason) : labels[item.status]||item.status));
    if(done){
      const reopen=el("button","ghost",item.completion==="added" ? "Добавить повторно" : "Вернуть строку");reopen.type="button";
      reopen.addEventListener("click",()=>reviewAction(item,"reopen"));row.append(reopen);review.append(row);continue;
    }
    const include=el("input");include.type="checkbox";include.checked=draft.checked;
    const select=el("select");select.setAttribute("aria-label","Товар: "+item.source_reference);select.append(new Option("Выберите товар",""));
    for(const product of item.candidates){
      const option=new Option(`${product.article||product.id} · ${product.name} · ${product.price_label||"цена неизвестна"} / ${product.unit_label||product.unit} · остаток ${product.quantity??"неизвестен"}`,String(product.id));
      select.append(option);
    }
    select.value=draft.product;
    const quantity=el("input","quantity-input");quantity.type="number";quantity.min="0.000001";quantity.step="any";quantity.value=draft.quantity;
    quantity.setAttribute("aria-label","Количество: "+item.source_reference);
    const includeLabel=el("label","spec-choice","Выбрать");includeLabel.prepend(include);
    const controls=el("div","spec-controls");controls.append(includeLabel,select,quantity,el("span","article",item.source_unit||"Единица в документе не указана"));row.append(controls);
    const detail=el("div","candidate-facts");row.append(detail);
    const update=()=>{
      reviewDrafts.set(item.row_id,{...reviewDrafts.get(item.row_id),checked:include.checked,product:select.value,quantity:quantity.value});saveDrafts();
      const product=item.candidates.find(p=>p.id===Number(select.value));detail.replaceChildren();
      if(product){
        const options=product.purchase_options;
        detail.append(el("p","article",`Единица продажи: ${product.unit_label||product.unit}. Остаток: ${product.quantity??"неизвестен"}.`));
        if(options && !options.can_add)detail.append(el("p","field-error",options.reason));
        if(item.source_unit && Workflow.unit(item.source_unit)!==Workflow.unit(product.unit))detail.append(el("p","field-error","Единицы различаются. Уточните единицу; автоматического пересчёта нет."));
        for(const field of product.comparison?.differences||[])detail.append(el("p","article",`${field.attribute}: ${field.source} → ${field.candidate}`));
        if(product.comparison?.unknowns?.length)detail.append(el("p","article","Не подтверждено: "+product.comparison.unknowns.join(", ")));
        const link=safeLink("Карточка поставщика",product.url);if(link)detail.append(link);
      }
    };
    include.addEventListener("change",update);select.addEventListener("change",update);quantity.addEventListener("input",update);update();
    for(const error of state.item_errors||[])if(error.source_id===item.row_id || item.candidate_product_ids.includes(error.product_id))row.append(el("p","field-error",error.error));
    const edit=el("details","row-repair");edit.append(el("summary",null,"Исправить или исключить строку"));
    const query=el("input");query.value=draft.query??item.query;query.maxLength=500;query.setAttribute("aria-label","Исправить артикул или описание: "+item.source_reference);
    const search=el("button","ghost","Найти замену");search.type="button";search.addEventListener("click",()=>reviewAction(item,"search",{query:query.value}));
    const unit=el("input");unit.value=draft.unit??item.source_unit;unit.maxLength=40;unit.setAttribute("aria-label","Уточнённая единица документа: "+item.source_reference);
    const unitSave=el("button","ghost","Уточнить единицу");unitSave.type="button";unitSave.addEventListener("click",()=>reviewAction(item,"unit",{source_unit:unit.value}));
    const reason=el("input");reason.value=draft.reason||"";reason.maxLength=500;reason.placeholder="Причина исключения";reason.setAttribute("aria-label","Причина исключения: "+item.source_reference);
    for(const [field,key] of [[query,"query"],[unit,"unit"],[reason,"reason"]])field.addEventListener("input",()=>{reviewDrafts.set(item.row_id,{...rowDraft(item),[key]:field.value});saveDrafts();});
    const exclude=el("button","ghost","Исключить");exclude.type="button";exclude.addEventListener("click",()=>reviewAction(item,"exclude",{reason:reason.value}));
    edit.append(query,search,unit,unitSave,reason,exclude);
    if(item.candidate_product_ids.length){const alternatives=el("button","ghost","Найти аналоги");alternatives.type="button";alternatives.addEventListener("click",()=>reviewAction(item,"alternatives"));edit.append(alternatives);}
    row.append(edit);review.append(row);
  }
  if(filtered.length>50){
    const paging=el("div","actions");
    for(const [delta,label] of [[-1,"Предыдущие строки"],[1,"Следующие строки"]]){
      const button=el("button","ghost",label);button.type="button";button.dataset.unavailable=String(delta<0?reviewPage===0:(reviewPage+1)*50>=filtered.length);button.disabled=button.dataset.unavailable==="true";
      button.addEventListener("click",()=>{reviewPage+=delta;renderState(currentState,{focus:"preserve"});});paging.append(button);
    }
    paging.append(el("span","article",`${reviewPage*50+1}–${Math.min((reviewPage+1)*50,filtered.length)} из ${filtered.length}`));review.append(paging);
  }
  const selectedCount=rows.filter(r=>rowDraft(r).checked).length;
  const prepare=el("button","primary",selectedCount>50 ? "Подготовить следующую партию (до 50 строк)" : "Проверить выбранные строки");prepare.type="button";
  prepare.addEventListener("click",()=>{
    const manual=selectedInputs().filter(i=>!i.source_id);
    const selected=rows.filter(r=>rowDraft(r).checked).slice(0,Math.max(0,50-manual.length));
    if(!selected.length){showStatus("Выберите строки. В одном предложении — до 50 строк вместе с ручным выбором.");return;}
    const items=selected.map(row=>{const draft=rowDraft(row);return {product_id:Number(draft.product),quantity:draft.quantity,source_id:row.row_id};});
    if(items.some(i=>!i.product_id||!(Number(i.quantity)>0))){showStatus("Выберите товар и положительное количество для отмеченных строк.");return;}
    updateSelection(Workflow.merge(manual,items),true);
  });
  review.append(el("p","article","Отмечено строк: "+selectedCount+". Добавленные строки исключаются из следующей партии."),prepare);log.append(review);
}

async function changeCart(action, id) {
  if (busy) return;
  setBusy(true);
  showStatus("");
  try {
    const res = await fetch("/api/cart/" + action, {
      method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
      body: JSON.stringify({ proposal_id: id })
    });
    const data = await res.json();
    if (data.cart) renderState(data, { focus: res.ok ? "result" : "input" });
    if (!res.ok) showStatus(data.error || "Не удалось изменить корзину.");
  } catch { showStatus("Нет связи. Повторите действие: товар не добавится дважды."); }
  finally { setBusy(false); }
}

function renderFiles() {
  chips.replaceChildren();
  chips.hidden = !files.length;
  files.forEach((file, index) => {
    const li = el("li", null, file.name + " ");
    const remove = el("button", "remove-file", "Убрать");
    remove.type = "button";
    remove.addEventListener("click", () => { files.splice(index, 1); renderFiles(); });
    li.append(remove);
    chips.append(li);
  });
}
attachBtn.addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", () => {
  const selected = Array.from(fileInput.files || []);
  const next = [...files, ...selected];
  fileInput.value = "";
  if (next.length > 5 || next.some(f => f.size > 10 * 1024 * 1024) || next.reduce((n, f) => n + f.size, 0) > 20 * 1024 * 1024) {
    showStatus("До 5 файлов: 10 МБ на файл, 20 МБ суммарно.");
    return;
  }
  files = next;
  showStatus("");
  renderFiles();
});
function resizeMessage() {
  input.style.height = "auto";
  input.style.overflowY = input.scrollHeight > 128 ? "auto" : "hidden";
  input.style.height = Math.min(input.scrollHeight, 128) + "px";
}
input.addEventListener("input", resizeMessage);
input.addEventListener("keydown", event => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); form.requestSubmit(); }
});
form.addEventListener("submit", async event => {
  event.preventDefault();
  if (busy) return;
  const text = input.value.trim();
  if (!text && !files.length) return;
  const body = new FormData();
  body.append("message", text);
  body.append("proposal_id", proposal?.id || "");
  body.append("chat_id", currentState.chat_id || "");
  const turn = {id: crypto.randomUUID(), controller: new AbortController(), generation: viewGeneration};
  body.append("request_id", turn.id);
  for (const file of files) body.append("files", file);
  const submittedFiles = [...files];
  const restoreDraft = () => {
    input.value = text + (input.value ? "\n\n" + input.value : "");
    resizeMessage();
  };
  turn.restore = restoreDraft;
  activeChat = turn;
  input.value = ""; resizeMessage();
  setBusy(true);
  showStatus("");
  welcome.remove();
  bubble("user", text || "Посмотрите вложение.");
  const waitingText = files.length ? "Читаю спецификацию и сопоставляю товары…" : "Подбираю ответ…";
  const pending = bubble("assistant", waitingText);
  pending.classList.add("pending-reply");
  pending.scrollIntoView({ block: "end" });
  announcement.textContent = waitingText;
  try {
    const res = await fetch("/api/chat", { method: "POST", headers: { "X-CSRF-Token": csrf }, body, signal: turn.controller.signal });
    const data = await res.json();
    if (activeChat !== turn || turn.generation !== viewGeneration) return;
    if (!res.ok) {
      restoreDraft();
      renderState(currentState);
      showStatus(data.error || data.detail || "Не удалось получить ответ.");
      return;
    }
    files = files.filter(file => !submittedFiles.includes(file));
    fileInput.value = "";
    renderFiles();
    renderState(data);
    announcement.textContent = data.text || "Ответ готов.";
    showStatus(data.ok === false ? data.error : "");
  } catch (error) {
    if (activeChat !== turn || turn.generation !== viewGeneration) return;
    restoreDraft();
    renderState(currentState);
    showStatus("Нет связи с сервером. Сообщение сохранено в поле ввода.");
  }
  finally { if (activeChat === turn) { activeChat = null; setBusy(false); } }
});

stopBtn.addEventListener("click", async () => {
  const turn = activeChat;
  if (!turn) return;
  stopBtn.disabled = true;
  try {
    const response = await fetch("/api/chat/stop", {method: "POST", headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf}, body: JSON.stringify({request_id: turn.id})});
    const data = await response.json();
    if (!response.ok) { showStatus(data.error || "Не удалось остановить ответ."); return; }
    if (activeChat !== turn) return;
    turn.controller.abort(); activeChat = null;
    if (data.stopped) turn.restore();
    renderState(data);
    showStatus(data.stopped ? "Ответ остановлен. Сообщение сохранено для повторной отправки." : "Ответ уже готов.");
    setBusy(false);
  } catch { showStatus("Нет связи. Попробуйте остановить ответ ещё раз."); }
  finally { stopBtn.disabled = false; }
});

newChatBtn.addEventListener("click", async () => {
  newChatBtn.disabled = true;
  try {
    const response = await fetch("/api/chat/new", {method: "POST", headers: {"X-CSRF-Token": csrf}});
    const data = await response.json();
    if (!response.ok) { showStatus(data.error || "Не удалось начать новый чат."); return; }
    retiredChats.add(currentState.chat_id);
    viewGeneration++;
    activeChat?.controller.abort(); activeChat = null;
    input.value = ""; files = []; resizeMessage(); renderFiles();
    draftQuantities.clear(); reviewDrafts.clear(); shownResults.clear();
    renderState(data); saveDrafts(); setBusy(false);
    showStatus("Начат новый чат. Товары в корзине сохранены.");
    input.focus();
  } catch { showStatus("Нет связи. Ваш текущий чат сохранён."); }
  finally { newChatBtn.disabled = !csrf; }
});

async function boot() {
  setBusy(true);
  try {
    const res = await fetch("/api/state");
    if (!res.ok) throw new Error();
    const data = await res.json();
    try {
      const saved = JSON.parse(sessionStorage.getItem("ekt-review-draft"));
      if (saved?.chat === data.chat_id) for (const [key, value] of saved.rows) reviewDrafts.set(key, value);
    } catch {}
    renderState(data);
    setBusy(false);
  } catch { showStatus("Не удалось загрузить чат. Обновите страницу."); }
}
boot();
