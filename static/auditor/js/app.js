document.addEventListener("DOMContentLoaded", function () {
  const dropzone = document.getElementById("dropzone");
  const fileInput = document.getElementById("id_bill_file");
  const fileInfoPill = document.getElementById("file-selection-info");
  const selectedFilename = document.getElementById("selected-filename");
  const selectedFilesize = document.getElementById("selected-filesize");

  if (dropzone && fileInput) {
    dropzone.addEventListener("click", () => fileInput.click());
    fileInput.addEventListener("change", () => {
      if (fileInput.files && fileInput.files[0] && fileInfoPill) {
        selectedFilename.textContent = fileInput.files[0].name;
        selectedFilesize.textContent = (fileInput.files[0].size / 1024).toFixed(1) + " KB";
        fileInfoPill.classList.remove("hidden");
      }
    });
  }

  const tbody = document.getElementById("line-items-tbody");
  const addRowBtn = document.getElementById("add-line-item-btn");
  const expectedSubtotalEl = document.getElementById("live-expected-subtotal");
  const billedSubtotalEl = document.getElementById("live-billed-subtotal");
  const copySubtotalBtn = document.getElementById("copy-subtotal-to-grand-btn");
  const grandTotalInput = document.getElementById("id_reported_grand_total");

  function recalculateLiveTotals() {
    if (!tbody) return;
    let sumExpected = 0;
    let sumBilled = 0;
    tbody.querySelectorAll(".line-item-row").forEach((row) => {
      const qty = parseFloat(row.querySelector(".calc-qty")?.value || "");
      const price = parseFloat(row.querySelector(".calc-price")?.value || "");
      const billed = parseFloat(row.querySelector(".calc-total")?.value || "");
      if (!isNaN(qty) && !isNaN(price)) sumExpected += qty * price;
      if (!isNaN(billed)) sumBilled += billed;
    });
    if (expectedSubtotalEl) expectedSubtotalEl.textContent = "₹" + sumExpected.toFixed(2);
    if (billedSubtotalEl) {
      billedSubtotalEl.textContent = "₹" + sumBilled.toFixed(2);
      billedSubtotalEl.dataset.rawBilled = sumBilled.toFixed(2);
    }
  }

  function attachRowListeners(row) {
    row.querySelectorAll("input").forEach((inp) => inp.addEventListener("input", recalculateLiveTotals));
    const removeBtn = row.querySelector(".remove-row-btn");
    if (removeBtn) {
      removeBtn.addEventListener("click", () => {
        if (tbody.querySelectorAll(".line-item-row").length > 1) {
          row.remove();
          recalculateLiveTotals();
        }
      });
    }
  }

  if (tbody) {
    tbody.querySelectorAll(".line-item-row").forEach(attachRowListeners);
    recalculateLiveTotals();
  }

  if (addRowBtn && tbody) {
    addRowBtn.addEventListener("click", () => {
      const tr = document.createElement("tr");
      tr.className = "line-item-row";
      tr.innerHTML = `
        <td><input type="text" name="item_description[]" placeholder="Item description" required class="table-input"></td>
        <td><input type="number" step="0.01" name="item_quantity[]" value="1" class="table-input calc-qty"></td>
        <td><input type="text" name="item_unit[]" value="unit" class="table-input"></td>
        <td><input type="number" step="0.01" name="item_unit_price[]" placeholder="0.00" class="table-input calc-price"></td>
        <td><input type="number" step="0.01" name="item_billed_total[]" placeholder="0.00" class="table-input calc-total"></td>
        <td><input type="text" name="item_code[]" placeholder="Optional" class="table-input"></td>
        <td><button type="button" class="btn-icon remove-row-btn">&times;</button></td>
      `;
      tbody.appendChild(tr);
      attachRowListeners(tr);
      recalculateLiveTotals();
    });
  }

  if (copySubtotalBtn && grandTotalInput && billedSubtotalEl) {
    copySubtotalBtn.addEventListener("click", () => {
      grandTotalInput.value = billedSubtotalEl.dataset.rawBilled || "0.00";
    });
  }
});
