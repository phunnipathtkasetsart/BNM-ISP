(() => {
  'use strict';
  document.querySelectorAll('[data-dialog]').forEach(link => {
    link.addEventListener('click', event => {
      const dialog = document.getElementById(link.dataset.dialog);
      if (!dialog || !dialog.showModal) return;
      event.preventDefault();
      dialog.showModal();
    });
  });
  document.querySelectorAll('[data-close-dialog]').forEach(button => {
    button.addEventListener('click', () => button.closest('dialog').close());
  });
  document.querySelectorAll('[data-confirm]').forEach(form => {
    form.addEventListener('submit', event => {
      if (!window.confirm(form.dataset.confirm)) event.preventDefault();
    });
  });
  document.querySelectorAll('[data-copy-code]').forEach(button => {
    button.addEventListener('click', async () => {
      const status = document.getElementById('copy-status');
      try {
        await navigator.clipboard.writeText(button.dataset.copyCode);
        status.textContent = 'Copied!';
      } catch (_) {
        status.textContent = 'Select the code above to copy it.';
      }
    });
  });
  const grid = document.querySelector('[data-course-grid]');
  const viewButtons = document.querySelectorAll('[data-view]');
  if (grid && viewButtons.length) {
    const KEY = 'course:view';
    const apply = view => {
      grid.classList.toggle('is-list', view === 'list');
      viewButtons.forEach(button => {
        const on = button.dataset.view === view;
        button.classList.toggle('is-active', on);
        button.setAttribute('aria-pressed', String(on));
      });
    };
    let saved = 'grid';
    try { saved = localStorage.getItem(KEY) === 'list' ? 'list' : 'grid'; } catch (_) {}
    apply(saved);
    viewButtons.forEach(button => {
      button.addEventListener('click', () => {
        apply(button.dataset.view);
        try { localStorage.setItem(KEY, button.dataset.view); } catch (_) {}
      });
    });
  }
})();



(() => {
  'use strict';
  document.querySelectorAll('[data-numeric-section]').forEach(input => {
    input.addEventListener('input', () => {
      const caret = input.selectionStart;
      const before = input.value;
      const cleaned = before.replace(/[^0-9]/g, '');
      if (cleaned === before) return;
      input.value = cleaned;
      if (caret !== null) {
        const position = before.slice(0, caret).replace(/[^0-9]/g, '').length;
        input.setSelectionRange(position, position);
      }
    });
  });

  document.querySelectorAll('[data-dialog]').forEach(link => {
    link.addEventListener('click', event => {
      const dialog = document.getElementById(link.dataset.dialog);
      if (!dialog || !dialog.showModal) return;
      event.preventDefault();
      dialog.showModal();
    });
  });

  document.querySelectorAll('[data-close-dialog]').forEach(button => {
    button.addEventListener('click', () => button.closest('dialog').close());
  });

  document.querySelectorAll('[data-confirm]').forEach(form => {
    form.addEventListener('submit', event => {
      if (!window.confirm(form.dataset.confirm)) event.preventDefault();
    });
  });

  document.querySelectorAll('[data-copy-code]').forEach(button => {
    button.addEventListener('click', async () => {
      const status = document.getElementById('copy-status');
      try {
        await navigator.clipboard.writeText(button.dataset.copyCode);
        status.textContent = 'Copied!';
      } catch (_) {
        status.textContent = 'Select the code above to copy it.';
      }
    });
  });

  // Admin Live Course Search (No page reload)
  const searchInput = document.getElementById('admin-course-search');
  if (searchInput) {
    const cards = document.querySelectorAll('.course-card');
    searchInput.addEventListener('input', () => {
      const query = searchInput.value.toLowerCase().trim();
      cards.forEach(card => {
        const text = card.textContent.toLowerCase();
        card.style.display = text.includes(query) ? '' : 'none';
      });
    });
  }
})();


document.addEventListener("DOMContentLoaded", () => {
  const toggleBtn = document.getElementById("nav-toggle");
  const navMenu = document.getElementById("nav-menu");

  if (toggleBtn && navMenu) {
    toggleBtn.addEventListener("click", () => {
      const isOpen = navMenu.classList.toggle("is-open");
      toggleBtn.setAttribute("aria-expanded", isOpen);
    });
  }
});



// Post Edit Form Toggle & Attachment Removal JS
document.addEventListener('DOMContentLoaded', () => {
  document.addEventListener('click', (e) => {
    // Open edit post form
    const editBtn = e.target.closest('[data-post-edit]');
    if (editBtn) {
      const card = editBtn.closest('.cc-post-card');
      if (!card) return;
      const display = card.querySelector('[data-post-display]');
      const form = card.querySelector('[data-post-editform]');
      if (display && form) {
        display.hidden = true;
        form.hidden = false;
      }
      return;
    }

    // Cancel editing
  const cancelBtn = e.target.closest('[data-post-edit-cancel]');
  if (cancelBtn) {
    const card = cancelBtn.closest('.cc-post-card');
    if (!card) return;
    const display = card.querySelector('[data-post-display]');
    const form = card.querySelector('[data-post-editform]');

    if (display && form) {
      // Reset HTML form fields
      form.reset();

      // Restore attachment chip if an attachment originally existed
      const attContainer = form.querySelector('[data-attachment-container]');
      const removeInput = form.querySelector('[data-remove-attachment-input]');
      const picker = form.querySelector('[data-file-picker]');
      const nameSpan = form.querySelector('[data-file-names]');

      if (attContainer) {
        attContainer.style.display = '';
        if (removeInput) removeInput.value = 'false';
        if (picker) picker.hidden = true;
      } else if (picker) {
        picker.hidden = false;
      }

      if (nameSpan) {
        nameSpan.textContent = 'No file chosen';
        nameSpan.style.color = '#60776c';
      }

      // Hide edit form and show post content
      display.hidden = false;
      form.hidden = true;
    }
    return;
  }

    // Discard attachment (X button)
    const removeAttBtn = e.target.closest('[data-remove-attachment-btn]');
    if (removeAttBtn) {
      const container = removeAttBtn.closest('[data-attachment-container]');
      const form = removeAttBtn.closest('form');
      if (container) {
        const input = container.querySelector('[data-remove-attachment-input]');
        if (input) input.value = 'true';
        container.style.display = 'none';
      }
      if (form) {
        const picker = form.querySelector('[data-file-picker]');
        if (picker) picker.hidden = false;
      }
      return;
    }
  });

  // Display selected filename when choosing a new file
  document.addEventListener('change', (e) => {
    const fileInput = e.target.closest('input[type="file"]');
    if (!fileInput) return;
    const filebox = fileInput.closest('.cc-filebox, .cc-field');
    if (!filebox) return;
    const nameSpan = filebox.querySelector('[data-file-names]');
    if (nameSpan) {
      if (fileInput.files && fileInput.files.length > 0) {
        const names = Array.from(fileInput.files).map(f => f.name).join(', ');
        nameSpan.textContent = names;
        nameSpan.style.color = '#183e35';
      } else {
        nameSpan.textContent = 'No file chosen';
        nameSpan.style.color = '#60776c';
      }
    }
  });
});