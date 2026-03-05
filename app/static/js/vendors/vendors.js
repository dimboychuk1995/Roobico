(function() {
  const modal = document.getElementById('createVendorModal');
  const form = document.getElementById('vendorForm');
  const editingVendorId = document.getElementById('editingVendorId');
  const modalTitle = document.getElementById('createVendorModalLabel');
  const submitBtn = document.getElementById('vendorSubmitBtn');
  const activeGroup = document.getElementById('vendorActiveGroup');
  
  // Form fields
  const nameInput = document.getElementById('vendorName');
  const phoneInput = document.getElementById('vendorPhone');
  const emailInput = document.getElementById('vendorEmail');
  const websiteInput = document.getElementById('vendorWebsite');
  const pcFirstInput = document.getElementById('vendorPCFirst');
  const pcLastInput = document.getElementById('vendorPCLast');
  const addressInput = document.getElementById('vendorAddress');
  const notesInput = document.getElementById('vendorNotes');
  const isActiveInput = document.getElementById('vendorIsActive');

  // Reset form when modal opens for create
  modal?.addEventListener('show.bs.modal', function(e) {
    const triggerBtn = e.relatedTarget;
    
    // Check if this is an edit button
    if (triggerBtn && triggerBtn.classList.contains('editVendorBtn')) {
      return; // Let the edit handler deal with it
    }
    
    // Reset for create mode
    editingVendorId.value = '';
    modalTitle.textContent = 'Create new vendor';
    submitBtn.textContent = 'Create Vendor';
    activeGroup.style.display = 'none';
    form.reset();
  });

  // Handle edit button clicks
  document.addEventListener('click', async function(e) {
    const btn = e.target.closest('.editVendorBtn');
    if (!btn) return;
    
    const vendorId = btn.getAttribute('data-vendor-id');
    if (!vendorId) return;

    try {
      const res = await fetch(`/vendors/api/${encodeURIComponent(vendorId)}`, {
        method: 'GET',
        headers: { 'Accept': 'application/json' }
      });
      
      const data = await res.json();
      
      if (!res.ok || !data.ok) {
        alert(data?.error || 'Failed to load vendor data');
        return;
      }
      
      const vendor = data.item;
      
      // Set form to edit mode
      editingVendorId.value = vendor._id;
      modalTitle.textContent = 'Edit vendor';
      submitBtn.textContent = 'Update Vendor';
      activeGroup.style.display = 'block';
      
      // Populate form fields
      nameInput.value = vendor.name || '';
      phoneInput.value = vendor.phone || '';
      emailInput.value = vendor.email || '';
      websiteInput.value = vendor.website || '';
      pcFirstInput.value = vendor.primary_contact_first_name || '';
      pcLastInput.value = vendor.primary_contact_last_name || '';
      addressInput.value = vendor.address || '';
      notesInput.value = vendor.notes || '';
      isActiveInput.checked = vendor.is_active !== false;
      
    } catch (err) {
      alert('Network error while loading vendor data');
    }
  });

  // Handle form submission
  form?.addEventListener('submit', async function(e) {
    const vendorId = editingVendorId.value;
    
    // If editing, use AJAX
    if (vendorId) {
      e.preventDefault();
      
      const formData = {
        name: nameInput.value.trim(),
        phone: phoneInput.value.trim(),
        email: emailInput.value.trim(),
        website: websiteInput.value.trim(),
        primary_contact_first_name: pcFirstInput.value.trim(),
        primary_contact_last_name: pcLastInput.value.trim(),
        address: addressInput.value.trim(),
        notes: notesInput.value.trim(),
        is_active: isActiveInput.checked
      };
      
      try {
        submitBtn.disabled = true;
        submitBtn.textContent = 'Saving...';
        
        const res = await fetch(`/vendors/api/${encodeURIComponent(vendorId)}/update`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Accept': 'application/json'
          },
          body: JSON.stringify(formData)
        });
        
        const data = await res.json();
        
        if (!res.ok || !data.ok) {
          alert(data?.error || 'Failed to update vendor');
          submitBtn.disabled = false;
          submitBtn.textContent = 'Update Vendor';
          return;
        }
        
        // Success - reload page
        window.location.reload();
        
      } catch (err) {
        alert('Network error while updating vendor');
        submitBtn.disabled = false;
        submitBtn.textContent = 'Update Vendor';
      }
    }
    // Otherwise let the form submit normally for create
  });
})();
