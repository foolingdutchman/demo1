const ORDER_DOCUMENT_TYPES = [
	["purchase_contract", "order.document.purchase_contract", true],
	["related_documents", "order.document.related_documents", false],
	["sales_contract", "order.document.sales_contract", false],
	["order_contract", "order.document.order_contract", false],
];
const WAYBILL_FIELDS = [
	["BL_NUMBER", "waybill.bl_number"],
	["BL_NET_WEIGHT", "waybill.bl_net_weight"],
	["WEIGHT_UNIT", "waybill.weight_unit"],
	["DEPARTURE_DATE", "waybill.departure_date"],
	["ARRIVAL_DATE", "waybill.arrival_date"],
	["PACK_NUMBER", "waybill.pack_number"],
	["PACK_UNIT", "waybill.pack_unit"],
	["VOYAGE_NUMBER", "waybill.voyage_number"],
	["COO_INOVICE_NUMBER", "waybill.coo_inovice_number"],
	["COO_INVOICE_DATE", "waybill.coo_invoice_date"],
];
const WAYBILL_METADATA_FIELDS = [
	["product", "market.cotton_column.product"],
	["origin", "market.cotton_column.origin"],
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
	let currentHasIntermediary = false;
	let currentDocuments = [];
	let currentRelatedDocuments = [];
	let currentWaybills = [];
	let pollingTimer = null;
	let reviewQueue = [];
	let reviewOpen = false;
	document.getElementById("order-docs-heading").textContent = App.t("order.number", { id });
	document.getElementById("order-docs-back").href = `order_detail.html?id=${id}`;

	function render(
		documents,
		hasIntermediary = false,
		relatedDocuments = [],
		waybills = [],
	) {
		currentHasIntermediary = hasIntermediary;
		currentDocuments = documents;
		currentRelatedDocuments = relatedDocuments;
		currentWaybills = waybills;
		list.replaceChildren();
		const saved = new Map(documents.map((document) => [document.document_type, document]));
		const types = hasIntermediary
			? [
				["supplier_intermediary_contract", "order.document.supplier_intermediary_contract", true],
				["intermediary_user_contract", "order.document.intermediary_user_contract", true],
				...ORDER_DOCUMENT_TYPES.slice(1),
			]
			: ORDER_DOCUMENT_TYPES;
		for (const [type, labelKey, canGenerate] of types) {
			const savedDocument = saved.get(type);
			const row = document.createElement("div");
			row.className = "document-row";
			const title = document.createElement("div");
			title.className = "document-title";
			title.textContent = App.t(labelKey);
			const detail = document.createElement("div");
			const relatedActions = document.createElement("div");
			relatedActions.className = "related-document-actions";
			const files = type === "related_documents" ? relatedDocuments : (savedDocument ? [savedDocument] : []);
			if (!files.length) {
				const filename = document.createElement("div");
				filename.className = "empty";
				filename.textContent = App.t("order.document.empty");
				detail.append(filename);
			}
			for (const file of files) {
				const item = document.createElement("div");
				item.className = "related-file";
				const filename = document.createElement("div");
				filename.className = type === "related_documents"
					? "meta"
					: "filename";
				filename.textContent = type === "related_documents"
					? App.t("order.document.analysis_result")
					: file.filename;
				const meta = document.createElement("div");
				meta.className = "meta";
				const category = file.analysis_status === "pending"
					? App.t("order.document.analysis_pending")
					: file.analysis?.category
					? App.t(`order.document.category.${file.analysis.category}`)
					: App.t("order.document.category.unrecognized");
				const fields = Object.entries(file.analysis?.fields || {})
					.map(([key, value]) => `${App.t(`order.document.field.${key}`)}: ${value}`)
					.join(" · ");
				meta.textContent = [category, file.created_at, fields].filter(Boolean).join(" · ");
				item.append(filename, meta);
				if (file.summary) {
					const summary = document.createElement("div");
					summary.className = "meta";
					summary.textContent = `${App.t("order.document.summary")}: ${file.summary}`;
					item.append(summary);
				}
				detail.append(item);
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
						const result = await App.api(`/api/orders/${id}/documents/purchase_contract`, {
							method: "POST",
						});
						render(
							result.documents || [],
							hasIntermediary,
							currentRelatedDocuments,
							currentWaybills,
						);
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
				actions.className = "actions related-actions";
				actions.append(relatedActions);
				for (const file of relatedDocuments) {
					const fileRow = document.createElement("div");
					fileRow.className = "related-document-row";
					const fileName = document.createElement("span");
					fileName.className = "related-document-name";
					fileName.textContent = file.filename;
					const download = document.createElement("button");
					download.type = "button";
					download.className = "secondary";
					download.textContent = App.t("order.document.download");
					download.addEventListener("click", async () => {
						download.disabled = true;
						try {
							await App.download(
								`/api/orders/${id}/related-documents/${file.id}/content`,
							);
						} catch (error) {
							handleUnauthorized(error, next);
						} finally {
							download.disabled = false;
						}
					});
					const remove = document.createElement("button");
					remove.type = "button";
					remove.className = "delete";
					remove.textContent = App.t("order.document.delete");
					remove.addEventListener("click", async () => {
						if (!await App.confirm(App.t("order.document.confirm_delete", {
							filename: file.filename,
						}))) return;
						remove.disabled = true;
						try {
							await App.api(
								`/api/orders/${id}/related-documents/${file.id}`,
								{ method: "DELETE" },
							);
							await load();
						} catch (error) {
							handleUnauthorized(error, next);
							remove.disabled = false;
						}
					});
					fileRow.append(fileName, download, remove);
					relatedActions.append(fileRow);
				}
				const input = document.createElement("input");
				input.type = "file";
				input.accept = ".pdf,image/*";
				input.multiple = true;
				input.hidden = true;
				const upload = document.createElement("button");
				upload.type = "button";
				upload.className = "upload-row";
				upload.textContent = App.t("order.document.upload");
				upload.addEventListener("click", () => input.click());
				input.addEventListener("change", async () => {
					const files = [...(input.files || [])];
					if (!files.length) return;
					upload.disabled = true;
					App.setNotice("");
					showAnalysisNotice(App.t("order.document.analyzing"));
					const body = new FormData();
					for (const file of files) body.append("file", file);
					try {
						const result = await App.api(`/api/orders/${id}/documents/related_documents`, {
							method: "POST",
							body,
						});
						render(
							currentDocuments,
							currentHasIntermediary,
							[...(result.documents || []), ...currentRelatedDocuments],
							currentWaybills,
						);
						await pollAnalysis();
						startPolling();
						status.textContent = App.t("order.document.uploaded", {
							filename: files.map((file) => file.name).join(", "),
						});
					} catch (error) {
						status.textContent = "";
						document.getElementById("document-analysis-loading")?.remove();
						handleUnauthorized(error, next);
					} finally {
						upload.disabled = false;
						input.value = "";
					}
				});
				const uploadRow = document.createElement("div");
				uploadRow.className = "related-upload-row";
				uploadRow.append(input, upload);
				actions.append(uploadRow);
			}

			function showAnalysisNotice(message) {
				App.setNotice("");
				const existing = document.getElementById("document-analysis-loading");
				existing?.remove();
				const overlay = document.createElement("div");
				overlay.id = "document-analysis-loading";
				overlay.className = "app-dialog-overlay";
				const dialog = document.createElement("div");
				dialog.className = "app-dialog";
				const title = document.createElement("h2");
				title.textContent = App.t("order.document.analysis_loading_title");
				const content = document.createElement("p");
				content.textContent = message;
				dialog.append(title, content);
				overlay.append(dialog);
				document.body.append(overlay);
			}

			function showReviewDialog(file) {
				reviewOpen = true;
				const overlay = document.createElement("div");
				overlay.className = "app-dialog-overlay";
				const dialog = document.createElement("div");
				dialog.className = "app-dialog document-review-dialog";
				const title = document.createElement("h2");
				title.textContent = App.t("order.document.review_title");
				const summaryLabel = document.createElement("label");
				summaryLabel.className = "review-field";
				summaryLabel.textContent = App.t("order.document.summary");
				const summary = document.createElement("textarea");
				summary.rows = 2;
				summary.value = file.summary || "";
				summaryLabel.append(summary);
				const textLabel = document.createElement("label");
				textLabel.className = "review-field";
				textLabel.textContent = App.t("order.document.recognized_text");
				const recognized = document.createElement("textarea");
				recognized.rows = 2;
				recognized.value = file.recognized_text || file.analysis?.text || "";
				textLabel.append(recognized);
				const actions = document.createElement("div");
				actions.className = "app-dialog-actions";
				const cancel = document.createElement("button");
				cancel.className = "app-dialog-cancel";
				cancel.textContent = App.t("site.cancel");
				const close = () => {
					overlay.remove();
					reviewOpen = false;
					showNextReview();
				};
				cancel.addEventListener("click", close);
				const save = document.createElement("button");
				save.className = "app-dialog-confirm";
				save.textContent = App.t("profile.save");
				save.addEventListener("click", async () => {
					save.disabled = true;
					try {
						await App.api(`/api/orders/${id}/related-documents/${file.id}`, {
							method: "PATCH",
							headers: { "Content-Type": "application/json" },
							body: JSON.stringify({
								summary: summary.value,
								recognized_text: recognized.value,
							}),
						});
						close();
						await load();
					} catch (error) {
						handleUnauthorized(error, next);
						save.disabled = false;
					}
				});
				actions.append(cancel, save);
				dialog.append(title, summaryLabel, textLabel, actions);
				overlay.append(dialog);
				document.body.append(overlay);
			}

			function showNextReview() {
				if (reviewOpen || !reviewQueue.length) return;
				showReviewDialog(reviewQueue.shift());
			}

			async function pollAnalysis() {
				try {
					const data = await App.api(`/api/orders/${id}/documents`);
					const pending = (data.related_documents || []).filter(
						(file) => file.analysis_status === "pending",
					);
					const newlyCompleted = (data.related_documents || []).filter(
						(file) => file.analysis_status === "complete"
							&& !currentRelatedDocuments.some(
								(current) => current.id === file.id
									&& current.analysis_status === "complete",
							),
					);
					render(
						data.documents,
						data.has_intermediary,
						data.related_documents || [],
						data.waybills || [],
					);
					if (!pending.length) {
						clearInterval(pollingTimer);
						pollingTimer = null;
						document.getElementById("document-analysis-loading")?.remove();
						reviewQueue.push(...newlyCompleted);
						showNextReview();
					}
				} catch (error) {
					clearInterval(pollingTimer);
					pollingTimer = null;
					document.getElementById("document-analysis-loading")?.remove();
					handleUnauthorized(error, next);
				}
			}

			function startPolling() {
				if (pollingTimer) clearInterval(pollingTimer);
				pollingTimer = setInterval(pollAnalysis, 1500);
			}
			row.append(title, detail, actions);
			list.append(row);
		}
		if (waybills.length) {
			const section = document.createElement("section");
			section.className = "waybill-section";
			const heading = document.createElement("h2");
			heading.textContent = App.t("waybill.title");
			section.append(heading);
			for (const waybill of waybills) {
				const wrapper = document.createElement("div");
				wrapper.className = "waybill-editor";
				const source = relatedDocuments.find(
					(document) => document.id === waybill.related_document_id,
				);
				const caption = document.createElement("div");
				caption.className = "waybill-caption";
				caption.textContent = source?.filename || "";
				const table = document.createElement("table");
				table.className = "waybill-table";
				const thead = document.createElement("thead");
				const headerRow = document.createElement("tr");
				for (const [_field, labelKey] of [
					...WAYBILL_METADATA_FIELDS,
					...WAYBILL_FIELDS,
				]) {
					const th = document.createElement("th");
					th.textContent = App.t(labelKey);
					headerRow.append(th);
				}
				const actionHeader = document.createElement("th");
				actionHeader.textContent = App.t("waybill.action");
				headerRow.append(actionHeader);
				thead.append(headerRow);
				const tbody = document.createElement("tbody");
				const displayRow = document.createElement("tr");
				const inputs = new Map();
				const actionCell = document.createElement("td");

				function renderRow() {
					displayRow.replaceChildren();
					for (const [field] of [
						...WAYBILL_METADATA_FIELDS,
						...WAYBILL_FIELDS,
					]) {
						const cell = document.createElement("td");
						cell.textContent = waybill[field] || "—";
						displayRow.append(cell);
					}
					const edit = document.createElement("button");
					edit.type = "button";
					edit.className = "secondary";
					edit.textContent = App.t("waybill.edit");
					edit.addEventListener("click", renderEditRow);
					actionCell.replaceChildren(edit);
					displayRow.append(actionCell);
				}

				function renderEditRow() {
					displayRow.replaceChildren();
					inputs.clear();
					for (const [field] of WAYBILL_METADATA_FIELDS) {
						const td = document.createElement("td");
						td.textContent = waybill[field] || "—";
						displayRow.append(td);
					}
					for (const [field, labelKey] of WAYBILL_FIELDS) {
						const td = document.createElement("td");
						const input = document.createElement("input");
						input.value = waybill[field] || "";
						input.setAttribute("aria-label", App.t(labelKey));
						inputs.set(field, input);
						td.append(input);
						displayRow.append(td);
					}
					const actions = document.createElement("div");
					actions.className = "waybill-row-actions";
					const cancel = document.createElement("button");
					cancel.type = "button";
					cancel.className = "secondary";
					cancel.textContent = App.t("waybill.cancel");
					cancel.addEventListener("click", renderRow);
					const confirm = document.createElement("button");
					confirm.type = "button";
					confirm.textContent = App.t("waybill.confirm");
					confirm.addEventListener("click", async () => {
						confirm.disabled = true;
						cancel.disabled = true;
						App.setNotice("");
						try {
							const result = await App.api(
								`/api/orders/${id}/related-documents/${waybill.related_document_id}/waybill`,
								{
									method: "PATCH",
									headers: { "Content-Type": "application/json" },
									body: JSON.stringify(Object.fromEntries(
										[...inputs].map(([name, input]) => [name, input.value]),
									)),
								},
							);
							Object.assign(waybill, result.waybill || {});
							status.textContent = App.t("waybill.saved");
							renderRow();
						} catch (error) {
							handleUnauthorized(error, next);
							confirm.disabled = false;
							cancel.disabled = false;
						}
					});
					actions.append(confirm, cancel);
					actionCell.replaceChildren(actions);
					displayRow.append(actionCell);
				}

				tbody.append(displayRow);
				table.append(thead, tbody);
				renderRow();
				wrapper.append(caption, table);
				section.append(wrapper);
			}
			list.append(section);
		}
	}

	async function load() {
		const data = await App.api(`/api/orders/${id}/documents`);
		if (!Array.isArray(data.documents)) throw new Error(App.t("order.document.invalid"));
		render(
			data.documents,
			data.has_intermediary,
			data.related_documents || [],
			data.waybills || [],
		);
		if ((data.related_documents || []).some(
			(file) => file.analysis_status === "pending",
		)) {
			startPolling();
		}
	}

	render([]);
	if (!await initAuthenticatedPage(next)) return;
	try {
		await load();
	} catch (error) {
		handleUnauthorized(error, next);
	}
}
