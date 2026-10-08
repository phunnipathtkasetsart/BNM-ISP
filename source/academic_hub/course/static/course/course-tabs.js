(() => {
  'use strict';

  // 1. Course Content / Manage Content main tabs
  document.querySelectorAll('[data-cc-tabs]').forEach(bar => {
    const tabs = Array.from(bar.querySelectorAll('.cc-tab'));
    const key = bar.dataset.storageKey;

    const show = (tab, focus) => {
      tabs.forEach(t => {
        const on = t === tab;
        t.setAttribute('aria-selected', on);
        t.tabIndex = on ? 0 : -1;
        const panel = document.getElementById(t.dataset.panel);
        if (panel) panel.hidden = !on;
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
    const hasError = document.querySelector('#panel-manage .errorlist');
    const start = hasError
      ? tabs.find(t => t.dataset.panel === 'panel-manage')
      : tabs.find(t => t.dataset.panel === saved) || tabs[0];
    if (start) show(start);
  });

  // 2. Compose forms: Consolidated file validation, topic checks, and double-submit prevention
  document.querySelectorAll('[data-cc-compose]').forEach(form => {
    const input = form.querySelector('input[type="file"]');
    const names = form.querySelector('[data-file-names]');
    const error = form.querySelector('[data-file-error]');
    const maxMb = Number(form.dataset.maxMb || 50);
    const maxBytes = maxMb * 1024 * 1024;

    // File selection display & size check
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

    // "Add attachment to Materials" toggle fields visibility
    const toggle = form.querySelector('[data-share-toggle]');
    if (toggle) {
      const fields = form.querySelector('[data-share-fields]');
      const box = form.querySelector('input[name="add_to_materials"]');
      const syncShare = () => {
        const hasFile = Boolean(input && input.files && input.files.length > 0);
        if (!hasFile && box) box.checked = false;
        toggle.hidden = !hasFile;
        if (fields) fields.hidden = !(hasFile && box && box.checked);
      };
      input?.addEventListener('change', syncShare);
      box?.addEventListener('change', syncShare);
      syncShare();
    }

    // Form Submit Handler
    form.addEventListener('submit', (e) => {
      const box = form.querySelector('input[name="add_to_materials"]');
      const topicSelect = form.querySelector('select[name="topic"]');
      const newTopicInput = form.querySelector('input[name="new_topic"]');

      const isMaterialUpload = form.action.includes('upload_materials') || Boolean(form.querySelector('input[name="files"]'));
      const isAddingToMaterials = box && box.checked;

      if (isMaterialUpload || isAddingToMaterials) {
        if (isMaterialUpload && input && input.files.length === 0) {
          e.preventDefault();
          if (error) {
            error.textContent = 'Please choose at least one file to upload.';
            error.hidden = false;
          }
          input.focus();
          return false;
        }

        const hasSelectedTopic = topicSelect && topicSelect.value && topicSelect.value !== '__new' && topicSelect.value !== '';
        const hasNewTopic = newTopicInput && newTopicInput.value.trim().length > 0;

        if (!hasSelectedTopic && !hasNewTopic) {
          e.preventDefault();
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

      // Prevent double submits
      const button = form.querySelector('[type="submit"]');
      if (!button || button.disabled) return;
      button.textContent = button.dataset.submitLabel || 'Posting…';
      setTimeout(() => { button.disabled = true; }, 0);
    });
  });

  // 3. "Create new topic" name box visibility toggle (matches standard Django name="topic")
  document.querySelectorAll('select[name="topic"], [data-topic-select]').forEach(select => {
    const form = select.closest('form');
    if (!form) return;
    const box = form.querySelector('[data-new-topic]');
    if (!box) return;

    const sync = () => { box.hidden = select.value !== '__new'; };
    select.addEventListener('change', sync);
    sync();
  });

  // 4. Student view: materials filter and collapse/expand
  document.querySelectorAll('[data-cc-materials]').forEach(root => {
    const sections = Array.from(root.querySelectorAll('.cc-topic'));
    const filter = root.querySelector('[data-topic-filter]');
    const toggle = root.querySelector('[data-collapse-all]');

    sections.forEach(s => { s.open = true; });
    if (toggle) toggle.textContent = 'Collapse all';

    filter?.addEventListener('change', () => {
      sections.forEach(s => { s.hidden = Boolean(filter.value) && s.dataset.topic !== filter.value; });
    });

    toggle?.addEventListener('click', () => {
      const visible = sections.filter(s => !s.hidden);
      const anyOpen = visible.some(s => s.open);
      visible.forEach(s => { s.open = !anyOpen; });
      toggle.textContent = anyOpen ? 'Expand all' : 'Collapse all';
    });
  });

  // 5. Materials list inline edit toggle
  document.querySelectorAll('[data-edit-toggle]').forEach(button => {
    const form = document.getElementById(button.dataset.editToggle);
    if (!form) return;
    button.addEventListener('click', () => {
      form.hidden = !form.hidden;
      if (!form.hidden) form.querySelector('input')?.focus();
    });
    form.querySelector('[data-edit-cancel]')?.addEventListener('click', () => {
      form.hidden = true;
      button.focus();
    });
  });

  // 6. Manage Content sub-tabs
  document.querySelectorAll('.subtab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.subtab-btn').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.manage-tab-content').forEach(p => p.hidden = true);

      btn.classList.add('active');
      try { sessionStorage.setItem('course-subtab-' + location.pathname, btn.dataset.subtab); } catch (e) {}
      const targetPanel = document.getElementById('subpanel-' + btn.dataset.subtab);
      if (targetPanel) targetPanel.hidden = false;
    });
  });

  // Restore sub-tab selection & auto-expand on error
  try {
    const savedSub = sessionStorage.getItem('course-subtab-' + location.pathname);
    const subBtn = savedSub && document.querySelector(`.subtab-btn[data-subtab="${savedSub}"]`);
    if (subBtn) subBtn.click();
  } catch (e) {}

  document.querySelectorAll('.creation-card').forEach(card => {
    if (card.querySelector('.errorlist')) {
      card.open = true;
      if (card.closest('#subpanel-materials')) {
        document.querySelector('.subtab-btn[data-subtab="materials"]')?.click();
      }
    }
  });

  // 7. Announcement post card inline edit toggle
  document.addEventListener('click', e => {
    const open = e.target.closest('[data-post-edit]');
    const cancel = e.target.closest('[data-post-edit-cancel]');
    if (!open && !cancel) return;

    const card = e.target.closest('.cc-post-card');
    if (!card) return;

    const display = card.querySelector('[data-post-display]');
    const editForm = card.querySelector('[data-post-editform]');

    if (display && editForm) {
      display.hidden = Boolean(open);
      editForm.hidden = !open;
    }
  });
})();