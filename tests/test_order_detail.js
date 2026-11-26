const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const frontend = path.join(__dirname, "..", "frontend");
const app = fs.readFileSync(path.join(frontend, "app.js"), "utf8");
const order = fs.readFileSync(path.join(frontend, "order.js"), "utf8");
const detail = fs.readFileSync(path.join(frontend, "order_detail.js"), "utf8");

test("order detail renders snapshots, edits fields and confirms deletion", async () => {
	function element() {
		return {
			children: [], events: {}, textContent: "", value: "", hidden: false,
			append(...items) { this.children.push(...items); },
			replaceChildren(...items) { this.children = items; },
			addEventListener(name, callback) { this.events[name] = callback; },
			focus() {},
		};
	}
	const elements = new Map();
	const document = {
		documentElement: { lang: "en" },
		addEventListener() {},
		createElement: element,
		getElementById(id) {
			if (!elements.has(id)) elements.set(id, element());
			return elements.get(id);
		},
	};
	let confirmed = false;
	const window = { location: { href: "" } };
	const saved = {
		id: 5, source_quotation_product: "cotton", created_at: "2026-11-23 01:02:03",
		delivery_date: "2026-12-10",
		quotation_date: "2026-11-23", source_quotation_id: 4, batch_number: "<script>",
		supplier_id: 3, supplier_short_name: "SUD",
		intermediary_id: 4, intermediary_short_name: "BROKER",
		benchmark_source: "manual", pricing_inputs: {
			quantity_tons: "100", supplier_quote_usd_per_ton: "1102.31",
			buyer_bid_vat_inclusive: "false",
		},
		pricing_result: {
			max_target_cif_usd_per_ton: 1200, break_even_cif_usd_per_ton: 1300,
			supplier_evaluation: { cif_usd_per_ton: 1200, profit_cny_per_ton: 400 },
			scenarios: { fx: [{ fx_cny_per_usd: 6.7 }] },
		},
	};
	const calls = [];
	let notice = "";
	const context = vm.createContext({
		document, window, location: { search: "?id=5" }, URLSearchParams, Intl,
		App: {
			async confirm() { return confirmed; },
			t(key, values = {}) { return key + (values.id || ""); },
			setNotice(message) { notice = message; },
			async api(url, options = {}) {
				if (url === "/api/suppliers") {
					return { suppliers: [
						{ id: 3, short_name: "SUD", company_name: "Supplier" },
						{ id: 4, short_name: "BROKER", company_name: "Broker" },
					] };
				}
				calls.push({ url, options });
				assert.equal(url, "/api/orders/5");
				if (options.method === "PATCH") {
					const payload = JSON.parse(options.body);
					saved.batch_number = payload.quotation_batch_number;
					saved.delivery_date = payload.delivery_date;
					return { ok: true };
				}
				if (options.method === "DELETE") return { ok: true };
				return { order: saved };
			},
		},
	});
	vm.runInContext(app, context);
	vm.runInContext(order, context);
	vm.runInContext(detail, context);
	vm.runInContext("initAuthenticatedPage = async () => true", context);
	await vm.runInContext("initOrderDetailPage()", context);
	const content = elements.get("order-detail-content");
	const quoteFields = content.children[2].children;
	const find = (fields, name) =>
		fields.find((field) => field.children[0].textContent === name).children[1];
	assert.equal(find(quoteFields, "market.cotton_column.batch_number").textContent, "<script>");
	assert.equal(find(quoteFields, "supplier.select").textContent, "SUD");
	assert.equal(find(quoteFields, "order.delivery_date").textContent, "2026-12-10");
	assert.match(content.children[7].children[1].textContent, /"fx_cny_per_usd": 6.7/);
	elements.get("order-documents").events.click();
	assert.equal(window.location.href, "order_docs.html?id=5");
	elements.get("order-edit").events.click();
	const groups = elements.get("order-edit-fields").children;
	const quoteInputs = groups[1].children;
	const pricingInputs = groups[3].children;
	assert.equal(pricingInputs.length, 3);
	assert.equal(find(quoteInputs, "order.delivery_date").required, true);
	assert.equal(find(quoteInputs, "market.cotton_column.quotation_date").required, true);
	assert.equal(find(quoteInputs, "supplier.select").value, "3");
	assert.equal(find(quoteInputs, "supplier.intermediary_select").value, "4");
	find(quoteInputs, "order.delivery_date").value = "2026-12-10";
	find(quoteInputs, "market.cotton_column.batch_number").value = "edited";
	find(pricingInputs, "order.price_required").value = "50";
	await elements.get("order-edit-form").events.submit({ preventDefault() {} });
	const payload = JSON.parse(calls.find((call) => call.options.method === "PATCH").options.body);
	assert.equal(payload.delivery_date, "2026-12-10");
	assert.equal(payload.product, "cotton");
	assert.equal(payload.supplier_id, "3");
	assert.equal(payload.intermediary_id, "4");
	assert.equal(payload.quotation_batch_number, "edited");
	assert.ok(Math.abs(Number(payload.pricing_supplier_quote_usd_per_ton) - 1102.31) < 1e-8);
	assert.equal(elements.get("order-detail-status").textContent, "order.updated");
	assert.equal(elements.get("order-edit-form").hidden, true);
	assert.equal(find(content.children[2].children, "market.cotton_column.batch_number").textContent, "edited");
	const deleteButton = elements.get("order-delete");
	await deleteButton.events.click();
	assert.equal(calls.filter((call) => call.options.method === "DELETE").length, 0);
	confirmed = true;
	await deleteButton.events.click();
	assert.equal(calls.filter((call) => call.options.method === "DELETE").length, 1);
	assert.equal(window.location.href, "order.html");
	assert.equal(notice, "");
});

test("saving an unchanged sparse order uses complete pricing defaults", async () => {
	function element() {
		return {
			children: [], events: {}, textContent: "", value: "", hidden: false,
			append(...items) { this.children.push(...items); },
			replaceChildren(...items) { this.children = items; },
			addEventListener(name, callback) { this.events[name] = callback; },
			focus() {},
		};
	}
	const elements = new Map();
	const document = {
		documentElement: { lang: "en" },
		addEventListener() {},
		createElement: element,
		getElementById(id) {
			if (!elements.has(id)) elements.set(id, element());
			return elements.get(id);
		},
	};
	const orderData = {
		id: 7, source_quotation_product: "cotton",
		created_at: "2026-11-23 01:02:03", delivery_date: "2026-12-10",
		quotation_date: "2026-11-23", batch_number: "001",
		benchmark_source: "manual",
		pricing_inputs: {
			quantity_tons: "100",
			supplier_quote_usd_per_ton: "1102.31",
		},
		supplier_id: 3,
		intermediary_id: 4,
		pricing_result: {
			max_target_cif_usd_per_ton: 1200,
			break_even_cif_usd_per_ton: 1200,
			supplier_evaluation: {
				cif_usd_per_ton: 1200,
				profit_cny_per_ton: 0,
			},
		},
	};
	const context = vm.createContext({
		document,
		window: { location: { href: "" } },
		location: { search: "?id=7" },
		URLSearchParams,
		Intl,
		App: {
			t(key) { return key; },
			setNotice() {},
			async api(url, options = {}) {
				if (url === "/api/suppliers") {
					return { suppliers: [
						{ id: 3, short_name: "SUD", company_name: "Supplier" },
						{ id: 4, short_name: "BROKER", company_name: "Broker" },
					] };
				}
				if (options.method === "PATCH") {
					context.payload = JSON.parse(options.body);
					return { ok: true };
				}
				return { order: orderData };
			},
		},
	});
	vm.runInContext(app, context);
	vm.runInContext(order, context);
	vm.runInContext(detail, context);
	vm.runInContext("initAuthenticatedPage = async () => true", context);
	await vm.runInContext("initOrderDetailPage()", context);
	elements.get("order-edit").events.click();
	await elements.get("order-edit-form").events.submit({ preventDefault() {} });
	assert.equal(context.payload.product, "cotton");
	assert.equal(context.payload.pricing_supplier_quote_basis, "CIF");
	assert.equal(context.payload.pricing_domestic_sales_vat_percent, "9");
	assert.equal(context.payload.pricing_quantity_tons, "100");
	assert.equal(
		context.payload.pricing_supplier_quote_usd_per_ton,
		"1102.31",
	);
});

test("order detail uses quotation fields for the saved product", async () => {
	function element() {
		return {
			children: [], events: {}, textContent: "", value: "", hidden: false,
			append(...items) { this.children.push(...items); },
			replaceChildren(...items) { this.children = items; },
			addEventListener(name, callback) { this.events[name] = callback; },
			focus() {},
		};
	}
	const elements = new Map();
	const document = {
		documentElement: { lang: "en" },
		addEventListener() {},
		createElement: element,
		getElementById(id) {
			if (!elements.has(id)) elements.set(id, element());
			return elements.get(id);
		},
	};
	const saved = {
		id: 6, source_quotation_product: "sesame", created_at: "2026-11-24 01:02:03",
		delivery_date: "2026-12-10", quotation_date: "2026-11-24",
		source_quotation_id: 2, batch_number: "sesame-1", oil_percent: 50,
		processor: "TUHAMA", origin: "SUDAN", benchmark_source: "manual",
		pricing_inputs: {
			quantity_tons: "100", supplier_quote_usd_per_ton: "1400",
		},
		pricing_result: {
			max_target_cif_usd_per_ton: 1400, break_even_cif_usd_per_ton: 1400,
			supplier_evaluation: { cif_usd_per_ton: 1400, profit_cny_per_ton: 0 },
		},
		supplier_id: 3,
		intermediary_id: 4,
	};
	const context = vm.createContext({
		document, window: { location: { href: "" } },
		location: { search: "?id=6" }, URLSearchParams, Intl,
		App: {
			t(key) { return key; },
			setNotice() {},
			async api(url, options = {}) {
				if (url === "/api/suppliers") {
					return { suppliers: [
						{ id: 3, short_name: "SUD", company_name: "Supplier" },
						{ id: 4, short_name: "BROKER", company_name: "Broker" },
					] };
				}
				if (options.method === "PATCH") {
					context.lastPayload = JSON.parse(options.body);
					return { ok: true };
				}
				return { order: saved };
			},
		},
	});
	vm.runInContext(app, context);
	vm.runInContext(order, context);
	vm.runInContext(detail, context);
	vm.runInContext("initAuthenticatedPage = async () => true", context);
	await vm.runInContext("initOrderDetailPage()", context);
	const quoteFields = elements.get("order-detail-content").children[2].children;
	const labels = quoteFields.map((field) => field.children[0].textContent);
	assert.ok(labels.includes("market.cotton_column.oil_percent"));
	assert.ok(labels.includes("market.cotton_column.crop_year"));
	assert.ok(!labels.includes("market.cotton_column.micronaire"));
	elements.get("order-edit").events.click();
	const quoteInputs = elements.get("order-edit-fields").children[1].children;
	const inputLabels = quoteInputs.map((field) => field.children[0].textContent);
	assert.ok(inputLabels.includes("market.cotton_column.oil_percent"));
	assert.ok(!inputLabels.includes("market.cotton_column.micronaire"));
	assert.ok(inputLabels.includes("market.cotton_column.crop_year"));
	const pricingInputs = elements.get("order-edit-fields").children[3].children;
	const find = (fields, name) =>
		fields.find((field) => field.children[0].textContent === name).children[1];
	find(quoteInputs, "market.cotton_column.quotation_date").value = "2026-11-24";
	find(pricingInputs, "order.price_required_usd").value = "1400";
	find(pricingInputs, "order.quantity_required").value = "100";
	await elements.get("order-edit-form").events.submit({ preventDefault() {} });
	assert.equal(context.lastPayload.product, "sesame");
});
