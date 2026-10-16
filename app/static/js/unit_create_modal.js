// Автозаполнение Make/Model/Year/Type по VIN в общей модалке «Create New Unit»
// (components/unit_create_modal.html) на страницах без work_order_details.js —
// например, на странице клиента. На странице WO не подключать: там те же ID
// обрабатывает work_order_details.js.
(function () {
  "use strict";
  var vinInput = document.getElementById("unitVinInput");
  if (!vinInput || vinInput.dataset.vinBound) return;
  vinInput.dataset.vinBound = "1";

  var spinner = document.getElementById("vinLoadingSpinner");
  var fields = {
    make: document.getElementById("unitMakeInput"),
    model: document.getElementById("unitModelInput"),
    year: document.getElementById("unitYearInput"),
    type: document.getElementById("unitTypeInput"),
  };

  function toast(msg, type) {
    if (window.appAlert) window.appAlert(msg, type || "error");
    else window.alert(msg);
  }

  function flash(cls, ms) {
    vinInput.classList.add(cls);
    setTimeout(function () { vinInput.classList.remove(cls); }, ms);
  }

  var lastVin = "";
  var timer = null;

  async function lookup() {
    var vin = String(vinInput.value || "").trim().toUpperCase();
    if (vinInput.value !== vin) vinInput.value = vin;
    if (vin.length !== 17 || vin === lastVin) return;
    lastVin = vin;
    if (spinner) spinner.style.display = "block";
    vinInput.classList.add("vin-checking");
    try {
      var res = await fetch("/work_orders/api/vin?vin=" + encodeURIComponent(vin), { headers: { "Accept": "application/json" } });
      var data = await res.json().catch(function () { return null; });
      if (!res.ok || !data || !data.ok) {
        if (res.status === 403) toast("Not authorized to look up VINs.");
        else if (data && data.message && data.error !== "vin_length") toast(data.message, "warning");
        flash("vin-invalid", 2000);
        return;
      }
      Object.keys(fields).forEach(function (key) {
        if (fields[key] && data[key]) fields[key].value = data[key];
      });
      if (data.warning) toast(data.suggested_vin ? "NHTSA suggested VIN: " + data.suggested_vin : String(data.warning), "warning");
      flash("vin-valid", 1500);
    } catch (e) {
      toast("Network error while looking up the VIN.");
    } finally {
      if (spinner) spinner.style.display = "none";
      vinInput.classList.remove("vin-checking");
    }
  }

  function schedule(ms) {
    if (timer) clearTimeout(timer);
    timer = setTimeout(lookup, ms);
  }
  vinInput.addEventListener("input", function () { schedule(500); });
  vinInput.addEventListener("blur", function () { schedule(50); });
})();
