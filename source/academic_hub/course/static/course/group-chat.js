(() => {
  'use strict';

  const form = document.querySelector('.group-chat__composer');
  const message = form?.querySelector('textarea[name="body"]');
  const fileInput = form?.querySelector('[data-chat-file-input]');
  const preview = form?.querySelector('[data-file-preview]');
  if (!form || !message || !fileInput || !preview) return;

  const maxMessageHeight = 84;
  let previewUrls = [];

  const formatSize = bytes => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  const renderFiles = () => {
    previewUrls.forEach(url => URL.revokeObjectURL(url));
    previewUrls = [];
    preview.replaceChildren();

    Array.from(fileInput.files || []).forEach((file, index) => {
      const item = document.createElement('div');
      item.className = 'group-chat__file-item';

      const thumb = document.createElement('span');
      thumb.className = 'group-chat__file-thumb';
      if (file.type.startsWith('image/')) {
        const image = document.createElement('img');
        image.alt = '';
        const url = URL.createObjectURL(file);
        previewUrls.push(url);
        image.src = url;
        thumb.append(image);
      } else {
        const extension = file.name.includes('.')
          ? file.name.split('.').pop().slice(0, 5)
          : 'FILE';
        thumb.textContent = extension || 'FILE';
      }

      const info = document.createElement('span');
      info.className = 'group-chat__file-info';
      const name = document.createElement('span');
      name.className = 'group-chat__file-name';
      name.textContent = file.name;
      name.title = file.name;
      const size = document.createElement('span');
      size.className = 'group-chat__file-size';
      size.textContent = formatSize(file.size);
      info.append(name, size);

      const remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'group-chat__file-remove';
      remove.setAttribute('aria-label', `Remove ${file.name}`);
      remove.textContent = '×';
      remove.addEventListener('click', () => {
        const transfer = new DataTransfer();
        Array.from(fileInput.files).forEach((selected, selectedIndex) => {
          if (selectedIndex !== index) transfer.items.add(selected);
        });
        fileInput.files = transfer.files;
        renderFiles();
      });

      item.append(thumb, info, remove);
      preview.append(item);
    });

    preview.hidden = preview.childElementCount === 0;
  };

  const resizeMessage = () => {
    message.style.height = '42px';
    const height = Math.min(message.scrollHeight, maxMessageHeight);
    message.style.height = `${height}px`;
    message.classList.toggle('is-overflowing', message.scrollHeight > maxMessageHeight);
  };

  fileInput.addEventListener('change', renderFiles);
  message.addEventListener('input', resizeMessage);
  form.addEventListener('submit', () => {
    previewUrls.forEach(url => URL.revokeObjectURL(url));
    previewUrls = [];
  });
  resizeMessage();

  message.addEventListener('keydown', event => {
    if (
      event.key !== 'Enter'
      || event.shiftKey
      || event.isComposing
      || event.keyCode === 229
    ) {
      return;
    }

    event.preventDefault();
    form.requestSubmit();
  });
})();
