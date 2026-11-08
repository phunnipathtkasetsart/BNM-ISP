(() => {
  'use strict';

  const questionForm = document.querySelector('#forum-question-form');
  if (questionForm) {
    const draftKey = `faq-question-draft:${window.location.pathname}`;
    const draftStatus = document.querySelector('#forum-draft-status');
    const categoryLink = questionForm.querySelector('[data-forum-category-link]');

    try {
      const savedDraft = window.sessionStorage.getItem(draftKey);
      if (savedDraft) {
        const values = JSON.parse(savedDraft);
        for (const control of questionForm.elements) {
          if (!control.name || !(control.name in values)) continue;
          if (control.type === 'checkbox') {
            control.checked = values[control.name];
          } else {
            control.value = values[control.name];
          }
        }
        window.sessionStorage.removeItem(draftKey);
      }

      categoryLink?.addEventListener('click', () => {
        const values = {};
        for (const control of questionForm.elements) {
          if (!control.name || control.name === 'csrfmiddlewaretoken') continue;
          values[control.name] =
            control.type === 'checkbox' ? control.checked : control.value;
        }
        window.sessionStorage.setItem(draftKey, JSON.stringify(values));
      });
    } catch (error) {
      if (draftStatus) {
        draftStatus.classList.remove('sr-only');
        draftStatus.textContent =
          'Your browser could not preserve this question draft while creating a category.';
      }
      console.error('Unable to preserve FAQ question draft.', error);
    }
  }

  document.querySelectorAll('[data-faq-description]').forEach(description => {
    const toggle = description.nextElementSibling;
    if (!toggle?.matches('[data-faq-description-toggle]')) return;
    const details = description.closest('details');
    const updateToggle = () => {
      if (details && !details.open) return;
      toggle.hidden = description.scrollHeight <= description.clientHeight + 1;
    };
    details?.addEventListener('toggle', updateToggle);
    updateToggle();
    toggle.addEventListener('click', () => {
      const expanded = description.classList.toggle(
        'qa__description--expanded',
      );
      toggle.textContent = expanded ? 'Show less' : 'Show more';
      toggle.setAttribute('aria-expanded', String(expanded));
    });
  });

  const form = document.querySelector('#faq-filter-form');
  const search = form?.querySelector('[name="search"]');
  const status = document.querySelector('#faq-live-status');
  if (!form || !search || !status) return;

  let searchTimer;
  let activeRequest;

  const updateResults = async () => {
    const parameters = new URLSearchParams(new FormData(form));
    parameters.delete('page');
    const url = new URL(form.action, window.location.href);
    url.search = parameters.toString();
    activeRequest?.abort();
    activeRequest = new AbortController();
    document.querySelector('#forum-results')?.setAttribute('aria-busy', 'true');
    status.textContent = 'Searching FAQ questions.';

    try {
      const response = await fetch(url, {
        credentials: 'same-origin',
        headers: {
          Accept: 'text/html',
          'X-Requested-With': 'XMLHttpRequest',
        },
        signal: activeRequest.signal,
      });
      if (response.redirected) {
        window.location.assign(response.url);
        return;
      }
      if (!response.ok) {
        throw new Error(`FAQ search failed (${response.status}).`);
      }
      const html = await response.text();
      const documentFragment = new DOMParser().parseFromString(html, 'text/html');
      const nextResults = documentFragment.querySelector('#forum-results');
      const currentResults = document.querySelector('#forum-results');
      if (!nextResults || !currentResults) {
        throw new Error('FAQ search returned an invalid results response.');
      }
      currentResults.replaceWith(nextResults);
      window.history.replaceState({}, '', url);
      status.textContent =
        nextResults.querySelector('.forum-results__count')?.textContent.trim() ||
        'FAQ results updated.';
    } catch (error) {
      if (error.name === 'AbortError') return;
      status.textContent = 'Live search failed. Loading the results normally.';
      form.requestSubmit();
    }
  };

  search.addEventListener('input', () => {
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(updateResults, 250);
  });

  form.addEventListener('change', event => {
    if (event.target === search) return;
    updateResults();
  });
})();
