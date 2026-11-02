(() => {
  'use strict';

  const root = document.querySelector('[data-group-work-live]');
  if (!root) return;

  const refresh = async () => {
    try {
      const response = await fetch(root.dataset.apiUrl, {
        credentials: 'same-origin',
        headers: { Accept: 'application/json' },
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.error || 'Could not refresh group information.');
      }

      data.groups.forEach(group => {
        const capacity = root.querySelector(`[data-group-capacity="${group.id}"]`);
        if (capacity) {
          capacity.textContent = `${group.member_count}/${data.max_members}`;
        }
        const slots = root.querySelector(`[data-group-slots="${group.id}"]`);
        if (slots) {
          slots.textContent = `Available invite slots: ${group.remaining_slots}`;
        }
      });
      const status = root.querySelector('[data-group-live-error]');
      if (status) status.textContent = '';
    } catch (error) {
      const status = root.querySelector('[data-group-live-error]');
      if (status) status.textContent = error.message;
    }
  };

  refresh();
  window.setInterval(refresh, 15000);
})();
