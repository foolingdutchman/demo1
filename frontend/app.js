document.addEventListener("DOMContentLoaded", async () => {
	try {
		await App.init();
		const page = document.body.dataset.page;
		if (page === "login") {
			await initLoginPage();
		} else if (page === "home") {
			await initHomePage();
		} else if (page === "detail") {
			await initDetailPage();
		}
	} catch (error) {
		App.setNotice(error.message);
	}
});

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
			const next = query.get("next") || "index.html";
			const safeNext = /^(index\.html|detail\.html\?id=\d+)$/.test(next)
				? next
				: "index.html";
			window.location.href = safeNext;
		} catch (error) {
			App.setNotice(error.message);
		}
	});
	setMode(mode);
}

async function initHomePage() {
	let session;
	try {
		session = await App.api("/api/session");
	} catch (error) {
		App.setNotice(error.message);
		return;
	}
	if (!session.authenticated) {
		goToLogin("index.html");
		return;
	}
	document.getElementById("username").textContent = App.t("site.logged_in", {
		username: session.user.username,
	});

	document.getElementById("logout-button").addEventListener("click", async () => {
		try {
			await App.api("/api/logout", { method: "POST" });
			goToLogin("index.html");
		} catch (error) {
			App.setNotice(error.message);
		}
	});

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
			handleUnauthorized(error, "index.html");
		}
	});
	await loadUploadList();
}

async function loadUploadList() {
	const container = document.getElementById("upload-list");
	container.replaceChildren();
	try {
		const result = await App.api("/api/uploads");
		if (result.uploads.length === 0) {
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
		for (const upload of result.uploads) {
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
					await loadUploadList();
				} catch (error) {
					handleUnauthorized(error, "index.html");
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
		handleUnauthorized(error, "index.html");
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
		} catch (error) {
			handleUnauthorized(error, `detail.html?id=${id}`);
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
			window.location.href = "index.html";
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
