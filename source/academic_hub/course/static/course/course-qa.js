(() => {
  'use strict';

  const root = document.querySelector('[data-course-qa]');
  if (!root) return;

  const base = root.dataset.apiBase;
  const list = root.querySelector('[data-qa-list]');
  const pagination = root.querySelector('[data-qa-pagination]');
  const message = root.querySelector('[data-qa-message]');
  const thread = root.querySelector('[data-qa-thread]');
  const threadContent = root.querySelector('[data-qa-thread-content]');
  const askDialog = root.querySelector('[data-qa-ask-dialog]');
  const searchInput = root.querySelector('[data-qa-search]');
  const statusFilter = root.querySelector('[data-qa-status]');
  const pageSize = 5;
  const state = { page: 1, count: 0, questionId: null, search: '', status: '' };
  let searchTimer;

  const element = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };

  const actionButton = (text, action, className = 'qa-action') => {
    const node = element('button', className, text);
    node.type = 'button';
    node.dataset.qaAction = action;
    return node;
  };

  const dateText = value => {
    if (!value) return '';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '';
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: 'medium',
      timeStyle: 'short',
      timeZone: 'Asia/Bangkok',
    }).format(date);
  };

  const setMessage = (text = '', kind = '') => {
    message.textContent = text;
    message.dataset.kind = kind;
  };

  const request = async (url, options = {}) => {
    const headers = new Headers(options.headers || {});
    headers.set('Accept', 'application/json');
    if (options.body) {
      headers.set('Content-Type', 'application/json');
      const token = root.querySelector('[name="csrfmiddlewaretoken"]')?.value;
      if (token) headers.set('X-CSRFToken', token);
    }
    const response = await fetch(url, {
      credentials: 'same-origin',
      ...options,
      headers,
    });
    if (response.redirected) {
      window.location.assign(response.url);
      throw new Error('Your session was redirected. Please sign in and try again.');
    }
    const text = await response.text();
    let data;
    try {
      data = text ? JSON.parse(text) : {};
    } catch (_) {
      throw new Error('The server returned an unexpected response. Please refresh and try again.');
    }
    if (!response.ok) {
      const fieldErrors = Object.values(data.errors || {}).flat().map(item => item.message);
      throw new Error(fieldErrors[0] || data.error || 'The request could not be completed.');
    }
    return data;
  };

  const meta = (author, role, date, edited) => {
    const node = element('p', 'qa-meta');
    node.append(element('strong', '', author || 'Former member'));
    if (role) node.append(element('span', 'qa-role', role));
    if (date) node.append(document.createTextNode(` · ${dateText(date)}`));
    if (edited) node.append(element('span', 'qa-edited', ' · edited'));
    return node;
  };

  const addQuestionActions = (container, question) => {
    if (question.can_pin) {
      container.append(actionButton(question.is_pinned ? 'Unpin' : 'Pin', 'pin'));
    }
    if (question.can_edit) container.append(actionButton('Edit', 'edit-question'));
    if (question.can_archive) {
      container.append(actionButton('Archive', 'archive-question', 'qa-action qa-action--danger'));
    }
    if (question.archived_at && root.dataset.isDepartment === 'true') {
      container.append(actionButton('Restore question', 'restore-question'));
    }
  };

  const renderList = questions => {
    list.replaceChildren();
    if (!questions.length) {
      const empty = element('p', 'qa-empty', state.search
        ? 'No matching questions. Try another search.'
        : state.status === 'archived'
          ? 'No archived questions.'
          : 'No questions yet. Ask the first one!');
      list.append(empty);
      return;
    }

    questions.forEach(question => {
      const row = element('article',
        `qa-result${question.is_pinned || question.has_verified_answer ? ' is-pinned' : ''}`);
      const open = element('button', 'qa-result__open');
      open.type = 'button';
      open.dataset.openQuestion = question.id;
      const title = element('span', 'qa-result__title', question.title);
      const details = element('span', 'qa-result__meta',
        `${question.author_name || 'Former member'} · ${question.answer_count} ${question.answer_count === 1 ? 'answer' : 'answers'}`);
      open.append(title, details);
      row.append(open);
      const badges = element('span', 'qa-result__badges');
      if (question.is_pinned) badges.append(element('span', 'qa-tag qa-tag--pinned', 'Pinned'));
      if (question.has_verified_answer) {
        badges.append(element('span', 'qa-tag qa-tag--verified', '📌 Verified answer'));
      }
      if (question.archived_at) badges.append(element('span', 'qa-tag qa-tag--archived', 'Archived'));
      if (badges.childElementCount) row.append(badges);
      list.append(row);
    });
  };

  const renderPagination = () => {
    pagination.replaceChildren();
    const pages = Math.ceil(state.count / pageSize);
    if (pages < 2) return;
    const previous = element('button', 'qa-page-button', '‹');
    previous.type = 'button';
    previous.disabled = state.page <= 1;
    previous.dataset.page = state.page - 1;
    previous.setAttribute('aria-label', 'Previous page');
    const next = element('button', 'qa-page-button', '›');
    next.type = 'button';
    next.disabled = state.page >= pages;
    next.dataset.page = state.page + 1;
    next.setAttribute('aria-label', 'Next page');
    pagination.append(previous, element('span', '', `${state.page}/${pages}`), next);
  };

  const loadQuestions = async () => {
    const query = new URLSearchParams({ page: String(state.page), page_size: String(pageSize) });
    if (state.search) query.set('q', state.search);
    if (state.status) query.set('status', state.status);
    setMessage('Loading questions…');
    try {
      const data = await request(`${base}?${query}`);
      state.count = data.count;
      renderList(data.results);
      renderPagination();
      setMessage(data.count ? `${data.count} question${data.count === 1 ? '' : 's'}` : '');
    } catch (error) {
      list.replaceChildren();
      pagination.replaceChildren();
      setMessage(error.message, 'error');
    }
  };

  const makeForm = (kind, label, value, maxLength, multiline = false, secondaryValue = '') => {
    const form = element('form', 'qa-inline-form');
    form.dataset.qaForm = kind;
    const field = element(multiline ? 'textarea' : 'input');
    field.name = kind === 'edit-question' ? 'title' : 'body';
    field.required = true;
    field.maxLength = maxLength;
    if (multiline) field.rows = 4;
    else field.type = 'text';
    field.value = value || '';
    field.setAttribute('aria-label', label);
    form.append(field);
    if (kind === 'edit-question') {
      const body = element('textarea');
      body.name = 'body';
      body.required = true;
      body.maxLength = 5000;
      body.rows = 5;
      body.value = secondaryValue;
      body.setAttribute('aria-label', 'Question details');
      form.append(body);
    }
    const actions = element('div', 'qa-form-foot');
    const save = element('button', 'course-btn', kind.startsWith('edit') ? 'Save changes' : 'Post answer');
    save.type = 'submit';
    const cancel = element('button', 'course-btn secondary', 'Cancel');
    cancel.type = 'button';
    cancel.dataset.qaCancelForm = '';
    actions.append(save, cancel);
    form.append(actions);
    return form;
  };

  const renderAnswer = (question, answer) => {
    const card = element('article',
      `qa-answer${answer.is_verified ? ' is-verified' : ''}${answer.archived_at ? ' is-archived' : ''}`);
    const heading = element('div', 'qa-answer__heading');
    heading.append(meta(answer.author_name, answer.author_role_label, answer.created_at, answer.edited_at));
    if (answer.is_verified) {
      const official = element('span', 'qa-tag qa-tag--verified', '📌 Verified answer');
      heading.append(official);
    }
    if (answer.archived_at) heading.append(element('span', 'qa-tag qa-tag--archived', 'Archived'));
    card.append(heading, element('p', 'qa-answer__body', answer.body));

    const actions = element('div', 'qa-actions');
    actions.dataset.answerId = answer.id;
    if (answer.can_verify) {
      actions.append(actionButton(answer.is_verified ? 'Unverify' : 'Verify answer', 'verify-answer'));
    }
    if (answer.can_edit) actions.append(actionButton('Edit', 'edit-answer'));
    if (answer.can_archive) {
      actions.append(actionButton('Archive', 'archive-answer', 'qa-action qa-action--danger'));
    }
    if (answer.archived_at && !question.archived_at && root.dataset.isDepartment === 'true') {
      actions.append(actionButton('Restore answer', 'restore-answer'));
    }
    card.append(actions);
    return card;
  };

  const renderThread = (question, answers) => {
    threadContent.replaceChildren();
    const article = element('article', 'qa-thread-question');
    const heading = element('div', 'qa-thread-question__heading');
    heading.append(element('h3', '', question.title));
    if (question.is_pinned) heading.append(element('span', 'qa-tag qa-tag--pinned', 'Pinned'));
    if (question.has_verified_answer) {
      heading.append(element('span', 'qa-tag qa-tag--verified', '📌 Verified answer'));
    }
    if (question.archived_at) heading.append(element('span', 'qa-tag qa-tag--archived', 'Archived'));
    article.append(heading, meta(question.author_name, question.author_role_label, question.created_at, question.edited_at));
    article.append(element('p', 'qa-thread-question__body', question.body));
    const questionActions = element('div', 'qa-actions');
    questionActions.dataset.questionId = question.id;
    addQuestionActions(questionActions, question);
    article.append(questionActions);
    threadContent.append(article);

    if (question.can_edit) {
      const form = makeForm('edit-question', 'Question title', question.title, 200, false, question.body);
      form.hidden = true;
      threadContent.append(form);
    }

    const answerHeading = element('h4', 'qa-answers-title', `Answers (${answers.length})`);
    threadContent.append(answerHeading);
    if (!answers.length) {
      threadContent.append(element('p', 'qa-empty-inline', 'No answers yet. A course teaching team member can help.'));
    }
    answers.forEach(answer => {
      const card = renderAnswer(question, answer);
      threadContent.append(card);
      if (answer.can_edit) {
        const form = makeForm('edit-answer', 'Answer text', answer.body, 5000, true);
        form.dataset.answerId = answer.id;
        form.hidden = true;
        threadContent.append(form);
      }
    });
    if (question.can_answer) {
      threadContent.append(makeForm('create-answer', 'Write an official answer', '', 5000, true));
    }
  };

  const loadThread = async questionId => {
    try {
      const data = await request(`${base}${questionId}/`);
      state.questionId = questionId;
      renderThread(data.question, data.answers);
      if (!thread.open) thread.showModal();
    } catch (error) {
      setMessage(error.message, 'error');
    }
  };

  const mutate = (url, body) => request(url, {
    method: 'POST',
    body: JSON.stringify(body || {}),
  });

  const refreshAfterMutation = async () => {
    await loadQuestions();
    if (!state.questionId) return;
    const visible = list.querySelector(`[data-open-question="${state.questionId}"]`);
    if (visible) {
      await loadThread(state.questionId);
    } else {
      if (thread.open) thread.close();
      state.questionId = null;
    }
  };

  const questionForm = root.querySelector('[data-qa-question-form]');
  if (questionForm) {
    questionForm.addEventListener('submit', async event => {
      event.preventDefault();
      const values = new FormData(questionForm);
      try {
        const created = await mutate(base, {
          title: String(values.get('title') || ''),
          body: String(values.get('body') || ''),
        });
        questionForm.reset();
        askDialog.close();
        state.page = 1;
        state.status = '';
        state.search = '';
        searchInput.value = '';
        if (statusFilter) statusFilter.value = '';
        setMessage('Question posted.', 'success');
        await loadQuestions();
        await loadThread(created.id);
      } catch (error) {
        setMessage(error.message, 'error');
      }
    });
  }

  root.closest('.course-tool')?.querySelector('[data-qa-open-ask]')
    ?.addEventListener('click', () => askDialog.showModal());
  root.querySelector('[data-qa-close-ask]')?.addEventListener('click', () => askDialog.close());
  root.querySelector('[data-qa-close-thread]')?.addEventListener('click', () => thread.close());

  searchInput.addEventListener('input', () => {
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(() => {
      state.search = searchInput.value.trim();
      state.page = 1;
      loadQuestions();
    }, 250);
  });

  statusFilter?.addEventListener('change', () => {
    state.status = statusFilter.value;
    state.page = 1;
    loadQuestions();
  });

  root.addEventListener('submit', async event => {
    const form = event.target;
    if (!form.matches('[data-qa-form]')) return;
    event.preventDefault();
    const values = new FormData(form);
    const kind = form.dataset.qaForm;
    const questionId = form.closest('[data-question-id]')?.dataset.questionId || state.questionId;
    let url = `${base}${questionId}/`;
    let body;
    if (kind === 'edit-question') {
      url += 'edit/';
      body = { title: String(values.get('title') || ''), body: String(values.get('body') || '') };
    } else if (kind === 'edit-answer') {
      url += `answers/${form.dataset.answerId}/edit/`;
      body = { body: String(values.get('body') || '') };
    } else {
      url += 'answers/';
      body = { body: String(values.get('body') || '') };
    }
    try {
      await mutate(url, body);
      setMessage(kind === 'create-answer' ? 'Answer posted.' : 'Changes saved.', 'success');
      await refreshAfterMutation();
    } catch (error) {
      setMessage(error.message, 'error');
    }
  });

  root.addEventListener('click', async event => {
    const open = event.target.closest('[data-open-question]');
    if (open) {
      await loadThread(open.dataset.openQuestion);
      return;
    }
    const page = event.target.closest('[data-page]');
    if (page) {
      state.page = Number(page.dataset.page);
      await loadQuestions();
      return;
    }
    const cancel = event.target.closest('[data-qa-cancel-form]');
    if (cancel) {
      cancel.closest('form').hidden = true;
      return;
    }
    const actionButton = event.target.closest('[data-qa-action]');
    if (!actionButton) return;
    const action = actionButton.dataset.qaAction;
    const actions = actionButton.closest('[data-question-id], [data-answer-id]');
    const questionId = actions?.dataset.questionId || state.questionId;
    const answerId = actions?.dataset.answerId;
    if (action === 'edit-question' || action === 'edit-answer') {
      const form = actionButton.closest('article')?.nextElementSibling;
      if (form?.matches('[data-qa-form]')) form.hidden = false;
      return;
    }
    if (action.startsWith('archive-')
      && !window.confirm('Archive this item? An authorized moderator can restore it.')) return;
    const actionsByName = {
      pin: [`${base}${questionId}/pin/`, { is_pinned: actionButton.textContent.trim() !== 'Unpin' }],
      'archive-question': [`${base}${questionId}/archive/`, {}],
      'restore-question': [`${base}${questionId}/restore/`, {}],
      'archive-answer': [`${base}${questionId}/answers/${answerId}/archive/`, {}],
      'restore-answer': [`${base}${questionId}/answers/${answerId}/restore/`, {}],
      'verify-answer': [`${base}${questionId}/answers/${answerId}/verify/`, {
        is_verified: actionButton.textContent.trim() !== 'Unverify',
      }],
    };
    if (!actionsByName[action]) return;
    try {
      await mutate(...actionsByName[action]);
      setMessage('Discussion updated.', 'success');
      await refreshAfterMutation();
    } catch (error) {
      setMessage(error.message, 'error');
    }
  });

  loadQuestions();
})();
