(function () {
  "use strict";

  function initLocationsPage() {
    var form = document.getElementById("createShopForm");
    var errorEl = document.getElementById("createShopError");
    var submitBtn = document.getElementById("createShopSubmitBtn");
    var modalEl = document.getElementById("createShopModal");

    var editForm = document.getElementById("editShopForm");
    var editErrorEl = document.getElementById("editShopError");
    var editSubmitBtn = document.getElementById("editShopSubmitBtn");
    var editModalEl = document.getElementById("editShopModal");

    if (!form || !submitBtn || !modalEl) return;

    function setError(message) {
      if (!errorEl) return;
      if (!message) {
        errorEl.textContent = "";
        errorEl.classList.add("d-none");
        return;
      }
      errorEl.textContent = message;
      errorEl.classList.remove("d-none");
    }

    function setEditError(message) {
      if (!editErrorEl) return;
      if (!message) {
        editErrorEl.textContent = "";
        editErrorEl.classList.add("d-none");
        return;
      }
      editErrorEl.textContent = message;
      editErrorEl.classList.remove("d-none");
    }

    function buildFormData(targetForm) {
      var fd = new FormData();
      var fields = ["name", "address", "phone", "email", "billing_address"];
      fields.forEach(function (f) {
        var inp = targetForm.querySelector('input[name="' + f + '"]');
        fd.append(f, inp ? inp.value.trim() : "");
      });
      var logoInput = targetForm.querySelector('input[name="logo"]');
      if (logoInput && logoInput.files && logoInput.files.length > 0) {
        fd.append("logo", logoInput.files[0]);
      }
      return fd;
    }

    function getVal(targetForm, name) {
      var inp = targetForm.querySelector('input[name="' + name + '"]');
      return inp ? inp.value.trim() : "";
    }

    form.addEventListener("submit", async function (event) {
      event.preventDefault();
      setError("");

      var nameVal = getVal(form, "name");
      if (!nameVal) {
        setError("Shop name is required.");
        var nameInput = form.querySelector('input[name="name"]');
        if (nameInput) nameInput.focus();
        return;
      }
      var addressVal = getVal(form, "address");
      if (!addressVal || addressVal.length < 5) {
        setError("Address is required.");
        var addressInput = form.querySelector('input[name="address"]');
        if (addressInput) addressInput.focus();
        return;
      }
      var billingVal = getVal(form, "billing_address");
      if (!billingVal) {
        setError("Billing address is required.");
        var billingInput = form.querySelector('input[name="billing_address"]');
        if (billingInput) billingInput.focus();
        return;
      }

      submitBtn.disabled = true;
      try {
        var res = await fetch("/settings/api/locations", {
          method: "POST",
          body: buildFormData(form),
          credentials: "same-origin",
        });

        var data = await res.json().catch(function () { return {}; });
        if (!res.ok || !data.ok) {
          var msg = (data && data.errors && data.errors[0]) || (data && data.error) || "Failed to create shop.";
          setError(msg);
          return;
        }

        form.reset();
        var modalInstance = bootstrap.Modal.getInstance(modalEl);
        if (modalInstance) modalInstance.hide();
        window.location.reload();
      } catch (err) {
        setError("Network error while creating shop.");
      } finally {
        submitBtn.disabled = false;
      }
    });

    modalEl.addEventListener("hidden.bs.modal", function () {
      setError("");
      form.reset();
    });

    if (!editForm || !editSubmitBtn || !editModalEl) return;

    document.querySelectorAll(".edit-shop-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        editForm.querySelector('input[name="shop_id"]').value = btn.getAttribute("data-shop-id") || "";
        editForm.querySelector('input[name="name"]').value = btn.getAttribute("data-shop-name") || "";
        editForm.querySelector('input[name="phone"]').value = btn.getAttribute("data-shop-phone") || "";
        editForm.querySelector('input[name="email"]').value = btn.getAttribute("data-shop-email") || "";
        editForm.querySelector('input[name="address"]').value = btn.getAttribute("data-shop-address") || "";
        editForm.querySelector('input[name="billing_address"]').value = btn.getAttribute("data-shop-billing-address") || "";
        // Reset file input (can't set value)
        var logoInput = editForm.querySelector('input[name="logo"]');
        if (logoInput) logoInput.value = "";
        // Show logo preview if shop has one
        var previewEl = document.getElementById("editLogoPreview");
        if (previewEl) {
          var hasLogo = btn.getAttribute("data-shop-has-logo");
          var shopId = btn.getAttribute("data-shop-id");
          if (hasLogo && shopId) {
            previewEl.querySelector("img").src = "/settings/api/locations/" + shopId + "/logo?t=" + Date.now();
            previewEl.classList.remove("d-none");
          } else {
            previewEl.classList.add("d-none");
          }
        }
        setEditError("");
      });
    });

    editForm.addEventListener("submit", async function (event) {
      event.preventDefault();
      setEditError("");

      var shopId = (editForm.querySelector('input[name="shop_id"]').value || "").trim();
      if (!getVal(editForm, "name")) {
        setEditError("Shop name is required.");
        return;
      }
      if (!getVal(editForm, "billing_address")) {
        setEditError("Billing address is required.");
        return;
      }
      if (!shopId) {
        setEditError("Invalid shop id.");
        return;
      }

      editSubmitBtn.disabled = true;
      try {
        var fd = buildFormData(editForm);
        fd.append("shop_id", shopId);
        var res = await fetch("/settings/api/locations/" + encodeURIComponent(shopId), {
          method: "POST",
          body: fd,
          credentials: "same-origin",
        });
        var data = await res.json().catch(function () { return {}; });
        if (!res.ok || !data.ok) {
          var msg = (data && data.errors && data.errors[0]) || (data && data.error) || "Failed to update shop.";
          setEditError(msg);
          return;
        }

        var editModalInstance = bootstrap.Modal.getInstance(editModalEl);
        if (editModalInstance) editModalInstance.hide();
        window.location.reload();
      } catch (err) {
        setEditError("Network error while updating shop.");
      } finally {
        editSubmitBtn.disabled = false;
      }
    });

    editModalEl.addEventListener("hidden.bs.modal", function () {
      setEditError("");
      editForm.reset();
    });

    document.querySelectorAll(".inactive-shop-btn").forEach(function (btn) {
      btn.addEventListener("click", async function () {
        var shopId = btn.getAttribute("data-shop-id") || "";
        var shopName = btn.getAttribute("data-shop-name") || "this shop";
        if (!shopId) return;

        var confirmed = await appConfirm("Set '" + shopName + "' as inactive?");
        if (!confirmed) return;

        btn.disabled = true;
        try {
          var res = await fetch("/settings/api/locations/" + encodeURIComponent(shopId) + "/inactive", {
            method: "POST",
            credentials: "same-origin",
          });
          var data = await res.json().catch(function () { return {}; });
          if (!res.ok || !data.ok) {
            var msg = (data && data.errors && data.errors[0]) || (data && data.error) || "Failed to deactivate shop.";
            appAlert(msg, 'error');
            return;
          }
          window.location.reload();
        } catch (err) {
          appAlert("Network error while deactivating shop.", 'error');
        } finally {
          btn.disabled = false;
        }
      });
    });

    // Auto-copy address → billing_address when billing is empty
    var createAddr = form.querySelector('input[name="address"]');
    var createBilling = form.querySelector('input[name="billing_address"]');
    if (createAddr && createBilling) {
      createAddr.addEventListener("change", function () {
        if (!createBilling.value.trim()) {
          createBilling.value = createAddr.value;
        }
      });
    }

  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initLocationsPage, { once: true });
  } else {
    initLocationsPage();
  }
})();
