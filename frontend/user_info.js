async function initUserInfoPage() {
	const next = "user_info.html";
	if (!await initAuthenticatedPage(next)) return;
	document.title = `${App.t("profile.title")} - ${App.t("site.title")}`;
	const form = document.getElementById("profile-form");
	const save = document.getElementById("profile-save");
	const passwordForm = document.getElementById("password-form");
	const passwordSave = document.getElementById("profile-password-save");
	const status = document.getElementById("profile-status");
	const fields = {
		company_name: document.getElementById("profile-company-name"),
		company_address: document.getElementById("profile-company-address"),
		foreign_bank_account: document.getElementById("profile-foreign-bank"),
		cny_bank_account: document.getElementById("profile-cny-bank"),
	};

	async function load() {
		const data = await App.api("/api/user-profile");
		if (!data.profile || typeof data.profile !== "object") {
			throw new Error(App.t("profile.invalid"));
		}
		document.getElementById("profile-username").value = data.profile.username || "";
		for (const [name, input] of Object.entries(fields)) {
			input.value = data.profile[name] || "";
		}
	}

	form.addEventListener("submit", async (event) => {
		event.preventDefault();
		App.setNotice("");
		status.textContent = "";
		save.disabled = true;
		try {
			await App.api("/api/user-profile", {
				method: "PATCH",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify(Object.fromEntries(
					Object.entries(fields).map(([name, input]) => [name, input.value]),
				)),
			});
			status.textContent = App.t("profile.saved");
		} catch (error) {
			handleUnauthorized(error, next);
		} finally {
			save.disabled = false;
		}
	});

	passwordForm.addEventListener("submit", async (event) => {
		event.preventDefault();
		App.setNotice("");
		status.textContent = "";
		passwordSave.disabled = true;
		const values = Object.fromEntries(
			new FormData(passwordForm).entries(),
		);
		try {
			await App.api("/api/user-password", {
				method: "PATCH",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify(values),
			});
			passwordForm.reset();
			status.textContent = App.t("profile.password_changed");
		} catch (error) {
			handleUnauthorized(error, next);
		} finally {
			passwordSave.disabled = false;
		}
	});

	try {
		await load();
	} catch (error) {
		handleUnauthorized(error, next);
	}
}
