(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
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

    function buildPayload(targetForm) {
      var nameInput = targetForm.querySelector('input[name="name"]');
      var addressInput = targetForm.querySelector('input[name="address"]');
      var phoneInput = targetForm.querySelector('input[name="phone"]');
      var emailInput = targetForm.querySelector('input[name="email"]');

      return {
        name: (nameInput && nameInput.value ? nameInput.value : "").trim(),
        address: (addressInput && addressInput.value ? addressInput.value : "").trim(),
        phone: (phoneInput && phoneInput.value ? phoneInput.value : "").trim(),
        email: (emailInput && emailInput.value ? emailInput.value : "").trim(),
      };
    }

    form.addEventListener("submit", async function (event) {
      event.preventDefault();
      setError("");

      var nameInput = form.querySelector('input[name="name"]');
      var payload = buildPayload(form);

      if (!payload.name) {
        setError("Shop name is required.");
        if (nameInput) nameInput.focus();
        return;
      }

      submitBtn.disabled = true;
      try {
        var res = await fetch("/settings/api/locations", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
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
        setEditError("");
      });
    });

    editForm.addEventListener("submit", async function (event) {
      event.preventDefault();
      setEditError("");

      var shopId = (editForm.querySelector('input[name="shop_id"]').value || "").trim();
      var payload = buildPayload(editForm);
      if (!payload.name) {
        setEditError("Shop name is required.");
        return;
      }
      if (!shopId) {
        setEditError("Invalid shop id.");
        return;
      }

      editSubmitBtn.disabled = true;
      try {
        var res = await fetch("/settings/api/locations/" + encodeURIComponent(shopId), {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
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

        var confirmed = window.confirm("Set '" + shopName + "' as inactive?");
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
            alert(msg);
            return;
          }
          window.location.reload();
        } catch (err) {
          alert("Network error while deactivating shop.");
        } finally {
          btn.disabled = false;
        }
      });
    });
  });
})();
