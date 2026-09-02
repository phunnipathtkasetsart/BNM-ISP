# Design previews

Static snapshots of the account pages. Open any `.html` file straight in a
browser -- no Python, no virtualenv, no `runserver`. Useful for showing the
UI to someone who does not have the project set up.

    login-preview.html             sign-in page
    register-preview.html          sign-up page
    faq-preview.html               FAQ accordion
    forgot_password-preview.html   password-reset request page

## These are NOT the real pages

The live pages are Django templates in `name_list/accounts/templates/accounts/`
and the stylesheet is `name_list/accounts/static/accounts/css/style.css`.
**Those are the source of truth. Edit them, never these files.**

Each preview has its own copy of the CSS inlined in a `<style>` block, so it
will drift out of date the moment the real stylesheet changes. Treat a preview
as a photo of how the page looked on the day it was written, not as a spec.

If a preview and the running site disagree, the running site is right.

## What works and what does not

Working: the password visibility toggle, the Nisit ID digit filter, inline
validation messages, the "Having Problems?" sheet, the FAQ accordion, and all
the responsive layout.

Not working: nothing submits. There is no server, so the forms have no action,
there is no CSRF token, and no account is ever created.

## Refreshing a preview

There is no generator script. Re-inline the current `style.css` into the
`<style>` block by hand, copy the current template markup in, replace the
`{% ... %}` tags with plain HTML, and point images at
`../name_list/accounts/static/accounts/img/`.

Because that is manual and easy to forget, it is fine to let these go stale --
just do not let anyone review the UI from a preview alone.
