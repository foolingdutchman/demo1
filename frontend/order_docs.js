const ORDER_DOCUMENT_TYPES = [
	["purchase_contract", "order.document.purchase_contract", true],
	["related_documents", "order.document.related_documents", false],
	["sales_contract", "order.document.sales_contract", false],
	["order_contract", "order.document.order_contract", false],
];

async function initOrderDocumentsPage() {
	const idText = new URLSearchParams(location.search).get("id") || "";
	const id = Number(idText);
	if (!/^[1-9]\d*$/.test(idText) || !Number.isSafeInteger(id)) {
		App.setNotice(App.t("order.not_found"));
		return;
	}
	const next = `order_docs.html?id=${id}`;
	document.title = `${App.t("order.documents_title")} - ${App.t("site.title")}`;
	const list = document.getElementById("order-docs-list");
	const status = document.getElementById("order-docs-status");
	document.getElementById("order-docs-heading").textContent = App.t("order.number", { id });
	document.getElementById("order-docs-back").href = `order_detail.html?id=${id}`;

	function render(documents) {
		list.replaceChildren();
		const saved = new Map(documents.map((document) => [document.document_type, document]));
		for (const [type, labelKey, canGenerate] of ORDER_DOCUMENT_TYPES) {
			const savedDocument = saved.get(type);
			const row = document.createElement("div");
			row.className = "document-row";
			const title = document.createElement("div");
			title.className = "document-title";
			title.textContent = App.t(labelKey);
			const detail = document.createElement("div");
			const filename = document.createElement("div");
			filename.className = savedDocument ? "filename" : "empty";
			filename.textContent = savedDocument?.filename || App.t("order.document.empty");
			detail.append(filename);
			if (savedDocument) {
				const meta = document.createElement("div");
				meta.className = "meta";
				meta.textContent = savedDocument.created_at;
				detail.append(meta);
			}
			const actions = document.createElement("div");
			actions.className = "actions";
			if (savedDocument) {
				const download = document.createElement("button");
				download.type = "button";
				download.className = "secondary";
				download.textContent = App.t("order.document.download");
				download.addEventListener("click", async () => {
					download.disabled = true;
					App.setNotice("");
					try {
						await App.download(`/api/orders/${id}/documents/${type}/content`);
						status.textContent = App.t("order.document.downloaded", {
							filename: savedDocument.filename,
						});
					} catch (error) {
						status.textContent = "";
						handleUnauthorized(error, next);
					} finally {
						download.disabled = false;
					}
				});
				actions.append(download);
			} else if (canGenerate) {
				const generate = document.createElement("button");
				generate.type = "button";
				generate.textContent = App.t("order.contract");
				generate.addEventListener("click", async () => {
					generate.disabled = true;
					App.setNotice("");
					status.textContent = App.t("order.contract_generating");
					try {
						await App.api(`/api/orders/${id}/documents/purchase_contract`, {
							method: "POST",
						});
						await load();
						status.textContent = App.t("order.contract_generated");
					} catch (error) {
						status.textContent = "";
						handleUnauthorized(error, next);
					} finally {
						generate.disabled = false;
					}
				});
				actions.append(generate);
			} else if (type === "related_documents") {
				const input = document.createElement("input");
				input.type = "file";
				input.hidden = true;
				const upload = document.createElement("button");
				upload.type = "button";
				upload.textContent = App.t("order.document.upload");
				upload.addEventListener("click", () => input.click());
				input.addEventListener("change", async () => {
					const file = input.files?.[0];
					if (!file) return;
					upload.disabled = true;
					App.setNotice("");
					status.textContent = App.t("order.document.uploading");
					const body = new FormData();
					body.append("file", file);
					try {
						await App.api(`/api/orders/${id}/documents/related_documents`, {
							method: "POST",
							body,
						});
						await load();
						status.textContent = App.t("order.document.uploaded", {
							filename: file.name,
						});
					} catch (error) {
						status.textContent = "";
						handleUnauthorized(error, next);
					} finally {
						upload.disabled = false;
						input.value = "";
					}
				});
				actions.append(input, upload);
			}
			row.append(title, detail, actions);
			list.append(row);
		}
	}

	async function load() {
		const data = await App.api(`/api/orders/${id}/documents`);
		if (!Array.isArray(data.documents)) throw new Error(App.t("order.document.invalid"));
		render(data.documents);
	}

	render([]);
	if (!await initAuthenticatedPage(next)) return;
	try {
		await load();
	} catch (error) {
		handleUnauthorized(error, next);
	}
}
