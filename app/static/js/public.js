// Auto-search over server data without full page reload.
(function () {
	var pageInputNames = [
		"page",
		"per_page",
		"parts_page",
		"parts_per_page",
		"orders_page",
		"orders_per_page",
		"cores_page",
		"cores_per_page",
		"estimates_page",
		"estimates_per_page",
	];
	var activeSearchController = null;

	function getFormActionPath(form) {
		var action = form.getAttribute("action") || window.location.pathname;
		return new URL(action, window.location.origin).pathname;
	}

	function getFormTabValue(form) {
		var tabInput = form.querySelector('input[name="tab"]');
		return tabInput ? String(tabInput.value || "") : "";
	}

	function captureInputFocusState(form, input) {
		var activeElement = document.activeElement;
		var isFocused = activeElement === input;

		return {
			actionPath: getFormActionPath(form),
			tabValue: getFormTabValue(form),
			shouldRestoreFocus: isFocused,
			selectionStart: isFocused ? input.selectionStart : null,
			selectionEnd: isFocused ? input.selectionEnd : null,
			selectionDirection: isFocused ? input.selectionDirection : null,
		};
	}

	function findMatchingSearchInput(focusState) {
		var forms = document.querySelectorAll('form[method="get"]');
		for (var i = 0; i < forms.length; i += 1) {
			var form = forms[i];
			var input = form.querySelector('input[name="q"]');
			if (!input) {
				continue;
			}

			if (getFormActionPath(form) !== focusState.actionPath) {
				continue;
			}

			if (getFormTabValue(form) !== focusState.tabValue) {
				continue;
			}

			return input;
		}

		return null;
	}

	function restoreInputFocusState(focusState) {
		if (!focusState || !focusState.shouldRestoreFocus) {
			return;
		}

		var input = findMatchingSearchInput(focusState);
		if (!input) {
			return;
		}

		input.focus({ preventScroll: true });

		if (
			typeof focusState.selectionStart === "number" &&
			typeof focusState.selectionEnd === "number"
		) {
			input.setSelectionRange(
				focusState.selectionStart,
				focusState.selectionEnd,
				focusState.selectionDirection || "none"
			);
		}
	}

	function buildSearchUrl(form, input) {
		var url = new URL(getFormActionPath(form), window.location.origin);
		var formData = new FormData(form);
		var qValue = (input.value || "").trim();

		for (var i = 0; i < pageInputNames.length; i += 1) {
			formData.delete(pageInputNames[i]);
		}

		if (qValue) {
			formData.set("q", qValue);
		} else {
			formData.delete("q");
		}

		var params = new URLSearchParams();
		formData.forEach(function (value, key) {
			params.append(key, String(value));
		});
		url.search = params.toString();
		return url;
	}

	function buildFormSignature(form, input) {
		var url = buildSearchUrl(form, input);
		return url.pathname + "?" + url.searchParams.toString();
	}

	function replaceMainContent(html) {
		var parser = new DOMParser();
		var doc = parser.parseFromString(html, "text/html");
		var newMainCol = doc.querySelector(".app-main-col");
		var currentMainCol = document.querySelector(".app-main-col");

		if (!newMainCol || !currentMainCol) {
			return false;
		}

		currentMainCol.innerHTML = newMainCol.innerHTML;
		window.dispatchEvent(new CustomEvent("smallshop:content-replaced"));
		bindAutoSearchForms();
		return true;
	}

	async function runSearch(form, input) {
		var url = buildSearchUrl(form, input);
		var focusState = captureInputFocusState(form, input);

		if (activeSearchController) {
			activeSearchController.abort();
		}
		activeSearchController = new AbortController();

		try {
			document.body.classList.add("is-search-loading");
			var response = await fetch(url.toString(), {
				method: "GET",
				headers: {
					"X-Requested-With": "XMLHttpRequest",
					"Accept": "text/html",
				},
				signal: activeSearchController.signal,
				credentials: "same-origin",
			});

			if (!response.ok) {
				throw new Error("Search request failed");
			}

			var html = await response.text();
			var replaced = replaceMainContent(html);
			if (!replaced) {
				window.location.assign(url.toString());
				return;
			}

			var hash = window.location.hash || "";
			window.history.replaceState({}, "", url.pathname + url.search + hash);
			restoreInputFocusState(focusState);
		} catch (error) {
			if (error && error.name === "AbortError") {
				return;
			}
			window.location.assign(url.toString());
		} finally {
			document.body.classList.remove("is-search-loading");
		}
	}

	function setupAutoSearch(form) {
		if (form.dataset.autoSearchBound === "1") {
			return;
		}

		var input = form.querySelector('input[name="q"]');
		if (!input) {
			return;
		}

		form.dataset.autoSearchBound = "1";
		var delayMs = 450;
		var timer = null;
		var lastSubmittedSignature = buildFormSignature(form, input);

		function submitIfChanged() {
			var nextSignature = buildFormSignature(form, input);
			if (nextSignature === lastSubmittedSignature) {
				return;
			}
			lastSubmittedSignature = nextSignature;
			runSearch(form, input);
		}

		form.addEventListener("submit", function (event) {
			event.preventDefault();
			if (timer) {
				window.clearTimeout(timer);
			}
			submitIfChanged();
		});

		input.addEventListener("input", function () {
			if (timer) {
				window.clearTimeout(timer);
			}
			timer = window.setTimeout(submitIfChanged, delayMs);
		});

		input.addEventListener("keydown", function (event) {
			if (event.key !== "Enter") {
				return;
			}
			event.preventDefault();
			if (timer) {
				window.clearTimeout(timer);
			}
			submitIfChanged();
		});

		form.addEventListener("change", function (event) {
			var target = event && event.target;
			if (!target || !target.name) {
				return;
			}
			if (target.name === "q") {
				return;
			}
			if (target.name === "date_preset") {
				var dateFromInput = form.querySelector('input[name="date_from"]');
				var dateToInput = form.querySelector('input[name="date_to"]');
				if (dateFromInput) {
					dateFromInput.value = "";
				}
				if (dateToInput) {
					dateToInput.value = "";
				}
			}
			if (target.name === "date_from" || target.name === "date_to") {
				var presetSelect = form.querySelector('select[name="date_preset"]');
				if (presetSelect && presetSelect.value !== "custom") {
					presetSelect.value = "custom";
				}
			}
			submitIfChanged();
		});
	}

	function bindAutoSearchForms() {
		var forms = document.querySelectorAll('form[method="get"]');
		for (var i = 0; i < forms.length; i += 1) {
			if (forms[i].querySelector('input[name="q"]')) {
				setupAutoSearch(forms[i]);
			}
		}
	}

	document.addEventListener("DOMContentLoaded", bindAutoSearchForms);
})();
