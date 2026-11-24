const ORDER_BENCHMARK_SOURCES = [
	["cc_index_2227b", "market.cc_index_2227b"],
	["cc_index_3128b", "market.cc_index_3128b"],
	["cc_index_2129b", "market.cc_index_2129b"],
	["zce_cotton", "market.zce_cotton"],
	["manual", "market.sudan_manual_benchmark"],
];

async function initOrderDetailPage() {
	const idText = new URLSearchParams(location.search).get("id") || "";
	const id = Number(idText);
	if (!/^[1-9]\d*$/.test(idText) || !Number.isSafeInteger(id)) {
		App.setNotice(App.t("order.not_found"));
		return;
	}
	const next = `order_detail.html?id=${id}`;
	if (!await initAuthenticatedPage(next)) return;
	document.title = `${App.t("order.detail_title")} - ${App.t("site.title")}`;
	const content = document.getElementById("order-detail-content");
	const actions = document.getElementById("order-detail-actions");
	const form = document.getElementById("order-edit-form");
	const fields = document.getElementById("order-edit-fields");
	const status = document.getElementById("order-detail-status");
	const edit = document.getElementById("order-edit");
	const documents = document.getElementById("order-documents");
	const remove = document.getElementById("order-delete");
	const update = document.getElementById("order-update");
	const cancel = document.getElementById("order-cancel-edit");
	let order;
	let pricingInputs;
	let quotationInputs;
	let benchmark;
	let delivery;

	function addFields(parent, title, values) {
		const heading = document.createElement("h2");
		heading.textContent = App.t(title);
		const list = document.createElement("dl");
		list.className = "fields";
		for (const [label, value] of values) {
			const field = document.createElement("div");
			field.className = "field";
			const term = document.createElement("dt");
			term.textContent = App.t(label);
			const description = document.createElement("dd");
			description.textContent = value === null || value === undefined || value === "" ? "—" : String(value);
			field.append(term, description);
			list.append(field);
		}
		parent.append(heading, list);
	}
	function optionLabel(options, value) {
		const option = options.find(([key]) => key === value);
		return option ? App.t(option[1]) : value;
	}
	function render() {
		const pricing = order.pricing_inputs;
		const result = order.pricing_result;
		if (!pricing || typeof pricing !== "object" || !result || typeof result !== "object" ||
			!Number.isFinite(result.max_target_cif_usd_per_ton) ||
			!Number.isFinite(result.break_even_cif_usd_per_ton) || !result.supplier_evaluation ||
			!Number.isFinite(result.supplier_evaluation.cif_usd_per_ton) ||
			!Number.isFinite(result.supplier_evaluation.profit_cny_per_ton)) {
			throw new Error(App.t("order.list_invalid"));
		}
		content.replaceChildren();
		const heading = document.createElement("p");
		heading.textContent = App.t("order.number", { id: order.id });
		content.append(heading);
		addFields(content, "order.quote_details", [
			["order.created_at", order.created_at], ["order.delivery_date", order.delivery_date],
			["order.source_quotation", order.source_quotation_id],
			...COTTON_QUOTATION_TABLES[0].columns.map((name) =>
				[`market.cotton_column.${name}`, order[name]]),
		]);
		addFields(content, "order.pricing", [
			["market.sudan_benchmark_source_label", optionLabel(ORDER_BENCHMARK_SOURCES, order.benchmark_source)],
			...ORDER_PRICING_FIELDS.map(([name, label, type]) => {
				const value = name === "supplier_quote_usd_per_ton" ? usdPerTonToUsc(pricing[name]) : pricing[name];
				return [label, type ? optionLabel(ORDER_SELECT_OPTIONS[type], value) : value];
			}),
		]);
		const formatter = new Intl.NumberFormat(document.documentElement.lang, { maximumFractionDigits: 2 });
		addFields(content, "order.snapshot_result", [
			["market.sudan_max_target_price", `${formatter.format(result.max_target_cif_usd_per_ton)} USD/t`],
			["market.sudan_break_even_price", `${formatter.format(result.break_even_cif_usd_per_ton)} USD/t`],
			["market.sudan_quote_cif", `${formatter.format(result.supplier_evaluation.cif_usd_per_ton)} USD/t`],
			["market.sudan_profit_per_ton_header", `${formatter.format(result.supplier_evaluation.profit_cny_per_ton)} CNY/t`],
		]);
		const details = document.createElement("details");
		const summary = document.createElement("summary");
		summary.textContent = App.t("order.full_result");
		const pre = document.createElement("pre");
		pre.textContent = JSON.stringify(result, null, 2);
		details.append(summary, pre);
		content.append(details);
		actions.hidden = false;
	}
	async function load() {
		const data = await App.api(`/api/orders/${id}`);
		if (!data.order) throw new Error(App.t("order.list_invalid"));
		order = data.order;
		render();
	}
	function addInput(parent, key, value, type, options) {
		const label = document.createElement("label");
		const title = document.createElement("span");
		title.textContent = App.t(key);
		const input = document.createElement(options ? "select" : "input");
		if (options) {
			for (const [optionValue, optionLabelKey] of options) {
				const option = document.createElement("option");
				option.value = optionValue;
				option.textContent = App.t(optionLabelKey);
				input.append(option);
			}
		} else {
			input.type = type || "text";
			if (type === "number") {
				input.min = "0";
				input.step = "any";
			}
		}
		input.value = value ?? "";
		label.append(title, input);
		parent.append(label);
		return input;
	}
	function editOrder() {
		fields.replaceChildren();
		pricingInputs = new Map();
		quotationInputs = new Map();
		const quoteFields = document.createElement("div");
		quoteFields.className = "fields";
		const quoteHeading = document.createElement("h2");
		quoteHeading.textContent = App.t("order.quote_details");
		fields.append(quoteHeading, quoteFields);
		delivery = addInput(quoteFields, "order.delivery_date", order.delivery_date, "date");
		delivery.required = true;
		for (const name of COTTON_QUOTATION_TABLES[0].columns) {
			const input = addInput(quoteFields, `market.cotton_column.${name}`, order[name], name === "quotation_date" ? "date" : "text");
			input.required = name === "quotation_date";
			quotationInputs.set(name, input);
		}
		const pricingHeading = document.createElement("h2");
		pricingHeading.textContent = App.t("order.pricing");
		const pricingFields = document.createElement("div");
		pricingFields.className = "fields";
		fields.append(pricingHeading, pricingFields);
		benchmark = addInput(pricingFields, "market.sudan_benchmark_source_label", order.benchmark_source, null, ORDER_BENCHMARK_SOURCES);
		for (const [name, label, type] of ORDER_PRICING_FIELDS) {
			const stored = order.pricing_inputs[name];
			const value = name === "supplier_quote_usd_per_ton" ? usdPerTonToUsc(stored) : stored;
			const input = addInput(pricingFields, label, value, type ? null : "number", type ? ORDER_SELECT_OPTIONS[type] : null);
			input.required = name !== "direct_buyer_bid_cny_per_ton";
			pricingInputs.set(name, input);
		}
		App.setNotice("");
		status.textContent = "";
		form.hidden = false;
		content.hidden = true;
		actions.hidden = true;
	}
	edit.addEventListener("click", editOrder);
	documents.addEventListener("click", () => {
		window.location.href = `order_docs.html?id=${id}`;
	});
	cancel.addEventListener("click", () => {
		form.hidden = true;
		content.hidden = false;
		actions.hidden = false;
		App.setNotice("");
	});
	remove.addEventListener("click", async () => {
		if (!window.confirm(App.t("order.confirm_delete", { id }))) return;
		remove.disabled = true;
		try {
			await App.api(`/api/orders/${id}`, { method: "DELETE" });
			window.location.href = "order.html";
		} catch (error) {
			handleUnauthorized(error, next);
			remove.disabled = false;
		}
	});
	form.addEventListener("submit", async (event) => {
		event.preventDefault();
		const payload = { benchmark_source: benchmark.value, delivery_date: delivery.value };
		for (const [name, input] of quotationInputs) payload[`quotation_${name}`] = input.value;
		for (const [name, input] of pricingInputs) {
			payload[`pricing_${name}`] = name === "supplier_quote_usd_per_ton" ? uscToUsdPerTon(input.value) : input.value;
		}
		App.setNotice("");
		update.disabled = true;
		cancel.disabled = true;
		for (const input of [...quotationInputs.values(), ...pricingInputs.values(), benchmark, delivery]) input.disabled = true;
		try {
			await App.api(`/api/orders/${id}`, {
				method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
			});
			await load();
			form.hidden = true;
			content.hidden = false;
			status.textContent = App.t("order.updated");
		} catch (error) {
			handleUnauthorized(error, next);
		} finally {
			update.disabled = false;
			cancel.disabled = false;
			for (const input of [...quotationInputs.values(), ...pricingInputs.values(), benchmark, delivery]) input.disabled = false;
		}
	});
	try {
		await load();
	} catch (error) {
		handleUnauthorized(error, next);
	}
}
