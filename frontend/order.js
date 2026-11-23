const ORDER_PRICING_FIELDS = [
	["direct_buyer_bid_cny_per_ton", "market.sudan_buyer_bid_label"],
	["buyer_bid_vat_inclusive", "market.sudan_buyer_bid_vat_label", "boolean"],
	["domestic_benchmark_cny_per_ton", "market.sudan_benchmark_label"],
	["benchmark_vat_inclusive", "market.sudan_benchmark_vat_label", "boolean"],
	["quality_discount_cny_per_ton", "market.sudan_quality_discount_label"],
	["quality_discount_basis", "market.sudan_quality_basis_label", "quality"],
	["domestic_sales_vat_percent", "market.sudan_sales_vat_label"],
	["fx_cny_per_usd", "market.sudan_fx_label"],
	["duty_percent", "market.sudan_duty_label"],
	["import_vat_percent", "market.sudan_import_vat_label"],
	["import_vat_recoverable_percent", "market.sudan_recoverable_vat_label"],
	["quota_cost_cny_per_ton", "market.sudan_quota_cost_label"],
	["quota_recoverable_vat_cny_per_ton", "market.sudan_quota_vat_label"],
	["other_nonrecoverable_cost_cny_per_ton", "market.sudan_other_cost_label"],
	["other_recoverable_vat_cny_per_ton", "market.sudan_other_vat_label"],
	["target_profit_cny_per_ton", "market.sudan_target_profit_label"],
	["supplier_quote_usd_per_ton", "order.price_required"],
	["supplier_quote_basis", "market.sudan_quote_basis_label", "basis"],
	["ocean_freight_usd_per_ton", "market.sudan_freight_label"],
	["insurance_usd_per_ton", "market.sudan_insurance_label"],
	["other_cif_cost_usd_per_ton", "market.sudan_other_cif_cost_label"],
	["quantity_tons", "order.quantity_required"],
];
const ORDER_SELECT_OPTIONS = {
	boolean: [["true", "market.sudan_yes"], ["false", "market.sudan_no"]],
	quality: [["gross", "market.sudan_gross"], ["net", "market.sudan_net"]],
	basis: [["CIF", "market.sudan_cif_basis"], ["FOB", "market.sudan_fob_basis"]],
};

async function initOrdersPage() {
	if (!await initAuthenticatedPage("order.html")) return;
	document.title = `${App.t("order.list_title")} - ${App.t("site.title")}`;
	const container = document.getElementById("orders-list");
	try {
		const data = await App.api("/api/orders");
		if (!Array.isArray(data.orders)) throw new Error(App.t("order.list_invalid"));
		if (!data.orders.length) {
			const empty = document.createElement("p");
			empty.className = "empty";
			empty.textContent = App.t("order.list_empty");
			container.append(empty);
			return;
		}
		const formatter = new Intl.NumberFormat(document.documentElement.lang, { maximumFractionDigits: 2 });
		for (const order of data.orders) {
			const row = document.createElement("div");
			row.className = "order-row";
			const link = document.createElement("a");
			link.href = `order_detail.html?id=${encodeURIComponent(order.id)}`;
			link.textContent = [
				App.t("order.number", { id: order.id }), order.created_at,
				order.batch_number, `${order.quantity_tons} t`,
				`${formatter.format(Number(order.supplier_price_usd_per_ton) / USD_PER_TON_PER_USC_LB)} USC/lb ${order.supplier_quote_basis}`,
			].filter(Boolean).join(" · ");
			const button = document.createElement("button");
			button.type = "button";
			button.className = "delete";
			button.textContent = App.t("order.delete");
			button.addEventListener("click", async () => {
				if (!window.confirm(App.t("order.confirm_delete", { id: order.id }))) return;
				button.disabled = true;
				App.setNotice("");
				try {
					await App.api(`/api/orders/${order.id}`, { method: "DELETE" });
					row.remove();
					if (!container.children.length) {
						const empty = document.createElement("p");
						empty.className = "empty";
						empty.textContent = App.t("order.list_empty");
						container.append(empty);
					}
				} catch (error) {
					handleUnauthorized(error, "order.html");
					button.disabled = false;
				}
			});
			row.append(link, button);
			container.append(row);
		}
	} catch (error) {
		container.replaceChildren();
		handleUnauthorized(error, "order.html");
	}
}

async function initBookOrderPage() {
	if (!await initAuthenticatedPage("book_order.html")) return;
	document.title = `${App.t("order.title")} - ${App.t("site.title")}`;
	const form = document.getElementById("order-form");
	const result = document.getElementById("order-pricing-result");
	const save = document.getElementById("order-save");
	const recalculate = document.getElementById("order-recalculate");
	const quoteSelect = document.getElementById("order-quotation");
	const quoteHint = document.getElementById("order-quotation-hint");
	const deliveryDate = document.getElementById("order-delivery-date");
	const pricingInputs = new Map();
	const quotationInputs = new Map();
	let pricingRequest = 0;
	let quotations = [];
	let draft;
	try {
		draft = JSON.parse(sessionStorage.getItem("orderDraft") || "{}");
	} catch (error) {
		App.setNotice(App.t("order.draft_failed"));
		return;
	}
	deliveryDate.value = draft.deliveryDate || "";

	function addField(container, labelKey, control) {
		const label = document.createElement("label");
		const title = document.createElement("span");
		title.textContent = App.t(labelKey);
		label.append(title, control);
		container.append(label);
	}
	function makeSelect(options) {
		const control = document.createElement("select");
		for (const [value, key] of options) {
			const option = document.createElement("option");
			option.value = value;
			option.textContent = App.t(key);
			control.append(option);
		}
		return control;
	}
	const source = makeSelect([
		["cc_index_2227b", "market.cc_index_2227b"],
		["cc_index_3128b", "market.cc_index_3128b"],
		["cc_index_2129b", "market.cc_index_2129b"],
		["zce_cotton", "market.zce_cotton"],
		["manual", "market.sudan_manual_benchmark"],
	]);
	source.value = draft.benchmarkSource || "manual";
	addField(document.getElementById("order-pricing-fields"), "market.sudan_benchmark_source_label", source);
	for (const [name, label, type] of ORDER_PRICING_FIELDS) {
		const input = type ? makeSelect(ORDER_SELECT_OPTIONS[type]) : document.createElement("input");
		if (!type) {
			input.type = "number";
			input.min = "0";
			input.step = "any";
			input.required = name !== "direct_buyer_bid_cny_per_ton";
		}
		const value = draft.pricing?.[name] ?? SUDAN_COTTON_DEFAULTS[name];
		input.value = name === "supplier_quote_usd_per_ton" ? usdPerTonToUsc(value) : value;
		pricingInputs.set(name, input);
		addField(document.getElementById("order-pricing-fields"), label, input);
	}
	for (const name of COTTON_QUOTATION_TABLES[0].columns) {
		const input = document.createElement("input");
		input.type = name === "quotation_date" ? "date" : "text";
		input.required = name === "quotation_date";
		input.value = draft.quotation?.[name] ?? "";
		quotationInputs.set(name, input);
		addField(document.getElementById("order-quotation-fields"), `market.cotton_column.${name}`, input);
	}
	const manual = document.createElement("option");
	manual.value = "";
	manual.textContent = App.t("order.manual_quote");
	quoteSelect.append(manual);
	let pendingQuote = null;
	if (draft.quotationId) {
		pendingQuote = document.createElement("option");
		pendingQuote.value = draft.quotationId;
		pendingQuote.textContent = App.t("order.quotation_missing");
		quoteSelect.append(pendingQuote);
		quoteSelect.value = draft.quotationId;
	}

	function currentDraft() {
		const pricing = Object.fromEntries([...pricingInputs].map(([key, input]) => [key, input.value]));
		pricing.supplier_quote_usd_per_ton = uscToUsdPerTon(pricing.supplier_quote_usd_per_ton);
		return {
			pricing,
			quotation: Object.fromEntries([...quotationInputs].map(([key, input]) => [key, input.value])),
			benchmarkSource: source.value,
			quotationId: quoteSelect.value,
			deliveryDate: deliveryDate.value,
		};
	}
	function syncQuote(selected, clearManual = false, resetQuantity = false, preserveDate = false) {
		const quantity = pricingInputs.get("quantity_tons");
		for (const [name, input] of quotationInputs) {
			input.readOnly = Boolean(selected) && !(name === "quotation_date" && !selected.quotation_date);
			if (selected && !(preserveDate && name === "quotation_date" && !selected.quotation_date)) input.value = selected[name] ?? "";
			else if (clearManual) input.value = "";
		}
		const weight = selected && [selected.net_weight_tons, selected.standard_weight_tons]
			.find((value) => typeof value === "number" && Number.isFinite(value) && value > 0);
		quantity.readOnly = Boolean(weight);
		if (weight) quantity.value = String(weight);
		else if (selected && resetQuantity) quantity.value = "";
		if (!selected) quotationInputs.get("net_weight_tons").value = quantity.value;
		quoteHint.textContent = App.t(selected ? (weight ? "order.quote_weight_locked" : "order.quote_weight_missing") : "order.manual_weight");
		++pricingRequest;
		result.textContent = "";
	}
	function rememberDraft() {
		try {
			sessionStorage.setItem("orderDraft", JSON.stringify(currentDraft()));
			return true;
		} catch (error) {
			App.setNotice(App.t("order.draft_failed"));
			return false;
		}
	}
	for (const [name, input] of pricingInputs) {
		input.addEventListener("input", () => {
			if (name === "domestic_benchmark_cny_per_ton") source.value = "manual";
			if (name === "quantity_tons" && !quoteSelect.value) {
				quotationInputs.get("net_weight_tons").value = input.value;
			}
			++pricingRequest;
			result.textContent = "";
			rememberDraft();
		});
		input.addEventListener("change", () => {
			++pricingRequest;
			result.textContent = "";
			rememberDraft();
		});
	}
	source.addEventListener("change", rememberDraft);
	deliveryDate.addEventListener("input", rememberDraft);
	quotationInputs.get("net_weight_tons").readOnly = true;
	for (const input of quotationInputs.values()) input.addEventListener("input", rememberDraft);
	quoteSelect.addEventListener("change", () => {
		const selected = quotations.find((row) => String(row.row_number) === quoteSelect.value);
		syncQuote(selected, !selected, true);
		quotationInputs.get("net_weight_tons").readOnly = true;
		save.disabled = Boolean(selected && selected.ordered);
		App.setNotice("");
		rememberDraft();
	});

	if (!draft.quotationId) syncQuote(null);
	quotationInputs.get("net_weight_tons").readOnly = true;
	try {
		const data = await App.api("/api/cotton-quotations");
		if (!Array.isArray(data.sheets?.quotation_summary)) throw new Error(App.t("market.cotton_data_unavailable"));
		quotations = data.sheets.quotation_summary;
		for (const quote of quotations) {
			const option = document.createElement("option");
			option.value = String(quote.row_number);
			option.disabled = Boolean(quote.ordered);
			option.textContent = [quote.quotation_date, quote.batch_number, quote.processor, quote.origin]
				.filter(Boolean).join(" · ") || `#${quote.row_number}`;
			if (quote.ordered) option.textContent += ` · ${App.t("quotation.ordered")}`;
			quoteSelect.append(option);
		}
		const selected = quotations.find((row) => String(row.row_number) === draft.quotationId);
		if (draft.quotationId && (!selected || selected.ordered)) {
			save.disabled = true;
			throw new Error(App.t(selected ? "order.already_ordered" : "order.quotation_missing"));
		}
		if (pendingQuote) pendingQuote.remove();
		quoteSelect.value = draft.quotationId || "";
		syncQuote(selected, false, false, true);
		quotationInputs.get("net_weight_tons").readOnly = true;
	} catch (error) {
		if (handleUnauthorized(error, "book_order.html")) return;
		if (draft.quotationId) save.disabled = true;
	}

	recalculate.addEventListener("click", async () => {
		const requestId = ++pricingRequest;
		App.setNotice("");
		result.textContent = "";
		try {
			const response = await App.api("/api/sudan-cotton-pricing", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify(currentDraft().pricing),
			});
			if (requestId !== pricingRequest) return;
			const formatter = new Intl.NumberFormat(document.documentElement.lang, { maximumFractionDigits: 2 });
			result.textContent = App.t("order.calculated", {
				target: formatter.format(response.max_target_cif_usd_per_ton),
				break_even: formatter.format(response.break_even_cif_usd_per_ton),
			});
			if (response.supplier_evaluation) {
				result.textContent += ` ${App.t("order.quote_calculated", {
					cif: formatter.format(response.supplier_evaluation.cif_usd_per_ton),
					profit: formatter.format(response.supplier_evaluation.profit_cny_per_ton),
				})}`;
			}
		} catch (error) {
			if (requestId === pricingRequest) handleUnauthorized(error, "book_order.html");
		}
	});
	form.addEventListener("submit", async (event) => {
		event.preventDefault();
		if (!rememberDraft()) return;
		const data = currentDraft();
		const fields = { benchmark_source: data.benchmarkSource, quotation_id: data.quotationId, delivery_date: data.deliveryDate };
		for (const [key, value] of Object.entries(data.pricing)) fields[`pricing_${key}`] = value;
		for (const [key, value] of Object.entries(data.quotation)) fields[`quotation_${key}`] = value;
		App.setNotice("");
		save.disabled = true;
		recalculate.disabled = true;
		source.disabled = true;
		deliveryDate.disabled = true;
		quoteSelect.disabled = true;
		for (const input of [...pricingInputs.values(), ...quotationInputs.values()]) input.disabled = true;
		try {
			const response = await App.api("/api/orders", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify(fields),
			});
			try {
				sessionStorage.removeItem("orderDraft");
			} catch (error) {
				App.setNotice(App.t("order.draft_failed"));
				form.hidden = true;
				const link = document.createElement("a");
				link.href = `order_detail.html?id=${encodeURIComponent(response.order.id)}`;
				link.textContent = App.t("order.view_created", { id: response.order.id });
				document.getElementById("order-status").append(link);
				return;
			}
			window.location.href = `order_detail.html?id=${encodeURIComponent(response.order.id)}`;
		} catch (error) {
			handleUnauthorized(error, "book_order.html");
			save.disabled = false;
			recalculate.disabled = false;
			source.disabled = false;
			deliveryDate.disabled = false;
			quoteSelect.disabled = false;
			for (const input of [...pricingInputs.values(), ...quotationInputs.values()]) input.disabled = false;
		}
	});
}
