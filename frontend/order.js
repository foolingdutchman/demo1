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
				order.product, order.origin, order.batch_number, `${order.quantity_tons} t`,
				`${formatter.format(Number(
					supplierPriceToDisplay(
						order.supplier_price_usd_per_ton,
						order.product_type || order.source_quotation_product,
					),
				))} ${(order.product_type || order.source_quotation_product) === "cotton" ? "USC/lb" : "USD/Mt"} ${order.supplier_quote_basis}`,
			].filter(Boolean).join(" · ");
			const button = document.createElement("button");
			button.type = "button";
			button.className = "delete";
			button.textContent = App.t("order.delete");
			button.addEventListener("click", async () => {
				if (!await App.confirm(App.t("order.confirm_delete", { id: order.id }))) return;
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
	const allowedProducts = ["cotton", "sesame", "peanut", "peanut_meal"];
	const locationSearch = typeof location !== "undefined"
		? location.search
		: typeof window !== "undefined" && window.location
		? window.location.search
		: "";
	const queryParts = String(locationSearch || "").split(/[?&]/);
	const selectedProduct = allowedProducts.find(
		(product) => queryParts.includes(`product=${product}`),
	) || "";
	const authenticatedNext = selectedProduct
		? `book_order.html?product=${selectedProduct}`
		: "book_order.html";
	if (!await initAuthenticatedPage(authenticatedNext)) return;
	document.title = `${App.t("order.title")} - ${App.t("site.title")}`;
	const form = document.getElementById("order-form");
	const result = document.getElementById("order-pricing-result");
	const save = document.getElementById("order-save");
	const quoteSelect = document.getElementById("order-quotation");
	const supplierSelect = document.getElementById("order-supplier");
	const intermediarySelect = document.getElementById("order-intermediary");
	const quoteHint = document.getElementById("order-quotation-hint");
	const deliveryDate = document.getElementById("order-delivery-date");
	const productSelect = document.getElementById("order-product");
	const backLink = document.getElementById("order-back");
	const pricingInputs = new Map();
	const quotationInputs = new Map();
	let supplierPriceProduct = productSelect.value;
	let supplierPriceLabelElement = null;
	let pricingRequest = 0;
	let quotations = [];
	let quotationSheets = {};
	let suppliers = [];
	let draft;
	try {
		draft = JSON.parse(sessionStorage.getItem("orderDraft") || "{}");
		if (selectedProduct && draft.product !== selectedProduct) {
			draft = { product: selectedProduct };
		}
	} catch (error) {
		App.setNotice(App.t("order.draft_failed"));
		return;
	}
	deliveryDate.value = draft.deliveryDate || "";
	const cameFromCotton = /\/product\/cotton(?:[/?#]|$)/.test(document.referrer || "");
	productSelect.value = draft.product || (cameFromCotton ? "cotton" : "");
	backLink.addEventListener("click", (event) => {
		event.preventDefault();
		if (window.history?.length > 1) window.history.back();
		else window.location.href = "assets.html";
	});

	function addField(container, labelKey, control) {
		const label = document.createElement("label");
		const title = document.createElement("span");
		title.textContent = App.t(labelKey);
		label.append(title, control);
		container.append(label);
		return title;
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
	const visiblePricingFields = new Set([
		"supplier_quote_usd_per_ton",
		"supplier_quote_basis",
		"quantity_tons",
	]);
	for (const [name, label, type] of ORDER_PRICING_FIELDS) {
		const input = type ? makeSelect(ORDER_SELECT_OPTIONS[type]) : document.createElement("input");
		if (!type) {
			input.type = "number";
			input.min = "0";
			input.step = "any";
			input.required = name !== "direct_buyer_bid_cny_per_ton";
		}
		const value = draft.pricing?.[name] ?? SUDAN_COTTON_DEFAULTS[name];
		input.value = name === "supplier_quote_usd_per_ton"
			? supplierPriceToDisplay(value, productSelect.value)
			: value;
		pricingInputs.set(name, input);
		if (visiblePricingFields.has(name)) {
			const fieldLabel = addField(
				document.getElementById("order-pricing-fields"),
				name === "supplier_quote_usd_per_ton"
					? supplierPriceLabel(productSelect.value)
					: label,
				input,
			);
			if (name === "supplier_quote_usd_per_ton") {
				supplierPriceLabelElement = fieldLabel;
			}
		}
	}
	function productConfig() {
		const key = productSelect.value === "cotton"
			? "quotation_summary"
			: productSelect.value;
		return PRODUCT_QUOTATION_TABLES.find((item) => item.key === key);
	}
	function renderQuotationFields() {
		const container = document.getElementById("order-quotation-fields");
		const config = productConfig();
		quotationInputs.clear();
		container.replaceChildren();
		if (!config) return;
		for (const name of config.columns) {
			const input = document.createElement("input");
			input.type = name === "quotation_date" ? "date" : "text";
			input.required = name === "quotation_date";
			input.value = draft.quotation?.[name] ?? "";
			quotationInputs.set(name, input);
			addField(container, `market.cotton_column.${name}`, input);
			input.addEventListener("input", rememberDraft);
		}
		const productInput = quotationInputs.get("product");
		const netWeightInput = quotationInputs.get("net_weight_tons");
		if (productInput) productInput.readOnly = true;
		if (netWeightInput) netWeightInput.readOnly = true;
		syncProductName();
	}
	function syncProductName() {
		const option = [...productSelect.children].find(
			(item) => item.value === productSelect.value,
		);
		const productInput = quotationInputs.get("product");
		if (productInput) productInput.value = option?.textContent || "";
	}
	renderQuotationFields();
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
		pricing.supplier_quote_usd_per_ton = supplierPriceToUsdPerTon(
			pricing.supplier_quote_usd_per_ton,
			productSelect.value,
		);
		return {
			product: productSelect.value,
			pricing,
			quotation: Object.fromEntries([...quotationInputs].map(([key, input]) => [key, input.value])),
			benchmarkSource: source.value,
			supplierId: supplierSelect.value,
			intermediaryId: intermediarySelect.value,
			quotationId: quoteSelect.value,
			deliveryDate: deliveryDate.value,
		};
	}
	async function ensureCottonFx() {
		const fxInput = pricingInputs.get("fx_cny_per_usd");
		if (Number(fxInput.value) > 0) return Number(fxInput.value);
		const result = await App.api("/api/market-prices");
		const fx = result && result.prices && Number(result.prices.usd_cny);
		if (!Number.isFinite(fx) || fx <= 0) return null;
		fxInput.value = String(fx);
		return fx;
	}
	async function syncSupplierQuotePrice(selected) {
		const input = pricingInputs.get("supplier_quote_usd_per_ton");
		if (!selected) {
			input.value = "";
			return;
		}
		if (productSelect.value !== "cotton") {
			const price = Number(selected.price_usc_per_mt);
			if (Number.isFinite(price) && price >= 0) input.value = String(price);
			return;
		}
		const quoteCnyPerTon = Number(selected.gross_price_cny_per_ton);
		if (!Number.isFinite(quoteCnyPerTon) || quoteCnyPerTon <= 0) return;
		try {
			const existingFx = Number(pricingInputs.get("fx_cny_per_usd").value);
			const fx = existingFx > 0 ? existingFx : await ensureCottonFx();
			if (!fx) return;
			input.value = supplierPriceToDisplay(
				quoteCnyPerTon / fx,
				"cotton",
			);
			rememberDraft();
		} catch (error) {
			App.setNotice(error.message || App.t("market.unavailable"));
		}
	}
	function syncQuote(
		selected,
		clearManual = false,
		resetQuantity = false,
		preserveDate = false,
		preserveSupplierPrice = false,
	) {
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
		const netWeight = quotationInputs.get("net_weight_tons");
		if (!selected && netWeight) netWeight.value = quantity.value;
		quoteHint.textContent = App.t(selected ? (weight ? "order.quote_weight_locked" : "order.quote_weight_missing") : "order.manual_weight");
		++pricingRequest;
		result.textContent = "";
		if (!preserveSupplierPrice) syncSupplierQuotePrice(selected);
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
	function renderIntermediaryOptions(selectedValue = "") {
		intermediarySelect.replaceChildren();
		const empty = document.createElement("option");
		empty.value = "";
		empty.textContent = App.t("supplier.intermediary_optional");
		intermediarySelect.append(empty);
		for (const supplier of suppliers) {
			if (String(supplier.id) === supplierSelect.value) continue;
			const option = document.createElement("option");
			option.value = String(supplier.id);
			option.textContent = `${supplier.short_name} · ${supplier.company_name}`;
			intermediarySelect.append(option);
		}
		const create = document.createElement("option");
		create.value = "__new__";
		create.textContent = App.t("supplier.create_option");
		intermediarySelect.append(create);
		intermediarySelect.value = [...intermediarySelect.children].some(
			(option) => option.value === selectedValue,
		) ? selectedValue : "";
	}
	for (const [name, input] of pricingInputs) {
		input.addEventListener("input", () => {
			if (name === "domestic_benchmark_cny_per_ton") source.value = "manual";
			if (name === "quantity_tons" && !quoteSelect.value) {
				const netWeight = quotationInputs.get("net_weight_tons");
				if (netWeight) netWeight.value = input.value;
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
	supplierSelect.addEventListener("change", () => {
		App.setNotice("");
		renderIntermediaryOptions(intermediarySelect.value);
		rememberDraft();
		if (supplierSelect.value === "__new__") {
			window.location.href = "supplier.html?return=book_order.html";
		}
	});
	intermediarySelect.addEventListener("change", () => {
		App.setNotice("");
		rememberDraft();
		if (intermediarySelect.value === "__new__") {
			window.location.href = "supplier.html?return=book_order.html&role=intermediary";
		}
	});
	deliveryDate.addEventListener("input", rememberDraft);
	async function renderQuotationOptions(preserveDraft = false) {
		const config = productConfig();
		quoteSelect.replaceChildren();
		quotations = config ? quotationSheets[config.key] || [] : [];
		const manual = document.createElement("option");
		manual.value = "";
		manual.textContent = App.t("order.manual_quote");
		quoteSelect.append(manual);
		for (const quote of quotations) {
			const option = document.createElement("option");
			option.value = String(quote.row_number);
			option.disabled = Boolean(quote.ordered);
			option.textContent = [
				quote.quotation_date, quote.batch_number, quote.processor, quote.origin,
			].filter(Boolean).join(" · ") || `#${quote.row_number}`;
			if (quote.ordered) option.textContent += ` · ${App.t("quotation.ordered")}`;
			quoteSelect.append(option);
		}
		quoteSelect.value = "";
		save.disabled = false;
		syncQuote(null, !preserveDraft, true, false, preserveDraft);
	}
	productSelect.addEventListener("change", () => {
		const supplierPrice = pricingInputs.get("supplier_quote_usd_per_ton");
		const storedPrice = supplierPriceToUsdPerTon(
			supplierPrice.value,
			supplierPriceProduct,
		);
		renderQuotationFields();
		supplierPrice.value = supplierPriceToDisplay(storedPrice, productSelect.value);
		if (supplierPriceLabelElement) {
			supplierPriceLabelElement.textContent = App.t(
				supplierPriceLabel(productSelect.value),
			);
		}
		supplierPriceProduct = productSelect.value;
		renderQuotationOptions();
		rememberDraft();
	});
	quoteSelect.addEventListener("change", () => {
		const selected = quotations.find((row) => String(row.row_number) === quoteSelect.value);
		syncQuote(selected, !selected, true);
		const netWeight = quotationInputs.get("net_weight_tons");
		if (netWeight) netWeight.readOnly = true;
		save.disabled = Boolean(selected && selected.ordered);
		App.setNotice("");
		rememberDraft();
	});

	try {
		const data = await App.api("/api/suppliers");
		if (!Array.isArray(data.suppliers)) throw new Error(App.t("supplier.list_invalid"));
		suppliers = data.suppliers;
		const createOption = [...supplierSelect.children].find(
			(option) => option.value === "__new__",
		);
		for (const supplier of suppliers) {
			const option = document.createElement("option");
			option.value = String(supplier.id);
			option.textContent = `${supplier.short_name} · ${supplier.company_name}`;
			if (createOption) supplierSelect.insertBefore(option, createOption);
			else supplierSelect.append(option);
		}
		const draftSupplierId = String(draft.supplierId || "");
		supplierSelect.value = suppliers.some(
			(supplier) => String(supplier.id) === draftSupplierId,
		) ? draftSupplierId : "";
		const draftIntermediaryId = String(draft.intermediaryId || "");
		renderIntermediaryOptions(draftIntermediaryId);
	} catch (error) {
		if (handleUnauthorized(error, authenticatedNext)) return;
	}

	if (!draft.quotationId) syncQuote(null);
	const initialNetWeight = quotationInputs.get("net_weight_tons");
	if (initialNetWeight) initialNetWeight.readOnly = true;
	try {
		const data = await App.api("/api/cotton-quotations");
		if (!data.sheets) throw new Error(App.t("market.cotton_data_unavailable"));
		quotationSheets = data.sheets;
		await renderQuotationOptions(true);
		const selected = quotations.find((row) => String(row.row_number) === draft.quotationId);
		if (draft.quotationId && (!selected || selected.ordered)) {
			save.disabled = true;
			throw new Error(App.t(selected ? "order.already_ordered" : "order.quotation_missing"));
		}
		if (pendingQuote) pendingQuote.remove();
		quoteSelect.value = draft.quotationId || "";
		syncQuote(selected, false, false, true, true);
		const netWeight = quotationInputs.get("net_weight_tons");
		if (netWeight) netWeight.readOnly = true;
	} catch (error) {
		if (handleUnauthorized(error, authenticatedNext)) return;
		if (draft.quotationId) save.disabled = true;
	}

	form.addEventListener("submit", async (event) => {
		event.preventDefault();
		if (!rememberDraft()) return;
		const data = currentDraft();
		if (!data.product) {
			App.setNotice(App.t("order.product_required"));
			productSelect.focus();
			return;
		}
		if (!data.supplierId || data.supplierId === "__new__") {
			App.setNotice(App.t("supplier.required"));
			supplierSelect.focus();
			return;
		}
		if (
			data.intermediaryId
			&& data.intermediaryId !== "__new__"
			&& data.intermediaryId === data.supplierId
		) {
			App.setNotice(App.t("supplier.same_party"));
			intermediarySelect.focus();
			return;
		}
		const fields = {
			product: data.product,
			benchmark_source: data.benchmarkSource,
			supplier_id: data.supplierId,
			intermediary_id: data.intermediaryId === "__new__"
				? ""
				: data.intermediaryId,
			quotation_id: data.quotationId,
			delivery_date: data.deliveryDate,
		};
		for (const [key, value] of Object.entries(data.pricing)) fields[`pricing_${key}`] = value;
		for (const [key, value] of Object.entries(data.quotation)) fields[`quotation_${key}`] = value;
		App.setNotice("");
		save.disabled = true;
		source.disabled = true;
		deliveryDate.disabled = true;
		supplierSelect.disabled = true;
		intermediarySelect.disabled = true;
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
			handleUnauthorized(error, authenticatedNext);
			save.disabled = false;
			source.disabled = false;
			deliveryDate.disabled = false;
			supplierSelect.disabled = false;
			intermediarySelect.disabled = false;
			quoteSelect.disabled = false;
			for (const input of [...pricingInputs.values(), ...quotationInputs.values()]) input.disabled = false;
		}
	});
}
