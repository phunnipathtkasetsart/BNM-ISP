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

    input.addEventListener('change', () => {
      const files = Array.from(input.files);
      const big = files.find(f => f.size > maxBytes);
      error.hidden = true;
      if (big) {
        error.textContent = `${big.name} is larger than ${maxMb} MB. Choose smaller files.`;
        error.hidden = false;
        input.value = '';
        names.textContent = 'No file chosen';
        return;
      }
      names.textContent = !files.length ? 'No file chosen'
        : files.length === 1 ? files[0].name
        : `${files.length} files: ${files.map(f => f.name).join(', ')}`;
    });

    form.addEventListener('submit', () => {
      const button = form.querySelector('[type="submit"]');
      if (!button || button.disabled) return;
      button.textContent = button.dataset.submitLabel || 'Posting…';
      // Disable on the next tick so the click still submits the form.
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
  document.querySelectorAll('[data-cc-materials]').forEach(root => {
    const sections = Array.from(root.querySelectorAll('.cc-topic'));
    const filter = root.querySelector('[data-topic-filter]');
    const toggle = root.querySelector('[data-collapse-all]');
    // Collapse all sections on initial load
  sections.forEach(s => { s.open = false; });
  if (toggle) toggle.textContent = 'Expand all';

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

