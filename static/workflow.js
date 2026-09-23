/* Source contributions stay distinct; the server aggregates Decimal quantities. */
const Workflow = {
  key(item) { return item.source_id || "manual:" + item.product_id; },
  merge(existing, incoming) {
    const values = new Map(existing.map(item => [this.key(item), item]));
    for (const item of incoming) values.set(this.key(item), item);
    return [...values.values()];
  },
  unit(value) {
    const key = String(value || "").trim().toLowerCase().replace(/\.$/, "");
    return ({метр:"м",метры:"м",метров:"м",m:"м",штука:"шт",штук:"шт",штуки:"шт",pcs:"шт",kg:"кг",килограмм:"кг",упаковка:"уп",упак:"уп"})[key] || key;
  },
  eligible(row, product, quantity) {
    return !["added", "excluded"].includes(row.completion) && product?.purchase_options?.can_add
      && Number(quantity) > 0 && (!row.source_unit || this.unit(row.source_unit) === this.unit(product.unit));
  }
};
if (typeof module !== "undefined") module.exports = Workflow;
