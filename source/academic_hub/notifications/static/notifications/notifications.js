(() => {
  'use strict';

  const root = document.querySelector('[data-notification-bell]');
  if (!root) return;

  const list = root.querySelector('[data-notification-list]');
  const count = root.querySelector('[data-notification-count]');
  const summary = root.querySelector('[data-notification-summary]');
  const error = root.querySelector('[data-notification-error]');
  const token = root.querySelector('[name="csrfmiddlewaretoken"]')?.value || '';

  const textElement = (tag, className, value) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    node.textContent = value;
    return node;
  };

  const request = async (url, options = {}) => {
    const headers = new Headers(options.headers || {});
    headers.set('Accept', 'application/json');
    if (options.method === 'POST') {
      headers.set('X-CSRFToken', token);
      headers.set('Content-Type', 'application/json');
    }
    const response = await fetch(url, {
      credentials: 'same-origin',
      ...options,
      headers,
    });
    const contentType = response.headers.get('content-type') || '';
    if (!contentType.includes('application/json')) {
      throw new Error(
        response.ok
          ? 'Notifications returned an unexpected response. Please refresh the page.'
          : `Notifications are temporarily unavailable (HTTP ${response.status}).`,
      );
    }
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || 'Could not update notifications.');
    }
    return data;
  };

  const setError = message => {
    error.textContent = message || '';
  };

  const dateText = value => {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '';
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: 'medium',
      timeStyle: 'short',
    }).format(date);
  };

  const render = data => {
    const unread = Number(data.unread_count) || 0;
    count.textContent = unread > 99 ? '99+' : String(unread);
    count.hidden = unread === 0;
    summary.textContent = unread ? `${unread} unread` : 'You’re all caught up';
    list.replaceChildren();

    if (!data.notifications.length) {
      list.append(textElement('p', 'notification-bell__empty', 'No notifications yet.'));
      return;
    }

    data.notifications.forEach(item => {
      const link = document.createElement('a');
      const target = typeof item.url === 'string' && item.url.startsWith('/')
        && !item.url.startsWith('//') ? item.url : '#';
      link.href = target;
      link.className = `notification-item${item.is_read ? '' : ' notification-item--unread'}`;
      link.dataset.notificationId = item.id;
      link.append(
        textElement('span', 'notification-item__title', item.title),
        textElement('span', 'notification-item__message', item.message || ''),
        textElement('time', 'notification-item__date', dateText(item.created_at)),
      );
      link.querySelector('time').dateTime = item.created_at;
      list.append(link);
    });
  };

  const refresh = async () => {
    try {
      render(await request(root.dataset.listUrl));
      setError('');
    } catch (reason) {
      setError(reason.message);
    }
  };

  root.addEventListener('toggle', () => {
    if (root.open) refresh();
  });

  list.addEventListener('click', async event => {
    const link = event.target.closest('[data-notification-id]');
    if (!link || link.classList.contains('notification-item--read')) return;
    event.preventDefault();
    try {
      const readUrl = root.dataset.readUrlTemplate.replace(
        '/0/read/',
        `/${encodeURIComponent(link.dataset.notificationId)}/read/`,
      );
      await request(readUrl, {
        method: 'POST',
        body: '{}',
      });
      window.location.assign(link.href);
    } catch (reason) {
      setError(reason.message);
    }
  });

  root.querySelector('[data-notification-read-all]').addEventListener('click', async () => {
    try {
      await request(root.dataset.readAllUrl, { method: 'POST', body: '{}' });
      await refresh();
    } catch (reason) {
      setError(reason.message);
    }
  });

  document.addEventListener('click', event => {
    if (root.open && !root.contains(event.target)) root.open = false;
  });
  root.addEventListener('keydown', event => {
    if (event.key === 'Escape' && root.open) {
      root.open = false;
      root.querySelector('summary').focus();
    }
  });

  refresh();
  window.setInterval(refresh, 60000);
})();
