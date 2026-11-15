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
    const maxMb = Number(form.dataset.maxMb || 25);
    const maxBytes = maxMb * 1024 * 1024;
    const MAX_FILES = 10; // Maximum allowed files limit

    const ALLOWED_EXTENSIONS = [
      'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 
      'txt', 'csv', 'rtf', 'jpg', 'jpeg', 'png', 'gif', 
      'webp', 'mp4', 'mp3', 'zip', 'rar'
    ];

    const BLOCKED_EXTENSIONS = [
      'exe', 'bat', 'cmd', 'sh', 'com', 'msi', 'scr', 'pif', 
      'application', 'gadget', 'ps1', 'vbs', 'js', 'jar', 'php', 'py'
    ];

    // Vertical Block List Container for selected file widgets
    let previewList = form.querySelector('[data-file-preview]');
    if (!previewList && input) {
      previewList = document.createElement('ul');
      previewList.className = 'cc-file-preview-list';
      previewList.style.maxHeight = '280px';
      previewList.style.overflowY = 'auto';
      const container = input.closest('.cc-field') || form;
      container.appendChild(previewList);
    }

    // Persistent file store & error tracking flag
    let accumulatedFiles = new DataTransfer();
    let hasBlockingError = false;

    const getExt = name => {
      const hasDot = name.includes('.');
      return hasDot ? name.split('.').pop().toLowerCase() : '';
    };

    const formatBytes = bytes => {
      if (bytes < 1024) return bytes + ' B';
      if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
      return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
    };

    // Render file widgets using course-content.css styles & update pill label
    const renderAndSync = () => {
      if (!input) return;
      input.files = accumulatedFiles.files;
      const count = accumulatedFiles.files.length;
      const filesArr = Array.from(accumulatedFiles.files);

      // Clean pill text display logic:
      // 0 -> "No file chosen"
      // 1 -> "filename.ext"
      // 2 -> "file1.ext, file2.ext"
      // > 2 -> "selected (n) files"
      if (names) {
        if (count === 0) {
          names.textContent = 'No file chosen';
        } else if (count === 1) {
          names.textContent = filesArr[0].name;
        } else if (count === 2) {
          names.textContent = `${filesArr[0].name}, ${filesArr[1].name}`;
        } else {
          names.textContent = `selected (${count}) files`;
        }
      }

      previewList.innerHTML = '';

      filesArr.forEach((file, index) => {
        const li = document.createElement('li');
        const ext = getExt(file.name);
        
        li.innerHTML = `
          <div class="cc-chip cc-chip--edit">
            <span class="cc-ext cc-ext--${ext}" aria-hidden="true">${ext.toUpperCase() || 'FILE'}</span>
            <div class="cc-chip__main">
              <span class="cc-chip__name">${file.name}</span>
              <span class="cc-chip__size">${formatBytes(file.size)}</span>
            </div>
            <button type="button" class="cc-chip__remove" aria-label="Discard ${file.name}" title="Discard file">
              <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <line x1="18" y1="6" x2="6" y2="18"></line>
                <line x1="6" y1="6" x2="18" y2="18"></line>
              </svg>
            </button>
          </div>
        `;

        // Discard individual file handler
        li.querySelector('.cc-chip__remove').addEventListener('click', (e) => {
          e.preventDefault();
          const nextDt = new DataTransfer();
          Array.from(accumulatedFiles.files).forEach((f, i) => {
            if (i !== index) nextDt.items.add(f);
          });
          accumulatedFiles = nextDt;
          validateAndSync();
        });

        previewList.appendChild(li);
      });

      syncShare();
    };

    // Validate size and extensions
    const validateAndSync = () => {
      hasBlockingError = false;
      if (error) error.hidden = true;
      const files = Array.from(accumulatedFiles.files);

      if (files.length === 0) {
        renderAndSync();
        return;
      }

      // 1. Check cumulative total size
      const totalBytes = files.reduce((sum, f) => sum + f.size, 0);
      if (totalBytes > maxBytes) {
        hasBlockingError = true;
        if (error) {
          const totalMb = (totalBytes / (1024 * 1024)).toFixed(1);
          error.textContent = `Total size of files (${totalMb} MB) exceeds limit of ${maxMb} MB. Choose smaller files.`;
          error.hidden = false;
        }
      }

      // 2. Check extension whitelist & blacklist
      for (const file of files) {
        const ext = getExt(file.name);
        if (!ext || BLOCKED_EXTENSIONS.includes(ext) || !ALLOWED_EXTENSIONS.includes(ext)) {
          hasBlockingError = true;
          if (error) {
            error.textContent = `"${file.name}" is an executable or unsupported file type.`;
            error.hidden = false;
          }
        }
      }

      renderAndSync();
    };

    // File input selection event
    form.addEventListener('change', (e) => {
      if (e.target !== input) return;
      e.stopImmediatePropagation();

      const newlyPicked = Array.from(input.files);
      if (!newlyPicked.length) return;

      let countExceeded = false;
      let duplicateFound = false;

      for (const file of newlyPicked) {
        // Enforce 10 files max limit
        if (accumulatedFiles.files.length >= MAX_FILES) {
          countExceeded = true;
          break;
        }

        const isDuplicate = Array.from(accumulatedFiles.files).some(
          existing => existing.name === file.name && existing.size === file.size
        );

        if (!isDuplicate) {
          accumulatedFiles.items.add(file);
        } else {
          duplicateFound = true;
        }
      }

      validateAndSync();

      // Non-blocking red warning display for excess files or duplicate files
      if (countExceeded && error && !hasBlockingError) {
        error.textContent = `Maximum limit of ${MAX_FILES} files reached. Excess files were not added.`;
        error.hidden = false;
      } else if (duplicateFound && error && !hasBlockingError) {
        error.textContent = `Duplicate file(s) already selected were skipped.`;
        error.hidden = false;
      }
    });

    // "Add attachment to Materials" toggle visibility
    const toggle = form.querySelector('[data-share-toggle]');
    const fields = form.querySelector('[data-share-fields]');
    const box = form.querySelector('input[name="add_to_materials"]');
    
    const syncShare = () => {
      if (!toggle) return;
      const hasFile = Boolean(accumulatedFiles.files.length > 0);
      if (!hasFile && box) box.checked = false;
      toggle.hidden = !hasFile;
      if (fields) fields.hidden = !(hasFile && box && box.checked);
    };

    box?.addEventListener('change', syncShare);

    // Form submit handler
    form.addEventListener('submit', (e) => {
      // Only block submission for actual errors (over size limit or invalid extensions)
      if (hasBlockingError) {
        e.preventDefault();
        return false;
      }

      const isMaterialUpload = form.action.includes('upload_materials') || Boolean(form.querySelector('input[name="files"]'));
      const isAddingToMaterials = box && box.checked;
      const topicSelect = form.querySelector('select[name="topic"]');
      const newTopicInput = form.querySelector('input[name="new_topic"]');

      if (isMaterialUpload || isAddingToMaterials) {
        if (isMaterialUpload && accumulatedFiles.files.length === 0) {
          e.preventDefault();
          if (error) {
            error.textContent = 'Please choose at least one file to upload.';
            error.hidden = false;
          }
          input?.focus();
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

    // Cancel on the edit form calls form.reset(): drop files picked but not saved.
    form.addEventListener('reset', () => {
      accumulatedFiles = new DataTransfer();
      validateAndSync();
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