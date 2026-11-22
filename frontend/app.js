document.addEventListener("DOMContentLoaded", async () => {
	try {
		await App.init();
		const page = document.body.dataset.page;
		if (page === "public") {
			initMarketBoard();
		} else if (page === "market-detail") {
			await initMarketDetailPage();
		} else if (page === "login") {
			await initLoginPage();
		} else if (page === "assets") {
			await initAssetsPage();
		} else if (page === "list") {
			await initListPage();
		} else if (page === "detail") {
			await initDetailPage();
		}
	} catch (error) {
		App.setNotice(error.message);
	}
});

let importCottonMarketPrices = null;
let sudanPricingRequestId = 0;
let sudanPricingTimer = null;
const IMPORT_COTTON_DEFAULTS = {
	customsCost: 100,
	clearanceFee: 1.5,
	tariffAmountCny: 3800,
};
const SUDAN_COTTON_DEFAULTS = {
	direct_buyer_bid_cny_per_ton: "",
	buyer_bid_vat_inclusive: "true",
	domestic_benchmark_cny_per_ton: "",
	benchmark_vat_inclusive: "true",
	quality_discount_cny_per_ton: "500",
	quality_discount_basis: "gross",
	domestic_sales_vat_percent: "9",
	fx_cny_per_usd: "",
	duty_percent: "0",
	import_vat_percent: "9",
	import_vat_recoverable_percent: "100",
	quota_cost_cny_per_ton: "4200",
	quota_recoverable_vat_cny_per_ton: "0",
	other_nonrecoverable_cost_cny_per_ton: "500",
	other_recoverable_vat_cny_per_ton: "0",
	target_profit_cny_per_ton: "300",
	supplier_quote_usd_per_ton: "",
	supplier_quote_basis: "CIF",
	ocean_freight_usd_per_ton: "0",
	insurance_usd_per_ton: "0",
	other_cif_cost_usd_per_ton: "0",
	quantity_tons: "",
};
const COTTON_QUOTATION_TABLES = [
	{
		key: "quotation_summary",
		title: "market.cotton_summary",
		columns: [
			"image_label",
			"batch_number",
			"processor",
			"origin",
			"package_count",
			"gross_weight_tons",
			"tare_weight_kg",
			"net_weight_tons",
			"standard_weight_tons",
			"moisture_percent",
			"impurity_percent",
			"micronaire",
			"length_mm",
			"uniformity_percent",
			"color_grade",
			"strength_average",
			"strength_min",
			"strength_max",
			"gross_price_cny_per_ton",
			"basis_cny_per_ton",
			"red_note",
			"source_filename",
			"source_sheet_name",
		],
	},
];

async function initMarketBoard() {
	const refreshButton = document.getElementById("market-refresh");
	refreshButton.addEventListener("click", loadMarketPrices);
	await loadMarketPrices();
}

async function initMarketDetailPage() {
	const query = new URLSearchParams(location.search);
	const asset = query.get("asset");
	const assets = [
		"gold",
		"btc",
		"eth",
		"ice_cotton",
		"zce_cotton",
		"cc_index_3128b",
		"cc_index_2227b",
		"cc_index_2129b",
		"usd_cny",
	];
	const returnPage = query.get("return") === "assets" ? "assets.html" : "index.html";
	document.getElementById("market-detail-back").href = returnPage;
	document.getElementById("market-detail-brand").href = "assets.html";
	if (!assets.includes(asset)) {
		document.getElementById("market-history-latest").textContent = App.t(
			"market.unavailable_short",
		);
		document.getElementById("market-detail-status").textContent = App.t(
			"market.invalid_asset",
		);
		return;
	}

	const assetName = App.t(`market.${asset}`);
	document.title = `${assetName} - ${App.t("market.title")}`;
	document.getElementById("market-detail-title").textContent = assetName;
	const unitKey = asset.startsWith("cc_index_")
		? "market.cc_index_unit"
		: asset === "btc" || asset === "eth"
		? "market.crypto_unit"
		: `market.${asset}_unit`;
	document.getElementById("market-detail-unit").textContent = App.t(
		unitKey,
	);
	document.getElementById("market-history-source").textContent = App.t(
		asset.startsWith("cc_index_")
			? "market.history_source.cc_index"
			: `market.history_source.${asset}`,
	);
	if (asset.startsWith("cc_index_")) {
		document.getElementById("market-history-summary").hidden = true;
		document.getElementById("market-history-panel").hidden = true;
		const note = document.getElementById("market-current-history-note");
		note.hidden = false;
		note.textContent = App.t("market.cc_index_history_note");
	}
	if (asset === "zce_cotton") {
		document.getElementById("market-import-cotton-panel").hidden = false;
		initSudanCottonPricing();
		loadCottonQuotations();
		document.getElementById("market-import-customs-cost").value =
			String(IMPORT_COTTON_DEFAULTS.customsCost);
		document.getElementById("market-import-clearance-fee").value =
			String(IMPORT_COTTON_DEFAULTS.clearanceFee);
		document.getElementById("market-import-tariff-amount").value =
			String(IMPORT_COTTON_DEFAULTS.tariffAmountCny);
		for (const id of [
			"market-import-customs-cost",
			"market-import-clearance-fee",
			"market-import-tariff-amount",
		]) {
			document.getElementById(id).addEventListener("input", updateImportCottonPrice);
		}
	}
	loadMarketDetailCurrentPrice(asset);
	if (asset.startsWith("cc_index_")) return;
	try {
		const result = await App.api(
			`/api/market-history?asset=${encodeURIComponent(asset)}`,
		);
		if (!Array.isArray(result.prices) || result.prices.length === 0) {
			throw new Error(App.t("market.history_unavailable"));
		}
		const latest = result.prices[result.prices.length - 1];
		const digits = asset.startsWith("cc_index_")
			? 0
			: asset === "usd_cny" ? 4 : 2;
		const formatter = new Intl.NumberFormat(document.documentElement.lang, {
			minimumFractionDigits: digits,
			maximumFractionDigits: digits,
		});
		document.getElementById("market-history-latest").textContent =
			`${result.currency} ${formatter.format(latest.price)}`;
		document.getElementById("market-history-date").textContent = App.t(
			"market.history_as_of",
			{ date: latest.date },
		);
		document.getElementById("market-detail-updated").textContent = App.t(
			"market.updated",
			{
				time: new Intl.DateTimeFormat(document.documentElement.lang, {
					dateStyle: "short",
					timeStyle: "medium",
				}).format(new Date(result.updated_at)),
			},
		);
		renderMarketCandlestickChart(result.prices, result.currency, formatter, assetName);
	} catch (error) {
		document.getElementById("market-history-latest").textContent = App.t(
			"market.unavailable_short",
		);
		document.getElementById("market-detail-status").textContent = error.message;
	}
}

async function loadCottonQuotations() {
	const status = document.getElementById("cotton-quotation-status");
	const container = document.getElementById("cotton-quotation-tables");
	container.replaceChildren();
	status.textContent = "";
	try {
		const session = await App.api("/api/session");
		if (!session.authenticated) return;
		document.getElementById("cotton-quotation-panel").hidden = false;
		const result = await App.api("/api/cotton-quotations");
		if (!result.sheets || COTTON_QUOTATION_TABLES.some(
			({ key }) => !Array.isArray(result.sheets[key]),
		)) {
			throw new Error(App.t("market.cotton_data_unavailable"));
		}
		const formatter = new Intl.NumberFormat(document.documentElement.lang, {
			maximumFractionDigits: 3,
		});
		let hasRecords = false;
		for (const sheet of COTTON_QUOTATION_TABLES) {
			const records = result.sheets[sheet.key];
			if (records.length === 0) continue;
			hasRecords = true;
			const heading = document.createElement("h3");
			heading.textContent = App.t(sheet.title);
			container.append(heading);
			const headers = sheet.columns.map((column) =>
				App.t(`market.cotton_column.${column}`),
			);
			const rows = records.map((record) => sheet.columns.map((column) => {
				const value = record[column];
				return typeof value === "number" ? formatter.format(value) : value ?? "";
			}));
			appendTable(container, headers, rows);
		}
		if (!hasRecords) {
			status.textContent = App.t("market.cotton_data_empty");
		}
	} catch (error) {
		document.getElementById("cotton-quotation-panel").hidden = false;
		status.textContent = error.message || App.t("market.cotton_data_unavailable");
	}
}

async function loadMarketDetailCurrentPrice(asset) {
	const value = document.getElementById("market-current-price");
	const status = document.getElementById("market-current-status");
	try {
		const result = await App.api("/api/market-prices");
		const price = result.prices && result.prices[asset];
		if (!Number.isFinite(price)) {
			throw new Error(App.t("market.unavailable"));
		}
		const currency = result.asset_currencies && result.asset_currencies[asset]
			? result.asset_currencies[asset]
			: result.currency;
		const digits = asset.startsWith("cc_index_")
			? 0
			: asset === "usd_cny" ? 4 : 2;
		const formatter = new Intl.NumberFormat(document.documentElement.lang, {
			minimumFractionDigits: digits,
			maximumFractionDigits: digits,
		});
		value.textContent = `${currency} ${formatter.format(price)}`;
		document.getElementById("market-current-updated").textContent = App.t(
			result.asset_dates && result.asset_dates[asset]
				? "market.history_as_of"
				: "market.updated",
			result.asset_dates && result.asset_dates[asset]
				? { date: result.asset_dates[asset] }
				: {
					time: new Intl.DateTimeFormat(document.documentElement.lang, {
						dateStyle: "short",
						timeStyle: "medium",
					}).format(new Date(result.updated_at)),
				},
		);
		if (asset === "zce_cotton") {
			importCottonMarketPrices = result.prices;
			updateImportCottonPrice();
			syncSudanBenchmark();
			const fx = document.querySelector(
				'[data-sudan-field="fx_cny_per_usd"]',
			);
			fx.value = Number.isFinite(result.prices.usd_cny)
				? String(result.prices.usd_cny)
				: "";
			queueSudanCottonPricing();
		}
	} catch (error) {
		value.textContent = App.t("market.unavailable_short");
		status.textContent = error.message || App.t("market.unavailable");
		if (asset === "zce_cotton") {
			document.getElementById("market-import-cotton-price").textContent =
				App.t("market.unavailable_short");
			document.getElementById("market-import-cotton-status").textContent =
				App.t("market.import_cotton_unavailable");
		}
	}
}

function initSudanCottonPricing() {
	document.getElementById("sudan-cotton-pricing-panel").hidden = false;
	for (const [field, value] of Object.entries(SUDAN_COTTON_DEFAULTS)) {
		document.querySelector(`[data-sudan-field="${field}"]`).value = value;
	}
	for (const field of document.querySelectorAll("[data-sudan-field]")) {
		field.addEventListener("input", () => {
			if (field.dataset.sudanField === "domestic_benchmark_cny_per_ton") {
				document.getElementById("sudan-benchmark-source").value = "manual";
			}
			queueSudanCottonPricing();
		});
		field.addEventListener("change", queueSudanCottonPricing);
	}
	document.getElementById("sudan-benchmark-source").addEventListener(
		"change",
		syncSudanBenchmark,
	);
	queueSudanCottonPricing();
}

function syncSudanBenchmark() {
	const source = document.getElementById("sudan-benchmark-source").value;
	if (source !== "manual") {
		const benchmark = importCottonMarketPrices
			? importCottonMarketPrices[source]
			: null;
		document.querySelector(
			'[data-sudan-field="domestic_benchmark_cny_per_ton"]',
		).value = Number.isFinite(benchmark) ? String(benchmark) : "";
	}
	queueSudanCottonPricing();
}

function queueSudanCottonPricing() {
	window.clearTimeout(sudanPricingTimer);
	const requestId = ++sudanPricingRequestId;
	sudanPricingTimer = window.setTimeout(
		() => calculateSudanCottonPricing(requestId),
		180,
	);
}

async function calculateSudanCottonPricing(requestId) {
	const status = document.getElementById("sudan-cotton-pricing-status");
	const results = document.getElementById("sudan-cotton-pricing-results");
	const fields = Object.fromEntries(
		[...document.querySelectorAll("[data-sudan-field]")].map((field) => [
			field.dataset.sudanField,
			field.value,
		]),
	);
	if (!fields.domestic_benchmark_cny_per_ton || !fields.fx_cny_per_usd) {
		status.textContent = App.t("market.sudan_waiting_for_prices");
		results.hidden = true;
		return;
	}
	status.textContent = App.t("market.sudan_calculating");
	try {
		const result = await App.api("/api/sudan-cotton-pricing", {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify(fields),
		});
		if (requestId !== sudanPricingRequestId) return;
		renderSudanCottonPricing(result);
		status.textContent = "";
		results.hidden = false;
	} catch (error) {
		if (requestId !== sudanPricingRequestId) return;
		results.hidden = true;
		status.textContent = error.message || App.t("market.sudan_pricing_unavailable");
	}
}

function renderSudanCottonPricing(result) {
	const format = (value, digits = 2) => new Intl.NumberFormat(
		document.documentElement.lang,
		{ minimumFractionDigits: digits, maximumFractionDigits: digits },
	).format(value);
	const cny = (value) => `CNY ${format(value)}`;
	const usd = (value) => `USD ${format(value)}`;
	const usCentsPerLb = (usdPerTon) => format(usdPerTon / 22.0462);
	const usdPrice = (usdPerTon) => App.t("market.sudan_usd_price_converted", {
		usd: usd(usdPerTon),
		usc: usCentsPerLb(usdPerTon),
	});
	document.getElementById("sudan-max-target-price").textContent = App.t(
		"market.sudan_dual_price",
		{
			usd: usd(result.max_target_cif_usd_per_ton),
			cny: cny(result.max_target_cif_cny_per_ton),
			usc: usCentsPerLb(result.max_target_cif_usd_per_ton),
		},
	);
	document.getElementById("sudan-break-even-price").textContent = App.t(
		"market.sudan_dual_price",
		{
			usd: usd(result.break_even_cif_usd_per_ton),
			cny: cny(result.break_even_cif_cny_per_ton),
			usc: usCentsPerLb(result.break_even_cif_usd_per_ton),
		},
	);
	document.getElementById("sudan-pricing-limit-status").textContent =
		result.max_target_cif_cny_per_ton <= 0
			? App.t("market.sudan_no_positive_cif")
			: "";

	const quotePanel = document.getElementById("sudan-quote-evaluation");
	const quote = result.supplier_evaluation;
	quotePanel.hidden = !quote;
	if (quote) {
		document.getElementById("sudan-quote-cif-price").textContent = App.t(
			"market.sudan_supplier_cif_converted",
			{ value: usdPrice(quote.cif_usd_per_ton) },
		);
		document.getElementById("sudan-quote-profit").textContent = App.t(
			"market.sudan_profit_per_ton",
			{ value: cny(quote.profit_cny_per_ton) },
		);
		document.getElementById("sudan-quote-total-profit").textContent =
			quote.profit_for_quantity_cny === null
				? App.t("market.sudan_quantity_not_set")
				: App.t("market.sudan_profit_total", {
					value: cny(quote.profit_for_quantity_cny),
				});
		document.getElementById("sudan-quote-headroom").textContent = App.t(
			"market.sudan_price_headroom",
			{
				value: usdPrice(
					result.max_target_cif_usd_per_ton - quote.cif_usd_per_ton,
				),
			},
		);
	}

	const cash = result.cash_flow_reference;
	const cashAmount = (value) => cash ? cny(value) : App.t("market.unavailable_short");
	document.getElementById("sudan-cash-flow-reference").textContent = App.t(
		"market.sudan_cash_reference",
		{
			value: cash
				? usdPrice(cash.cif_usd_per_ton)
				: App.t("market.unavailable_short"),
		},
	);
	document.getElementById("sudan-import-vat-cash").textContent = App.t(
		"market.sudan_import_vat_cash",
		{
			paid: cashAmount(cash && cash.import_vat_paid_cny_per_ton),
			recoverable: cashAmount(
				cash && cash.recoverable_import_vat_cny_per_ton,
			),
		},
	);
	document.getElementById("sudan-vat-balance").textContent = App.t(
		"market.sudan_vat_balance",
		{
			value: cashAmount(cash && cash.net_vat_payable_cny_per_ton),
		},
	);
	document.getElementById("sudan-funding").textContent = App.t(
		"market.sudan_funding",
		{
			perTon: cashAmount(cash && cash.initial_funding_cny_per_ton),
			total: cash && cash.initial_funding_for_quantity_cny !== null
				? cny(cash.initial_funding_for_quantity_cny)
				: result.inputs.quantity_tons === null
					? App.t("market.sudan_quantity_not_set")
					: cashAmount(null),
		},
	);

	renderSudanScenarioTable(
		"sudan-domestic-scenarios",
		[App.t("market.sudan_benchmark_delta"), App.t("market.sudan_max_target_price_usd")],
		result.scenarios.domestic_price.map((scenario) => [
			`${scenario.benchmark_delta_cny_per_ton > 0 ? "+" : ""}${format(scenario.benchmark_delta_cny_per_ton)}`,
			usdPrice(scenario.max_target_cif_usd_per_ton),
		]),
	);
	renderSudanScenarioTable(
		"sudan-fx-scenarios",
		[App.t("market.sudan_fx_label"), App.t("market.sudan_max_target_price_usd")],
		result.scenarios.fx.map((scenario) => [
			format(scenario.fx_cny_per_usd, 4),
			usdPrice(scenario.max_target_cif_usd_per_ton),
		]),
	);
	renderSudanScenarioTable(
		"sudan-quota-scenarios",
		[App.t("market.sudan_quota_cost_label"), App.t("market.sudan_max_target_price_usd")],
		result.scenarios.quota.map((scenario) => [
			cny(scenario.quota_cost_cny_per_ton),
			usdPrice(scenario.max_target_cif_usd_per_ton),
		]),
	);
	const quoteScenariosPanel = document.getElementById(
		"sudan-quote-scenarios-panel",
	);
	quoteScenariosPanel.hidden = result.scenarios.supplier_quote.length === 0;
	if (result.scenarios.supplier_quote.length) {
		renderSudanScenarioTable(
			"sudan-quote-scenarios",
			[App.t("market.sudan_quote_cif"), App.t("market.sudan_profit_per_ton_header")],
			result.scenarios.supplier_quote.map((scenario) => [
				usdPrice(scenario.cif_usd_per_ton),
				cny(scenario.profit_cny_per_ton),
			]),
		);
	}
}

function renderSudanScenarioTable(containerId, headers, rows) {
	const container = document.getElementById(containerId);
	const table = document.createElement("table");
	const thead = document.createElement("thead");
	const headerRow = document.createElement("tr");
	for (const label of headers) {
		const cell = document.createElement("th");
		cell.textContent = label;
		headerRow.append(cell);
	}
	thead.append(headerRow);
	table.append(thead);
	const tbody = document.createElement("tbody");
	for (const row of rows) {
		const tableRow = document.createElement("tr");
		for (const value of row) {
			const cell = document.createElement("td");
			cell.textContent = value;
			tableRow.append(cell);
		}
		tbody.append(tableRow);
	}
	table.append(tbody);
	container.replaceChildren(table);
}

function updateImportCottonPrice() {
	const value = document.getElementById("market-import-cotton-price");
	const status = document.getElementById("market-import-cotton-status");
	const fields = [
		document.getElementById("market-import-customs-cost"),
		document.getElementById("market-import-clearance-fee"),
		document.getElementById("market-import-tariff-amount"),
	];
	const parameters = fields.map((field) => field.valueAsNumber);
	const validParameters = fields.map((field, index) => {
		const valid = field.value !== "" && Number.isFinite(parameters[index])
			&& parameters[index] >= 0;
		field.setAttribute("aria-invalid", String(!valid));
		return valid;
	});

	if (!validParameters.every(Boolean)) {
		value.textContent = "—";
		status.textContent = App.t("market.import_cotton_invalid_parameters");
		return;
	}

	const prices = importCottonMarketPrices;
	if (!prices || !Number.isFinite(prices.zce_cotton)
		|| !Number.isFinite(prices.usd_cny) || prices.usd_cny <= 0) {
		value.textContent = App.t("market.unavailable_short");
		status.textContent = App.t("market.import_cotton_unavailable");
		return;
	}

	const price = calculateImportCottonPrice(prices, {
		customsCost: parameters[0],
		clearanceFee: parameters[1],
		tariffAmountCny: parameters[2],
	});
	if (price === null) {
		value.textContent = "—";
		status.textContent = App.t("market.import_cotton_nonpositive_result");
		return;
	}

	const formatter = new Intl.NumberFormat(document.documentElement.lang, {
		minimumFractionDigits: 2,
		maximumFractionDigits: 2,
	});
	value.textContent = `US¢ ${formatter.format(price)}`;
	status.textContent = "";
}

function calculateImportCottonPrice(prices, {
	customsCost,
	clearanceFee,
	tariffAmountCny,
}) {
	if (!prices || !Number.isFinite(prices.zce_cotton)
		|| !Number.isFinite(prices.usd_cny) || prices.usd_cny <= 0) {
		return null;
	}
	const price = (
		(prices.zce_cotton - customsCost) / 1.09
		/ prices.usd_cny / 22.0462
		- tariffAmountCny / prices.usd_cny / 22.0462
		- clearanceFee
	);
	return Number.isFinite(price) && price > 0 ? price : null;
}

function renderMarketCandlestickChart(points, currency, formatter, assetName) {
	if (points.some((point) => ![
		point.open, point.high, point.low, point.close,
	].every(Number.isFinite))) {
		throw new Error(App.t("market.history_unavailable"));
	}
	const container = document.getElementById("market-history-chart");
	container.replaceChildren();
	const namespace = "http://www.w3.org/2000/svg";
	const width = Math.max(760, points.length * 32);
	const height = 320;
	const left = 78;
	const right = 18;
	const top = 18;
	const bottom = 48;
	const plotHeight = height - top - bottom;
	const plotWidth = width - left - right;
	const rawMinimum = Math.min(...points.map(({ low }) => low));
	const rawMaximum = Math.max(...points.map(({ high }) => high));
	const padding = (rawMaximum - rawMinimum) * 0.08
		|| Math.max(rawMaximum * 0.02, 1);
	const minimum = Math.max(0, rawMinimum - padding);
	const maximum = rawMaximum + padding;
	const priceRange = maximum - minimum;
	const step = plotWidth / points.length;
	const candleWidth = Math.min(16, step * 0.58);
	const svg = document.createElementNS(namespace, "svg");
	svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
	svg.setAttribute("role", "img");
	svg.setAttribute("aria-label", App.t("market.history_chart_aria", {
		asset: assetName,
	}));
	const title = document.createElementNS(namespace, "title");
	title.textContent = App.t("market.history_chart_title");
	svg.append(title);
	const priceY = (price) => top + ((maximum - price) / priceRange) * plotHeight;

	for (let tick = 0; tick <= 4; tick += 1) {
		const y = top + (plotHeight * tick) / 4;
		const value = maximum - priceRange * tick / 4;
		const line = document.createElementNS(namespace, "line");
		line.setAttribute("x1", String(left));
		line.setAttribute("x2", String(width - right));
		line.setAttribute("y1", String(y));
		line.setAttribute("y2", String(y));
		line.setAttribute("class", "chart-grid");
		svg.append(line);
		const label = document.createElementNS(namespace, "text");
		label.setAttribute("x", String(left - 10));
		label.setAttribute("y", String(y + 4));
		label.setAttribute("text-anchor", "end");
		label.setAttribute("class", "chart-axis-label");
		label.textContent = formatter.format(value);
		svg.append(label);
	}

	const labelInterval = Math.max(1, Math.ceil(points.length / 6));
	points.forEach((point, index) => {
		const center = left + index * step + step / 2;
		const direction = point.close > point.open
			? "up"
			: point.close < point.open ? "down" : "flat";
		const wick = document.createElementNS(namespace, "line");
		wick.setAttribute("x1", String(center));
		wick.setAttribute("x2", String(center));
		wick.setAttribute("y1", String(priceY(point.high)));
		wick.setAttribute("y2", String(priceY(point.low)));
		wick.setAttribute("class", `candle-wick candle-${direction}`);
		svg.append(wick);
		const openY = priceY(point.open);
		const closeY = priceY(point.close);
		const body = document.createElementNS(namespace, "rect");
		body.setAttribute("x", String(center - candleWidth / 2));
		body.setAttribute("y", String(Math.min(openY, closeY)));
		body.setAttribute("width", String(candleWidth));
		body.setAttribute("height", String(Math.max(2, Math.abs(closeY - openY))));
		body.setAttribute("class", `candle-body candle-${direction}`);
		const tooltip = document.createElementNS(namespace, "title");
		tooltip.textContent = App.t("market.candle_tooltip", {
			date: point.date,
			currency,
			open: formatter.format(point.open),
			high: formatter.format(point.high),
			low: formatter.format(point.low),
			close: formatter.format(point.close),
		});
		body.append(tooltip);
		svg.append(body);
		if (index % labelInterval === 0 || index === points.length - 1) {
			const label = document.createElementNS(namespace, "text");
			label.setAttribute("x", String(center));
			label.setAttribute("y", String(height - 16));
			label.setAttribute("text-anchor", "middle");
			label.setAttribute("class", "chart-axis-label");
			label.textContent = point.date.slice(5);
			svg.append(label);
		}
	});
	container.append(svg);
}

async function loadMarketPrices() {
	const refreshButton = document.getElementById("market-refresh");
	const status = document.getElementById("market-status");
	const updated = document.getElementById("market-updated");
	refreshButton.disabled = true;
	status.textContent = "";
	updated.textContent = "";
	for (const value of document.querySelectorAll(
		"[data-market-value], [data-market-derived]",
	)) {
		value.textContent = App.t("market.loading");
	}
	try {
		const result = await App.api("/api/market-prices");
		const formatter = new Intl.NumberFormat(document.documentElement.lang, {
			minimumFractionDigits: 2,
			maximumFractionDigits: 2,
		});
		for (const value of document.querySelectorAll("[data-market-date]")) {
			value.textContent = "";
		}
		for (const [asset, price] of Object.entries(result.prices)) {
			const value = document.querySelector(`[data-market-value="${asset}"]`);
			const currency = result.asset_currencies && result.asset_currencies[asset]
				? result.asset_currencies[asset]
				: result.currency;
			const priceFormatter = asset.startsWith("cc_index_")
				? new Intl.NumberFormat(document.documentElement.lang, {
					minimumFractionDigits: 0,
					maximumFractionDigits: 0,
				})
				: asset === "usd_cny"
				? new Intl.NumberFormat(document.documentElement.lang, {
					minimumFractionDigits: 4,
					maximumFractionDigits: 4,
				})
				: formatter;
			if (value) value.textContent = `${currency} ${priceFormatter.format(price)}`;
			const quoteDate = result.asset_dates && result.asset_dates[asset];
			const dateValue = document.querySelector(
				`[data-market-date="${asset}"]`,
			);
			if (dateValue && quoteDate) {
				dateValue.textContent = App.t("market.history_as_of", {
					date: quoteDate,
				});
			}
		}
		for (const value of document.querySelectorAll("[data-market-value]")) {
			const asset = value.dataset.marketValue;
			if (!Number.isFinite(result.prices[asset])) {
				value.textContent = App.t("market.unavailable_short");
			}
		}
		const importCottonPrice = calculateImportCottonPrice(
			result.prices,
			IMPORT_COTTON_DEFAULTS,
		);
		if (!Number.isFinite(importCottonPrice)) {
			throw new Error(App.t("market.import_cotton_unavailable"));
		}
		const derivedFormatter = new Intl.NumberFormat(document.documentElement.lang, {
			minimumFractionDigits: 2,
			maximumFractionDigits: 2,
		});
		for (const value of document.querySelectorAll(
			'[data-market-derived="import_cotton"]',
		)) {
			value.textContent = `US¢ ${derivedFormatter.format(importCottonPrice)}`;
		}
		updated.textContent = App.t("market.updated", {
			time: new Intl.DateTimeFormat(document.documentElement.lang, {
				dateStyle: "short",
				timeStyle: "medium",
			}).format(new Date(result.updated_at)),
		});
	} catch (error) {
		status.textContent = error.message || App.t("market.unavailable");
		for (const value of document.querySelectorAll(
			"[data-market-value], [data-market-derived]",
		)) {
			value.textContent = App.t("market.unavailable_short");
		}
	} finally {
		refreshButton.disabled = false;
	}
}

function goToLogin(next) {
	window.location.href = `login.html?next=${encodeURIComponent(next)}`;
}

function handleUnauthorized(error, next) {
	if (error.status === 401) {
		goToLogin(next);
		return true;
	}
	App.setNotice(error.message);
	return false;
}

async function initLoginPage() {
	const form = document.getElementById("auth-form");
	const username = document.getElementById("username");
	const password = document.getElementById("password");
	const confirmation = document.getElementById("confirm-password");
	const confirmationField = document.getElementById("confirmation-field");
	const query = new URLSearchParams(location.search);
	let mode = query.get("mode") === "register" ? "register" : "login";

	function setMode(value) {
		mode = value;
		const registering = mode === "register";
		document.getElementById("mode-label").setAttribute("aria-label", App.t("auth.mode_label"));
		document.getElementById("page-title").textContent = App.t(`auth.${mode}`);
		document.getElementById("page-intro").textContent = App.t(`auth.${mode}_intro`);
		document.getElementById("login-mode").textContent = App.t("auth.login");
		document.getElementById("register-mode").textContent = App.t("auth.register");
		document.getElementById("login-mode").setAttribute("aria-pressed", String(!registering));
		document.getElementById("register-mode").setAttribute("aria-pressed", String(registering));
		document.getElementById("submit-button").textContent = App.t(
			registering ? "auth.register_submit" : "auth.login",
		);
		document.getElementById("username").placeholder = App.t("auth.username_placeholder");
		password.placeholder = App.t(
			registering ? "auth.register_password_placeholder" : "auth.password_placeholder",
		);
		confirmation.placeholder = App.t("auth.confirm_placeholder");
		confirmationField.hidden = !registering;
		confirmation.required = registering;
		password.autocomplete = registering ? "new-password" : "current-password";
	}

	document.getElementById("login-mode").addEventListener("click", () => setMode("login"));
	document.getElementById("register-mode").addEventListener("click", () => setMode("register"));
	form.addEventListener("submit", async (event) => {
		event.preventDefault();
		App.setNotice("");
		try {
			await App.api("/api/auth", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({
					mode,
					username: username.value,
					password: password.value,
					confirm_password: confirmation.value,
				}),
			});
			const next = query.get("next") || "assets.html";
			const safeNext = /^(assets\.html|list\.html|detail\.html\?id=\d+)$/.test(next)
				? next
				: "assets.html";
			window.location.href = safeNext;
		} catch (error) {
			App.setNotice(error.message);
		}
	});
	setMode(mode);
}

async function initAssetsPage() {
	if (!await initAuthenticatedPage("assets.html")) return;
	initMarketBoard();

	document.getElementById("upload-form").addEventListener("submit", async (event) => {
		event.preventDefault();
		App.setNotice("");
		const file = document.getElementById("upload-file").files[0];
		if (!file) return;
		const formData = new FormData();
		formData.append("file", file);
		try {
			const result = await App.api("/api/uploads", { method: "POST", body: formData });
			window.location.href = `detail.html?id=${result.upload.id}`;
		} catch (error) {
			handleUnauthorized(error, "assets.html");
		}
	});
	await loadUploadList(5, "assets.html");
}

async function initListPage() {
	if (!await initAuthenticatedPage("list.html")) return;
	document.title = `${App.t("list.title")} - ${App.t("site.title")}`;
	await loadUploadList(null, "list.html");
}

async function initAuthenticatedPage(nextPage) {
	let session;
	try {
		session = await App.api("/api/session");
	} catch (error) {
		App.setNotice(error.message);
		return false;
	}
	if (!session.authenticated) {
		goToLogin(nextPage);
		return false;
	}
	document.getElementById("username").textContent = App.t("site.logged_in", {
		username: session.user.username,
	});
	document.getElementById("logout-button").addEventListener("click", async () => {
		try {
			await App.api("/api/logout", { method: "POST" });
			window.location.href = "index.html";
		} catch (error) {
			App.setNotice(error.message);
		}
	});
	return true;
}

async function loadUploadList(limit = null, nextPage = "assets.html") {
	const container = document.getElementById("upload-list");
	container.replaceChildren();
	try {
		const result = await App.api("/api/uploads");
		const uploads = limit === null
			? result.uploads
			: result.uploads.slice(0, limit);
		if (uploads.length === 0) {
			const empty = document.createElement("p");
			empty.className = "empty";
			empty.textContent = App.t("site.empty_uploads");
			container.append(empty);
			return;
		}
		const wrapper = document.createElement("div");
		wrapper.className = "table-wrap";
		const table = document.createElement("table");
		const head = document.createElement("thead");
		const headerRow = document.createElement("tr");
		[
			App.t("site.filename"),
			App.t("site.size"),
			App.t("site.uploaded_at"),
			App.t("site.actions"),
		].forEach((label) => {
			const cell = document.createElement("th");
			cell.textContent = label;
			headerRow.append(cell);
		});
		head.append(headerRow);
		const body = document.createElement("tbody");
		for (const upload of uploads) {
			const row = document.createElement("tr");
			const nameCell = document.createElement("td");
			const link = document.createElement("a");
			link.href = `detail.html?id=${upload.id}`;
			link.textContent = upload.filename;
			nameCell.append(link);
			row.append(nameCell);

			const sizeCell = document.createElement("td");
			sizeCell.textContent = `${new Intl.NumberFormat().format(upload.size_bytes)} ${App.t("site.bytes")}`;
			row.append(sizeCell);
			const dateCell = document.createElement("td");
			dateCell.textContent = upload.uploaded_at;
			row.append(dateCell);
			const actionCell = document.createElement("td");
			const deleteButton = document.createElement("button");
			deleteButton.type = "button";
			deleteButton.className = "delete-button";
			deleteButton.textContent = App.t("site.delete");
			deleteButton.addEventListener("click", async () => {
				if (!window.confirm(App.t("site.confirm_delete"))) return;
				try {
					await App.api(`/api/uploads/${upload.id}`, { method: "DELETE" });
					await loadUploadList(limit, nextPage);
				} catch (error) {
					handleUnauthorized(error, nextPage);
				}
			});
			actionCell.append(deleteButton);
			row.append(actionCell);
			body.append(row);
		}
		table.append(head, body);
		wrapper.append(table);
		container.append(wrapper);
	} catch (error) {
		handleUnauthorized(error, nextPage);
	}
}

async function initDetailPage() {
	const id = new URLSearchParams(location.search).get("id");
	if (!/^\d+$/.test(id || "")) {
		App.setNotice(App.t("request.file_not_found"));
		return;
	}
	let current;
	try {
		current = await App.api(`/api/uploads/${id}`);
	} catch (error) {
		handleUnauthorized(error, `detail.html?id=${id}`);
		return;
	}
	renderDetail(current);
	document.getElementById("analyze-button").addEventListener("click", async () => {
		App.setNotice("");
		try {
			const result = await App.api(`/api/uploads/${id}?analyze=1`);
			renderAnalysis(result.analysis);
			if (result.cotton_quotation_import) {
				renderCottonQuotationImport(result.cotton_quotation_import);
			}
		} catch (error) {
			handleUnauthorized(error, `detail.html?id=${id}`);
		}
	});
	const aiAnalyzeButton = document.getElementById("ai-analyze-button");
	aiAnalyzeButton.addEventListener("click", async () => {
		const panel = document.getElementById("ai-analysis");
		const status = document.getElementById("ai-analysis-status");
		const output = document.getElementById("ai-analysis-output");
		aiAnalyzeButton.disabled = true;
		panel.hidden = false;
		status.textContent = App.t("ai.loading");
		output.replaceChildren();
		try {
			const result = await App.api(`/api/uploads/${id}/ai-analysis`, {
				method: "POST",
			});
			renderAiAnalysis(result.analysis);
		} catch (error) {
			if (error.status === 401) {
				handleUnauthorized(error, `detail.html?id=${id}`);
			} else {
				status.textContent = error.message;
			}
		} finally {
			aiAnalyzeButton.disabled = false;
		}
	});
	document.getElementById("download-button").addEventListener("click", async () => {
		try {
			const response = await fetch(
				App.assetUrl(`/api/uploads/${id}/content?download=1`),
				{ credentials: "include" },
			);
			if (!response.ok) throw new Error(App.t("request.file_not_found"));
			const url = URL.createObjectURL(await response.blob());
			const anchor = document.createElement("a");
			anchor.href = url;
			anchor.download = current.upload.filename;
			anchor.click();
			URL.revokeObjectURL(url);
		} catch (error) {
			App.setNotice(error.message);
		}
	});
	document.getElementById("delete-button").addEventListener("click", async () => {
		if (!window.confirm(App.t("site.confirm_delete"))) return;
		try {
			await App.api(`/api/uploads/${id}`, { method: "DELETE" });
			window.location.href = "assets.html";
		} catch (error) {
			handleUnauthorized(error, `detail.html?id=${id}`);
		}
	});
}

function renderDetail(result) {
	const upload = result.upload;
	document.getElementById("filename").textContent = upload.filename;
	document.title = `${upload.filename} - ${App.t("detail.title")}`;
	const labels = [
		["site.filename", upload.filename],
		["site.size", `${new Intl.NumberFormat().format(upload.size_bytes)} ${App.t("site.bytes")}`],
		["detail.record_id", upload.id],
		["detail.file_type", upload.content_type],
		["site.uploaded_at", upload.uploaded_at],
	];
	const details = document.getElementById("file-details");
	details.replaceChildren();
	for (const [label, value] of labels) {
		const item = document.createElement("div");
		item.className = "detail";
		const name = document.createElement("span");
		name.textContent = App.t(label);
		const content = document.createElement("strong");
		content.textContent = value;
		item.append(name, content);
		details.append(item);
	}
	renderPreview(result.preview);
	if (result.analysis) renderAnalysis(result.analysis);
	if (result.ai_analysis) {
		document.getElementById("ai-analysis").hidden = false;
		renderAiAnalysis(result.ai_analysis);
	}
}

async function renderPreview(preview) {
	const container = document.getElementById("preview");
	container.replaceChildren();
	const heading = document.createElement("h2");
	const titles = {
		pdf: "preview.pdf",
		image: "preview.image",
		text: "preview.text",
		unsupported: "preview.text",
		invalid_encoding: "preview.text",
	};
	heading.textContent = App.t(titles[preview.type] || "preview.text");
	container.append(heading);
	if (preview.type === "text") {
		const content = document.createElement("pre");
		content.textContent = preview.text;
		container.append(content);
	} else if (preview.type === "image" || preview.type === "pdf") {
		try {
			const response = await fetch(App.assetUrl(preview.url), { credentials: "include" });
			if (!response.ok) throw new Error(App.t("request.file_not_found"));
			const objectUrl = URL.createObjectURL(await response.blob());
			if (preview.type === "image") {
				const image = document.createElement("img");
				image.src = objectUrl;
				image.alt = document.getElementById("filename").textContent;
				container.append(image);
			} else {
				const frame = document.createElement("iframe");
				frame.src = objectUrl;
				frame.title = App.t("preview.pdf");
				container.append(frame);
			}
		} catch (error) {
			App.setNotice(error.message);
		}
	} else {
		const message = document.createElement("p");
		message.textContent = preview.message;
		container.append(message);
	}
}

function appendTable(container, headers, rows) {
	const wrapper = document.createElement("div");
	wrapper.className = "table-wrap";
	const table = document.createElement("table");
	const head = document.createElement("thead");
	const headerRow = document.createElement("tr");
	for (const header of headers) {
		const cell = document.createElement("th");
		cell.textContent = header;
		headerRow.append(cell);
	}
	head.append(headerRow);
	const body = document.createElement("tbody");
	for (const row of rows) {
		const tableRow = document.createElement("tr");
		for (const value of row) {
			const cell = document.createElement("td");
			cell.textContent = value === null || value === undefined ? "" : String(value);
			tableRow.append(cell);
		}
		body.append(tableRow);
	}
	table.append(head, body);
	wrapper.append(table);
	container.append(wrapper);
}

function renderAnalysis(analysis) {
	const container = document.getElementById("analysis");
	container.replaceChildren();
	container.hidden = false;
	const heading = document.createElement("h2");
	const titleKeys = {
		text: "analysis.file",
		pdf: "analysis.pdf",
		excel: "analysis.excel",
		image: "analysis.image",
		error: "analysis.file",
		unsupported: "analysis.file",
	};
	heading.textContent = App.t(titleKeys[analysis.type] || "analysis.file");
	container.append(heading);
	if (analysis.type === "text") {
		const list = document.createElement("ul");
		for (const [key, count] of [
			["analysis.lines", analysis.line_count],
			["analysis.characters", analysis.character_count],
			["analysis.words", analysis.word_count],
		]) {
			const item = document.createElement("li");
			item.textContent = App.t(key, { count: new Intl.NumberFormat().format(count) });
			list.append(item);
		}
		container.append(list);
	} else if (analysis.type === "pdf") {
		const list = document.createElement("ul");
		for (const [key, count] of [
			["pdf.pages", analysis.page_count],
			["pdf.characters", analysis.character_count],
			["pdf.words", analysis.word_count],
		]) {
			const item = document.createElement("li");
			item.textContent = `${App.t(key)}: ${new Intl.NumberFormat().format(count)}`;
			list.append(item);
		}
		container.append(list);
		if (analysis.frequent_words.length) {
			const frequent = document.createElement("p");
			frequent.textContent = App.t("pdf.frequent_words", {
				words: analysis.frequent_words.map(({ word, count }) => `${word} (${count})`).join(", "),
			});
			container.append(frequent);
		}
		if (analysis.text) {
			const label = document.createElement("h3");
			label.textContent = App.t("pdf.extracted_text");
			const text = document.createElement("pre");
			text.textContent = analysis.text;
			container.append(label, text);
		} else {
			const message = document.createElement("p");
			message.textContent = App.t("pdf.no_text");
			container.append(message);
		}
	} else if (analysis.type === "excel") {
		for (const sheet of analysis.sheets) {
			const section = document.createElement("section");
			const title = document.createElement("h3");
			title.textContent = sheet.name;
			const dimensions = document.createElement("p");
			dimensions.textContent = App.t("excel.rows_columns", {
				rows: new Intl.NumberFormat().format(sheet.rows),
				columns: new Intl.NumberFormat().format(sheet.columns),
			});
			const previewLabel = document.createElement("h4");
			previewLabel.textContent = App.t("excel.preview");
			section.append(title, dimensions, previewLabel);
			appendTable(section, sheet.preview.columns, sheet.preview.data);

			const missingLabel = document.createElement("h4");
			missingLabel.textContent = App.t("excel.columns_missing");
			section.append(missingLabel);
			appendTable(
				section,
				[
					App.t("excel.data_type"),
					App.t("excel.missing_count"),
					App.t("excel.missing_percent"),
				],
				sheet.missing.map((column) => [
					`${column.column} (${column.data_type})`,
					column.count,
					column.percent,
				]),
			);
			if (sheet.numeric_summary) {
				const summaryLabel = document.createElement("h4");
				summaryLabel.textContent = App.t("excel.numeric_summary");
				section.append(summaryLabel);
				const headers = sheet.numeric_summary.columns.map((key) =>
					key === "column" ? App.t("excel.column") : App.t(`excel.stat.${key}`),
				);
				appendTable(section, headers, sheet.numeric_summary.data);
			} else {
				const empty = document.createElement("p");
				empty.textContent = App.t("excel.no_numeric");
				section.append(empty);
			}
			container.append(section);
		}
	} else if (analysis.message) {
		const message = document.createElement("p");
		message.textContent = analysis.message;
		container.append(message);
	}
}

function renderCottonQuotationImport(importResult) {
	const container = document.getElementById("analysis");
	const status = document.createElement("p");
	status.className = "status";
	status.setAttribute("role", "status");
	if (importResult.already_imported) {
		status.textContent = App.t("analysis.cotton_already_imported", {
			count: importResult.rows_found,
		});
	} else if (importResult.rows_inserted > 0) {
		status.textContent = App.t("analysis.cotton_imported", {
			count: importResult.rows_inserted,
		});
	} else {
		status.textContent = App.t("analysis.cotton_import_empty");
	}
	const link = document.createElement("a");
	link.href = "financial_assets_marketing_detail.html?asset=zce_cotton&return=assets";
	link.textContent = App.t("analysis.cotton_view");
	container.prepend(status, link);
}

function renderAiAnalysis(analysis) {
	if (!analysis || analysis.type !== "ai" || typeof analysis.text !== "string") {
		throw new Error(App.t("ai.provider_failed"));
	}
	const status = document.getElementById("ai-analysis-status");
	const output = document.getElementById("ai-analysis-output");
	status.textContent = "";
	output.replaceChildren();
	if (analysis.model) {
		const model = document.createElement("p");
		model.textContent = `${App.t("ai.model_label")}: ${analysis.model}`;
		output.append(model);
	}
	const result = document.createElement("pre");
	result.className = "ai-output";
	result.textContent = analysis.text;
	output.append(result);
}
