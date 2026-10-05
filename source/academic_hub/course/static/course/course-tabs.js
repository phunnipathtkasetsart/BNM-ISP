(() => {
  'use strict';

  // Course Content / Manage Content tabs. Without this script both panels
  // stay visible, so nothing is lost if JavaScript fails to load.
  document.querySelectorAll('[data-cc-tabs]').forEach(bar => {
    const tabs = Array.from(bar.querySelectorAll('[role="tab"]'));
    const key = bar.dataset.storageKey;
    const panels = tabs.map(tab => document.getElementById(tab.dataset.panel));

    const remember = id => { try { sessionStorage.setItem(key, id); } catch (_) {} };
    const recall = () => { try { return sessionStorage.getItem(key); } catch (_) { return null; } };

    function show(tab, focus) {
      tabs.forEach((t, i) => {
        const on = t === tab;
        t.setAttribute('aria-selected', String(on));
        t.tabIndex = on ? 0 : -1;
        panels[i].hidden = !on;
      });
      remember(tab.id);
      if (focus) tab.focus();
    }

    tabs.forEach((tab, i) => {
      tab.addEventListener('click', () => show(tab, false));
      tab.addEventListener('keydown', event => {
        const step = { ArrowRight: 1, ArrowLeft: -1 }[event.key];
        if (step) {
          event.preventDefault();
          show(tabs[(i + step + tabs.length) % tabs.length], true);
        } else if (event.key === 'Home' || event.key === 'End') {
          event.preventDefault();
          show(tabs[event.key === 'Home' ? 0 : tabs.length - 1], true);
        }
      });
    });

    // After posting, the page reloads; stay on the tab the lecturer was using.
    const saved = tabs.find(t => t.id === recall());
    show(saved || tabs[0], false);
  });

  // Compose form: show the chosen file, refuse oversize files early, and
  // stop double submits on slow uploads. The server still enforces the limit.
  document.querySelectorAll('[data-cc-compose]').forEach(form => {
    const input = form.querySelector('input[type="file"]');
    const names = form.querySelector('[data-file-names]');
    const error = form.querySelector('[data-file-error]');
    const maxBytes = Number(form.dataset.maxMb || 50) * 1024 * 1024;

    input.addEventListener('change', () => {
      const file = input.files[0];
      error.hidden = true;
      if (file && file.size > maxBytes) {
        error.textContent = `${file.name} is larger than ${form.dataset.maxMb} MB. Choose a smaller file.`;
        error.hidden = false;
        input.value = '';
        names.textContent = 'No file chosen';
        return;
      }
      names.textContent = file ? file.name : 'No file chosen';
    });

    form.addEventListener('submit', () => {
      const button = form.querySelector('[type="submit"]');
      if (!button || button.disabled) return;
      button.dataset.originalLabel = button.textContent;
      button.textContent = button.dataset.submitLabel || 'Posting…';
      // Disable on the next tick so the click still submits the form.
      setTimeout(() => { button.disabled = true; }, 0);
    });
  });
})();
