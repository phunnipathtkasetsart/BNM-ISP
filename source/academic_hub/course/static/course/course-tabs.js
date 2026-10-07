(() => {
  'use strict';
  // Course Content / Manage Content tabs.
  document.querySelectorAll('[data-cc-tabs]').forEach(bar => {
    const tabs = Array.from(bar.querySelectorAll('.cc-tab'));
    const key = bar.dataset.storageKey;
    const show = (tab, focus) => {
      tabs.forEach(t => {
        const on = t === tab;
        t.setAttribute('aria-selected', on);
        t.tabIndex = on ? 0 : -1;
        document.getElementById(t.dataset.panel).hidden = !on;
      });
      if (focus) tab.focus();
      try { sessionStorage.setItem(key, tab.dataset.panel); } catch (e) {}
    };
    tabs.forEach((tab, i) => {
      tab.addEventListener('click', () => show(tab));
      tab.addEventListener('keydown', e => {
        const step = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
        if (!step) return;
        e.preventDefault();
        show(tabs[(i + step + tabs.length) % tabs.length], true);
      });
    });
    let saved = null;
    try { saved = sessionStorage.getItem(key); } catch (e) {}
    // A failed upload re-renders the page: reopen Manage so the error shows.
    const hasError = document.querySelector('#panel-manage .errorlist');
    const start = hasError
      ? tabs.find(t => t.dataset.panel === 'panel-manage')
      : tabs.find(t => t.dataset.panel === saved) || tabs[0];
    show(start);
  });

    // Compose forms: show chosen file names, refuse oversize files early, and
  // stop double submits on slow uploads. The server still enforces the limit.
  document.querySelectorAll('[data-cc-compose]').forEach(form => {
  const input = form.querySelector('input[type="file"]');
  const names = form.querySelector('[data-file-names]');
  const error = form.querySelector('[data-file-error]');
  const maxMb = Number(form.dataset.maxMb || 50);
  const maxBytes = maxMb * 1024 * 1024;

  // File selection change handler
  input?.addEventListener('change', () => {
    const files = Array.from(input.files);
    const big = files.find(f => f.size > maxBytes);
    if (error) error.hidden = true;
    
    if (big) {
      if (error) {
        error.textContent = `${big.name} is larger than ${maxMb} MB. Choose smaller files.`;
        error.hidden = false;
      }
      input.value = '';
      if (names) names.textContent = 'No file chosen';
      return;
    }
    
    if (names) {
      names.textContent = !files.length ? 'No file chosen'
        : files.length === 1 ? files[0].name
        : `${files.length} files: ${files.map(f => f.name).join(', ')}`;
    }
  });

  // PRE-SUBMIT VALIDATION: Prevents losing selected files on invalid submits
  form.addEventListener('submit', (e) => {
    const box = form.querySelector('input[name="add_to_materials"]');
    const topicSelect = form.querySelector('select[name="topic"]');
    const newTopicInput = form.querySelector('input[name="new_topic"]');
    
    // Check if this form requires topic validation:
    // 1. Upload Material form (always requires a topic)
    // 2. Announcement form with "Add attachment to Materials" checked
    const isMaterialUpload = form.action.includes('upload_materials') || Boolean(form.querySelector('input[name="files"]'));
    const isAddingToMaterials = box && box.checked;

    if (isMaterialUpload || isAddingToMaterials) {
      // 1. Validate File Choice (for Material Upload)
      if (isMaterialUpload && input && input.files.length === 0) {
        e.preventDefault();
        if (error) {
          error.textContent = 'Please choose at least one file to upload.';
          error.hidden = false;
        }
        input.focus();
        return false;
      }

      // 2. Validate Topic Choice
      const hasSelectedTopic = topicSelect && topicSelect.value && topicSelect.value !== '__new' && topicSelect.value !== '';
      const hasNewTopic = newTopicInput && newTopicInput.value.trim().length > 0;

      if (!hasSelectedTopic && !hasNewTopic) {
        e.preventDefault(); // Stop form submission so chosen files are NOT lost

        if (error) {
          error.textContent = 'Choose a topic category or enter a new topic name.';
          error.hidden = false;
        }

        if (topicSelect && topicSelect.value === '__new' && newTopicInput) {
          newTopicInput.focus();
        } else if (topicSelect) {
          topicSelect.focus();
        }
        return false;
      }
    }

    // Disable button to prevent double-submitting
    const button = form.querySelector('[type="submit"]');
    if (!button || button.disabled) return;
    button.textContent = button.dataset.submitLabel || 'Posting…';
    setTimeout(() => { button.disabled = true; }, 0);
  });
});
  


  // "Create new topic" name box shows only when that option is selected.
  document.querySelectorAll('[data-topic-select]').forEach(select => {
        const box = select.closest('form').querySelector('[data-new-topic]');
    const sync = () => { box.hidden = select.value !== '__new'; };
    select.addEventListener('change', sync);
    sync();
  });

  // Student view: topic filter and collapse/expand all.
  // Student view: topic filter and collapse/expand all.
  document.querySelectorAll('[data-cc-materials]').forEach(root => {
    const sections = Array.from(root.querySelectorAll('.cc-topic'));
    const filter = root.querySelector('[data-topic-filter]');
    const toggle = root.querySelector('[data-collapse-all]');

    // Expand all sections on initial load
    sections.forEach(s => { s.open = true; });
    if (toggle) toggle.textContent = 'Collapse all';

    filter.addEventListener('change', () => {
      sections.forEach(s => { s.hidden = Boolean(filter.value) && s.dataset.topic !== filter.value; });
    });

    toggle.addEventListener('click', () => {
      const visible = sections.filter(s => !s.hidden);
      const anyOpen = visible.some(s => s.open);
      visible.forEach(s => { s.open = !anyOpen; });
      toggle.textContent = anyOpen ? 'Expand all' : 'Collapse all';
    });
  });

  // Manage list: pencil opens the inline edit form.
  document.querySelectorAll('[data-edit-toggle]').forEach(button => {
    const form = document.getElementById(button.dataset.editToggle);
    button.addEventListener('click', () => {
      form.hidden = !form.hidden;
      if (!form.hidden) form.querySelector('input').focus();
    });
    form.querySelector('[data-edit-cancel]').addEventListener('click', () => {
      form.hidden = true;
      button.focus();
    });
  });


  // Announcement form: offer "Add to Materials" only when a file is attached.
  document.querySelectorAll('[data-cc-compose]').forEach(form => {
    const toggle = form.querySelector('[data-share-toggle]');
    if (!toggle) return;
    const fields = form.querySelector('[data-share-fields]');
    const input = form.querySelector('input[type="file"]');
    const box = form.querySelector('input[name="add_to_materials"]');
    const sync = () => {
      const has = input.files.length > 0;
      if (!has) box.checked = false;
      toggle.hidden = !has;
      fields.hidden = !(has && box.checked);
    };
    input.addEventListener('change', sync);
    box.addEventListener('change', sync);
    sync();
  });

// Manage Content Sub-Tabs
  document.querySelectorAll('.subtab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      // Remove active class from all sub-tab buttons
      document.querySelectorAll('.subtab-btn').forEach(b => b.classList.remove('active'));
      // Hide all sub-panels
      document.querySelectorAll('.manage-tab-content').forEach(p => p.hidden = true);
      
      // Activate clicked button and show corresponding panel
      btn.classList.add('active');
      try { sessionStorage.setItem('course-subtab-' + location.pathname, btn.dataset.subtab); } catch (e) {}
      const targetPanel = document.getElementById('subpanel-' + btn.dataset.subtab);
      if (targetPanel) targetPanel.hidden = false;
    });
  });

  // Auto-expand creation card if the server returns form validation errors
       try {
       const savedSub = sessionStorage.getItem('course-subtab-' + location.pathname);
       const subBtn = savedSub && document.querySelector(`.subtab-btn[data-subtab="${savedSub}"]`);
       if (subBtn) subBtn.click();
     } catch (e) {}
  document.querySelectorAll('.creation-card').forEach(card => {
    if (card.querySelector('.errorlist')) {
      card.open = true;
      
      // If the error is in the materials form, switch to the materials tab automatically
      if (card.closest('#subpanel-materials')) {
        document.querySelector('.subtab-btn[data-subtab="materials"]').click();
      }
    }
  });
  

     // Announcement edit: swap the card content for its edit form.
     document.addEventListener('click', e => {
       const open = e.target.closest('[data-post-edit]');
       const cancel = e.target.closest('[data-post-edit-cancel]');
       if (!open && !cancel) return;
       const card = e.target.closest('.cc-post-card');
       card.querySelector('[data-post-display]').hidden = Boolean(open);
       card.querySelector('[data-post-editform]').hidden = !open;
     });




     
})();

