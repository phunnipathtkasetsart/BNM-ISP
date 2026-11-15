(() => {
  'use strict';

  const createForm = document.querySelector('[data-group-work-create]');
  if (createForm) {
    const titleInput = createForm.querySelector('input[name="title"]');
    const submitButton = createForm.querySelector('button[type="submit"]');
    const warning = createForm.querySelector('[data-group-work-title-error]');
    const existingTitles = Array.from(document.querySelectorAll('.group-work-card h2'))
      .map(heading => heading.textContent.trim().toLocaleLowerCase());

    const checkTitle = () => {
      const title = titleInput.value.trim().toLocaleLowerCase();
      const duplicate = title !== '' && existingTitles.includes(title);
      warning.hidden = !duplicate;
      titleInput.setAttribute('aria-invalid', String(duplicate));
      submitButton.disabled = duplicate;
    };

    titleInput.addEventListener('input', checkTitle);
    checkTitle();
  }

  const groupCreateForm = document.querySelector('[data-group-create]');
  if (groupCreateForm) {
    const nameInput = groupCreateForm.querySelector('input[name="name"]');
    const submitButton = groupCreateForm.querySelector('button[type="submit"]');
    const warning = groupCreateForm.querySelector('[data-group-name-error]');
    const existingNames = JSON.parse(
      document.getElementById('existing-group-names').textContent,
    ).map(name => name.trim().toLocaleLowerCase());

    const checkGroupName = () => {
      const name = nameInput.value.trim().toLocaleLowerCase();
      const duplicate = name !== '' && existingNames.includes(name);
      warning.hidden = !duplicate;
      nameInput.setAttribute('aria-invalid', String(duplicate));
      submitButton.disabled = duplicate;
    };

    nameInput.addEventListener('input', checkGroupName);
    checkGroupName();
  }

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
          const reserved = group.pending_reservation_count
            ? ` (${group.pending_reservation_count} reserved)`
            : '';
          slots.textContent = `Available slots: ${group.remaining_slots}${reserved}`;
        }
        const actionButton = root.querySelector(
          `[data-group-actions="${group.id}"] [data-group-action-button]`,
        );
        if (actionButton) {
          if (group.has_pending_request) {
            actionButton.textContent = 'Requested';
            actionButton.disabled = true;
          } else if (group.has_pending_invitation && !group.is_public) {
            actionButton.textContent = 'Invited';
            actionButton.disabled = true;
          } else if (group.is_full) {
            actionButton.textContent = 'FULL';
            actionButton.disabled = true;
          } else if (group.viewer_has_group) {
            actionButton.textContent = 'Already in a group';
            actionButton.disabled = true;
          } else if (group.can_join || group.can_request) {
            actionButton.textContent = actionButton.dataset.actionLabel;
            actionButton.disabled = false;
          } else if (group.is_reserved) {
            actionButton.textContent = 'Reserved';
            actionButton.disabled = true;
          }
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
