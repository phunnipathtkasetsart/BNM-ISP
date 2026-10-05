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
})();



