(function () {
  "use strict";

  function initPdfDesign() {
    var form = document.getElementById("pdfDesignForm");
    var saveBtn = document.getElementById("savePdfDesignBtn");
    if (!form || !saveBtn) return;

    // ── Color pickers: sync color ↔ text ──
    var headerColor = form.querySelector('input[name="header_color"]');
    var headerColorText = form.querySelector('input[name="header_color_text"]');
    var accentColor = form.querySelector('input[name="accent_color"]');
    var accentColorText = form.querySelector('input[name="accent_color_text"]');

    function syncColorInputs(picker, text) {
      if (!picker || !text) return;
      picker.addEventListener("input", function () {
        text.value = picker.value;
        updatePreview();
      });
      text.addEventListener("input", function () {
        if (/^#[0-9a-fA-F]{6}$/.test(text.value)) {
          picker.value = text.value;
          updatePreview();
        }
      });
    }
    syncColorInputs(headerColor, headerColorText);
    syncColorInputs(accentColor, accentColorText);

    // ── Toggle switches → live preview ──
    var toggles = form.querySelectorAll('input[type="checkbox"]');
    toggles.forEach(function (el) {
      el.addEventListener("change", updatePreview);
    });

    // ── Text inputs → live preview ──
    var thankYouInput = form.querySelector('input[name="thank_you_message"]');
    var footerNotesInput = form.querySelector('textarea[name="footer_notes"]');
    if (thankYouInput) thankYouInput.addEventListener("input", updatePreview);
    if (footerNotesInput) footerNotesInput.addEventListener("input", updatePreview);

    // ── Live preview updater ──
    function updatePreview() {
      var hdrColor = headerColor ? headerColor.value : "#1f6b43";
      var accColor = accentColor ? accentColor.value : "#1f6b43";

      // Header
      var prevHeader = document.getElementById("prevHeader");
      if (prevHeader) prevHeader.style.backgroundColor = hdrColor;

      // Accent elements
      document.querySelectorAll(".prev-accent").forEach(function (el) {
        el.style.color = accColor;
        el.style.borderBottomColor = accColor;
      });
      document.querySelectorAll(".prev-accent-text").forEach(function (el) {
        el.style.color = accColor;
      });
      var prevTotals = document.getElementById("prevTotals");
      if (prevTotals) prevTotals.style.borderTopColor = accColor;

      // Toggles
      function toggle(id, checked) {
        var el = document.getElementById(id);
        if (el) el.style.display = checked ? "" : "none";
      }

      var cb = function (name) {
        var el = form.querySelector('input[name="' + name + '"]');
        return el ? el.checked : true;
      };

      toggle("prevCustEmail", cb("show_customer_email"));
      toggle("prevCustPhone", cb("show_customer_phone"));
      toggle("prevUnitNum", cb("show_unit_number"));
      toggle("prevVin", cb("show_vin"));
      toggle("prevMileage", cb("show_mileage"));
      toggle("prevLaborMeta", cb("show_labor_hours") || cb("show_labor_rate"));
      toggle("prevPartsTable", cb("show_parts_detail"));
      toggle("prevCoreRow", cb("show_core_charges"));
      toggle("prevCoreSub", cb("show_core_charges"));
      toggle("prevCoreTot", cb("show_core_charges"));
      toggle("prevMiscSub", cb("show_misc_charges"));
      toggle("prevMiscTot", cb("show_misc_charges"));
      toggle("prevShopSupplySub", cb("show_shop_supply"));
      toggle("prevShopSupplyTot", cb("show_shop_supply"));

      // Footer text
      var ty = document.getElementById("prevThankYou");
      if (ty && thankYouInput) {
        ty.textContent = thankYouInput.value || "";
        ty.style.display = thankYouInput.value ? "" : "none";
      }
      var fn = document.getElementById("prevFooterNotes");
      if (fn && footerNotesInput) {
        fn.textContent = footerNotesInput.value || "";
        fn.style.display = footerNotesInput.value ? "" : "none";
      }
    }

    // ── Initial preview ──
    updatePreview();

    // ── Form submit ──
    form.addEventListener("submit", async function (e) {
      e.preventDefault();
      saveBtn.disabled = true;

      var payload = {
        header_color: (headerColor ? headerColor.value : "#1f6b43"),
        accent_color: (accentColor ? accentColor.value : "#1f6b43"),
        show_logo: !!form.querySelector('input[name="show_logo"]').checked,
        show_customer_email: !!form.querySelector('input[name="show_customer_email"]').checked,
        show_customer_phone: !!form.querySelector('input[name="show_customer_phone"]').checked,
        show_unit_number: !!form.querySelector('input[name="show_unit_number"]').checked,
        show_vin: !!form.querySelector('input[name="show_vin"]').checked,
        show_mileage: !!form.querySelector('input[name="show_mileage"]').checked,
        show_labor_hours: !!form.querySelector('input[name="show_labor_hours"]').checked,
        show_labor_rate: !!form.querySelector('input[name="show_labor_rate"]').checked,
        show_parts_detail: !!form.querySelector('input[name="show_parts_detail"]').checked,
        show_core_charges: !!form.querySelector('input[name="show_core_charges"]').checked,
        show_misc_charges: !!form.querySelector('input[name="show_misc_charges"]').checked,
        show_shop_supply: !!form.querySelector('input[name="show_shop_supply"]').checked,
        thank_you_message: (thankYouInput ? thankYouInput.value : "").trim(),
        footer_notes: (footerNotesInput ? footerNotesInput.value : "").trim(),
      };

      try {
        var res = await fetch("/settings/api/pdf-design", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
          credentials: "same-origin",
        });
        var data = await res.json().catch(function () { return {}; });
        if (!res.ok || !data.ok) {
          var msg = (data.errors && data.errors[0]) || data.error || "Failed to save.";
          if (typeof appAlert === "function") appAlert(msg, "error");
          else alert(msg);
          return;
        }
        if (typeof appAlert === "function") appAlert("PDF design saved!", "success");
        else alert("Saved!");
      } catch (err) {
        if (typeof appAlert === "function") appAlert("Network error.", "error");
        else alert("Network error.");
      } finally {
        saveBtn.disabled = false;
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initPdfDesign, { once: true });
  } else {
    initPdfDesign();
  }
})();
