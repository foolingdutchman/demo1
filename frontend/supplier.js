async function initSupplierPage() {
	const params = new URLSearchParams(
		typeof location === "undefined" ? "" : location.search
	);
	const next = params.get("role") === "intermediary"
		? "supplier.html?return=book_order.html&role=intermediary"
		: "supplier.html?return=book_order.html";
	if (!await initAuthenticatedPage(next)) return;
	document.title = `${App.t("supplier.title")} - ${App.t("site.title")}`;
	const form = document.getElementById("supplier-form");
	const save = document.getElementById("supplier-save");
	form.addEventListener("submit", async (event) => {
		event.preventDefault();
		App.setNotice("");
		save.disabled = true;
		const fields = Object.fromEntries(new FormData(form).entries());
		try {
			const result = await App.api("/api/suppliers", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify(fields),
			});
			try {
				const draft = JSON.parse(sessionStorage.getItem("orderDraft") || "{}");
				if (params.get("role") === "intermediary") {
					draft.intermediaryId = String(result.supplier.id);
				} else {
					draft.supplierId = String(result.supplier.id);
				}
				sessionStorage.setItem("orderDraft", JSON.stringify(draft));
			} catch (error) {
				App.setNotice(App.t("order.draft_failed"));
				save.disabled = false;
				return;
			}
			window.location.href = "book_order.html";
		} catch (error) {
			handleUnauthorized(error, next);
			save.disabled = false;
		}
	});
}
