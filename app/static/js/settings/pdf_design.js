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
      var hdrColor = headerColor ? headerColor.value : "#1a1a1a";
      var accColor = accentColor ? accentColor.value : "#1a1a1a";

      // Divider line
      var prevDivider = document.getElementById("prevDivider");
      if (prevDivider) prevDivider.style.borderTopColor = hdrColor;

      // Bill To header (text + border, no bg)
      var prevBillToHdr = document.getElementById("prevBillToHdr");
      if (prevBillToHdr) {
        prevBillToHdr.style.color = hdrColor;
        prevBillToHdr.style.borderBottomColor = hdrColor;
      }

      // Remit Payment To header (mirrors Bill To styling)
      var prevRemitHdr = document.getElementById("prevRemitHdr");
      if (prevRemitHdr) {
        prevRemitHdr.style.color = hdrColor;
        prevRemitHdr.style.borderBottomColor = hdrColor;
      }

      // Info row header
      var prevInfoRowHeader = document.getElementById("prevInfoRowHeader");
      if (prevInfoRowHeader) {
        prevInfoRowHeader.querySelectorAll("th").forEach(function (th) {
          th.style.backgroundColor = hdrColor;
        });
      }

      // Items table header row
      var prevItemsHeader = document.getElementById("prevItemsHeader");
      if (prevItemsHeader) {
        prevItemsHeader.querySelectorAll("th").forEach(function (th) {
          th.style.backgroundColor = hdrColor;
        });
      }

      // Labor separator lines
      document.querySelectorAll(".prev-labor-sep").forEach(function (tr) {
        var td = tr.querySelector("td");
        if (td && td.style.borderBottom) {
          td.style.borderBottomColor = hdrColor;
        }
      });

      // Accent elements (WO number text)
      document.querySelectorAll(".prev-accent-text").forEach(function (el) {
        el.style.color = accColor;
      });

      // Totals grand row border
      var prevTotals = document.getElementById("prevTotals");
      if (prevTotals) {
        prevTotals.querySelectorAll("td").forEach(function (td) {
          td.style.borderTopColor = hdrColor;
        });
      }

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
      toggle("prevLogoArea", cb("show_logo"));
      toggle("prevUnitNum", cb("show_unit_number"));
      toggle("prevInfoUnitHdr", cb("show_unit_number"));
      toggle("prevInfoUnitVal", cb("show_unit_number"));
      toggle("prevVin", cb("show_vin"));
      toggle("prevMileage", cb("show_mileage"));
      toggle("prevLaborMeta", cb("show_labor_hours") || cb("show_labor_rate"));
      toggle("prevPartsTable", cb("show_parts_detail"));
      toggle("prevCoreRow", cb("show_core_charges"));
      toggle("prevCoreTot", cb("show_core_charges"));
      toggle("prevMiscTot", cb("show_misc_charges"));
      toggle("prevShopSupplyTot", cb("show_shop_supply"));
      toggle("prevPaidRow", cb("show_balance_due"));
      toggle("prevBalanceRow", cb("show_balance_due"));

      // Footer text
      var ty = document.getElementById("prevThankYou");
      if (ty && thankYouInput) {
        ty.textContent = thankYouInput.value || "";
        ty.style.display = thankYouInput.value ? "" : "none";
      }
      var termsContent = document.getElementById("prevTermsContent");
      if (termsContent && footerNotesInput) {
        var val = footerNotesInput.value.trim();
        if (val) {
          termsContent.innerHTML = "";
          val.split("\n").forEach(function (line) {
            var p = document.createElement("p");
            p.style.margin = "1px 0";
            p.textContent = line;
            termsContent.appendChild(p);
          });
        } else {
          termsContent.innerHTML =
            '<p style="margin:1px 0;">1) Payment is due upon completion unless otherwise agreed in writing.</p>' +
            '<p style="margin:1px 0;">2) Parts warranties are provided solely by the manufacturer. Labor warranty: 30 days or 1,000 miles.</p>' +
            '<p style="margin:1px 0;">3) Customer-supplied parts carry no warranty.</p>';
        }
      }
    }

    // ── Initial preview ──
    updatePreview();

    // ── Form submit ──
    form.addEventListener("submit", async function (e) {
      e.preventDefault();
      saveBtn.disabled = true;

      var payload = {
        header_color: (headerColor ? headerColor.value : "#1a1a1a"),
        accent_color: (accentColor ? accentColor.value : "#1a1a1a"),
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
        show_balance_due: !!form.querySelector('input[name="show_balance_due"]').checked,
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
