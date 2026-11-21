# BNM-ISP

A centralized, easily navigable, and simple-to-maintain digital hub that unifies academic information, operational guidance, and official updates, eliminating redundant staff inquiries and providing students with a single source of truth for course and department logistics.


## Group Members

| Name | Nisit ID | GitHub Username |
|------|----------|------------------|
| Suwat Teiwasuculmetri | 6810545956 | mosuwat |
| Phunnipath Theankaew | 6810545816 | phunnipathtkasetsart|
| Apichai Pattanakamolkul | 6810545972 | Sh1uSeZ |
| Chayapol Kaewsakul | 6810545557 | kewandkew1 |
| Suppamok Tosranon | 6810545921 | TheII-Jimmy |


## Current Project Status

**Work In Progress:** Iteration II


## Where to Find Documents / Diagrams

- **Project documents (PDF)** — located in [`docs/`](./docs)
  - Software Proposal
  - SRS (Software Requirements Specification)
  - Iteration Report
- **Grant Chart (JSON)** — located in [`docs/`](./docs)
  - Wide Grant Chart
  - Iteration 1 Grant Chart
- **Diagrams (JSON)** — located in [`docs/draw.io-diagram`](./docs/draw.io-diagram/)
  - draw.io diagrams (Use Case, Activity, Sequence)


## Setup

This guide takes you from an empty machine to a running copy of the application. The whole stack (Django, PostgreSQL and MinIO) runs in Docker, so you do not install Python or PostgreSQL yourself.

**Contents**

1. [What you will end up running](#1-what-you-will-end-up-running)
2. [Install Git and Docker](#2-install-git-and-docker)
3. [Clone the repository](#3-clone-the-repository)
4. [Create the `.env` file](#4-create-the-env-file)
5. [Set up Google Cloud Console](#5-set-up-google-cloud-console-oauth-and-gmail-api)
6. [Fill in the Google values in `.env`](#6-fill-in-the-google-values-in-env)
7. [Build and run with Docker](#7-build-and-run-with-docker)
8. [Database migrations](#8-database-migrations)
9. [Seed demo data (optional)](#9-seed-demo-data-optional)
10. [Sign in and assign academic advisors](#10-sign-in-and-assign-academic-advisors)
11. [Everyday commands](#11-everyday-commands)
12. [Troubleshooting](#12-troubleshooting)

Google setup (step 5) is only needed for **Sign in with Google** and for the **Contact advisor** (Gmail draft) feature. Without it the app still runs and Nisit ID + password sign-in works, so you can skip steps 5 and 6 on a first run and come back later.

### 1. What you will end up running

`docker compose` starts three containers from `source/academic_hub/docker-compose.yml`:

| Service | Image | Purpose | Port on your machine |
|---------|-------|---------|----------------------|
| `web` | built from `Dockerfile` (Python 3.12, Django 5.1) | The application | `8000` |
| `db` | `postgres:16-alpine` | PostgreSQL database (loaded from `db_backup.sql` the first time) | `5432` |
| `minio` | `pgsty/silo` (MinIO-compatible S3 server) | Object storage for Shorts video clips | `9000` (S3 API), `9001` (web console) |

Make sure ports `8000`, `5432`, `9000` and `9001` are free before you start.

### 2. Install Git and Docker

1. **Git** – download and install from <https://git-scm.com/downloads>. Check it with:

   ```bash
   git --version
   ```

2. **Docker Desktop** – download from <https://www.docker.com/products/docker-desktop/> and install it.

   - **Windows:** tick **"Use WSL 2 instead of Hyper-V"** during installation, and restart if the installer asks.
   - **macOS:** pick the **Apple Silicon** build for M1/M2/M3/M4 Macs, or the **Intel** build for older Macs.
   - **Linux:** install [Docker Engine](https://docs.docker.com/engine/install/) and the [Compose plugin](https://docs.docker.com/compose/install/linux/) instead.

3. Launch Docker Desktop and wait until it reports that the engine is running. Then verify:

   ```bash
   docker --version
   docker compose version
   ```

   Both commands must print a version. If `docker compose` is not found, update Docker Desktop.

### 3. Clone the repository

```bash
git clone https://github.com/phunnipathtkasetsart/BNM-ISP.git
cd BNM-ISP/source/academic_hub
```

**Every Docker command below must be run from `BNM-ISP/source/academic_hub`**, the folder that contains `docker-compose.yml`, `manage.py` and `.env.example`.

### 4. Create the `.env` file

`docker-compose.yml` reads its settings from a file named `.env`. The file is ignored by Git, so you create it from the template:

```bash
# macOS / Linux / Git Bash
cp .env.example .env
```

```powershell
# Windows PowerShell
Copy-Item .env.example .env
```

Open `.env` in a text editor. These are the settings it contains:

| Variable | What it does | What to put |
|----------|--------------|-------------|
| `SECRET_KEY` | Django's signing key | Any long random string. Generate one in step 4a. |
| `DEBUG` | Django debug mode | `True` for local development, `False` for anything public |
| `ALLOWED_HOSTS` | Hostnames Django will serve | Leave the default for local use |
| `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` | Database connection | Leave the defaults. They match the `db` container. |
| `GOOGLE_OAUTH_CLIENT_ID` | Google OAuth Client ID | From step 5 |
| `GOOGLE_OAUTH_CLIENT_SECRET` | Google OAuth Client secret | From step 5 |
| `GOOGLE_OAUTH_REDIRECT_URI` | Redirect URL for **Sign in with Google** | `http://localhost:8000/accounts/oauth/google/callback/` |
| `GOOGLE_GMAIL_OAUTH_REDIRECT_URI` | Redirect URL for the **Contact advisor** Gmail feature | `http://localhost:8000/accounts/oauth/google/gmail/callback/` |
| `GMAIL_TOKEN_ENCRYPTION_KEY` | Key used to encrypt students' stored Gmail refresh tokens | Generated in step 6 |
| `EMAIL_BACKEND`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL` | Password-reset emails | Leave as the console backend for local use. See step 4b to send real email. |
| `PASSWORD_RESET_BASE_URL` | Base URL used in reset links | `http://localhost:8000` |
| `MAX_UPLOAD_MB`, `SHORTS_MAX_UPLOAD_MB` | Upload limits for course files and Shorts clips | Defaults are fine |
| `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD` | MinIO credentials (commented out in the template) | Leave commented for local use. Defaults are `bnm-minio` / `bnm-minio-dev-password`. |

The database and MinIO settings in `docker-compose.yml` override the values in `.env`, so you normally only edit the Google values and `SECRET_KEY`.

#### 4a. (Recommended) Generate a Django `SECRET_KEY`

Run this once, copy the output into `SECRET_KEY=` in `.env`:

```bash
docker compose run --rm --no-deps web python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"
```

The first run builds the image, which takes a few minutes. If you would rather not do this yet, any long random string works for local development.

#### 4b. Email settings (password reset)

These settings control the emails the app sends itself, which is the **Forgot password** link. They are separate from the Gmail draft feature in step 5, which uses the student's own Google account and needs no SMTP server.

| Variable | What it does | Default / example |
|----------|--------------|-------------------|
| `EMAIL_BACKEND` | How Django sends mail | `django.core.mail.backends.console.EmailBackend` prints emails to the `web` log. Use `django.core.mail.backends.smtp.EmailBackend` to send real email. |
| `EMAIL_HOST` | SMTP server address | `smtp.gmail.com` |
| `EMAIL_PORT` | SMTP port | `587` (with `EMAIL_USE_TLS=True`) |
| `EMAIL_USE_TLS` | Encrypt the connection with STARTTLS | `True` |
| `EMAIL_HOST_USER` | Account used to log in to the SMTP server | `your.address@gmail.com` |
| `EMAIL_HOST_PASSWORD` | Password for that account | For Gmail, an **App Password**, not your normal password |
| `DEFAULT_FROM_EMAIL` | The "From" address on outgoing mail | Usually the same as `EMAIL_HOST_USER` |
| `PASSWORD_RESET_BASE_URL` | Start of the link inside the reset email | `http://localhost:8000` locally, your public `https://` domain when deployed |
| `PASSWORD_RESET_TIMEOUT_MINUTES` | How long a reset link stays valid | `30` |
| `PASSWORD_RESET_REQUEST_LIMIT` | Reset requests allowed per window | `5` |
| `PASSWORD_RESET_REQUEST_WINDOW` | Length of that window in seconds | `3600` |

**Local development (no setup needed).** Leave `EMAIL_BACKEND` as the console backend and keep `EMAIL_HOST`, `EMAIL_HOST_USER` and `EMAIL_HOST_PASSWORD` empty. Reset links are then printed in the app log:

```bash
docker compose logs -f web
```

**Sending real email through Gmail SMTP.**

1. Use a Google account with **2-Step Verification** turned on (<https://myaccount.google.com/security>).
2. Create an App Password at <https://myaccount.google.com/apppasswords>. Google shows a 16-character password once, so copy it. Some accounts, including many school and Workspace accounts, do not allow App Passwords. Use another account or another SMTP provider in that case.
3. Edit `.env`:

   ```env
   EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
   EMAIL_HOST=smtp.gmail.com
   EMAIL_PORT=587
   EMAIL_USE_TLS=True
   EMAIL_HOST_USER=your.address@gmail.com
   EMAIL_HOST_PASSWORD=abcdefghijklmnop
   DEFAULT_FROM_EMAIL=your.address@gmail.com
   PASSWORD_RESET_BASE_URL=http://localhost:8000
   ```

4. Apply the change with `docker compose up -d --force-recreate web`.

Any other SMTP provider works the same way. Copy the host, port and credentials from the provider's documentation. Never commit real credentials. `.env` is already ignored by Git.

### 5. Set up Google Cloud Console (OAuth and Gmail API)

The application uses **one** OAuth client for two features:

- **Sign in with Google** – scopes `openid email profile`. Only verified `@ku.th` accounts are accepted.
- **Contact advisor** – scope `https://www.googleapis.com/auth/gmail.compose`. A student connects their KU Gmail and the app creates a **draft** email addressed to their academic advisor. The student reviews and sends it themselves. The app cannot read the inbox and cannot send mail on its own.

You need a Google account to create the project. Google's console layout changes from time to time, so menu names may differ slightly from those below.

#### 5.1 Create a project

1. Go to <https://console.cloud.google.com/> and sign in.
2. Click the project picker at the top of the page, then **New Project**.
3. Enter a name (for example `BNM-ISP`), leave the organization and location as they are, and click **Create**.
4. Make sure the new project is selected in the project picker.

#### 5.2 Enable the Gmail API

1. Open **APIs & Services → Library** (or go to <https://console.cloud.google.com/apis/library>).
2. Search for **Gmail API**, open it and click **Enable**.

The sign-in feature needs no extra API. Only the Contact advisor feature needs Gmail API.

#### 5.3 Configure the OAuth consent screen (Google Auth Platform)

Open **APIs & Services → OAuth consent screen**. In newer consoles this is called **Google Auth Platform**. Click **Get started** if it is a new project.

1. **App information**
   - App name: for example `BNM-ISP Academic Hub`
   - User support email: pick your own email
2. **Audience**
   - Choose **External**. **Internal** is only offered to Google Workspace organizations, and only lets users of that organization in. A personal Google account cannot use it.
3. **Contact information** – enter an email address for Google's notices.
4. Agree to the Google API Services User Data Policy and click **Create**.
5. Check the publishing status on the **Audience** page. It must be **Testing**. Leave it in Testing for development and demos.

#### 5.4 Add the scopes

1. Open **Data Access** (older consoles: **OAuth consent screen → Edit app → Scopes**).
2. Click **Add or remove scopes** and select:
   - `openid`
   - `.../auth/userinfo.email`
   - `.../auth/userinfo.profile`
   - `https://www.googleapis.com/auth/gmail.compose` (listed as "Manage drafts and send emails". If it does not appear in the list, enable the Gmail API first, or paste the scope URL into the manual-entry box.)
3. Click **Update**, then **Save**.

#### 5.5 Add test users (including the academic advisor and students)

While the app is in **Testing**, Google only lets accounts on the **test users** list authorize it. Anyone else gets an **"Error 403: access_denied"** message.

1. Open **Audience** (older consoles: **OAuth consent screen → Test users**).
2. Under **Test users**, click **+ Add users**.
3. Enter the full `@ku.th` email address of every person who will use a Google feature, one per line, and click **Save**:
   - **Students** who will sign in with Google or press **Contact advisor**. They are the ones who authorize Gmail, because the draft is created in the student's own mailbox.
   - **Academic advisors (lecturers)** who will sign in with Google. An advisor only receives the email, so they do not need Gmail authorization, but they do need to be on the list to use Google sign-in.
   - **Yourself and any teammates or graders** who will test the app.

Limits to be aware of:

- A project in Testing mode accepts up to **100** test users.
- The email must match the one stored in the application's `Users` table, because the app checks that the connected Google account equals the student's registered KU email.
- In Testing mode Google expires refresh tokens after **7 days**. A student whose token has expired is automatically sent through the Google authorization screen again the next time they press Contact advisor.
- Each test user sees an **"unverified app"** warning screen, because `gmail.compose` is a sensitive scope. Click **Advanced → Go to *app name* (unsafe)** to continue. This is expected while the app is unverified.

#### 5.6 Create the OAuth client ID and client secret

1. Open **APIs & Services → Credentials** (in Google Auth Platform: **Clients**).
2. Click **+ Create Credentials → OAuth client ID** (or **+ Create client**).
3. Application type: **Web application**.
4. Name: for example `BNM-ISP web client`.
5. Under **Authorized redirect URIs** click **+ Add URI** and add **both** of these, exactly as written (including the trailing slash):

   ```
   http://localhost:8000/accounts/oauth/google/callback/
   http://localhost:8000/accounts/oauth/google/gmail/callback/
   ```

   Authorized JavaScript origins are not needed, so leave that section empty.
6. Click **Create**.
7. A dialog shows your **Client ID** and **Client secret**. Copy both now, or click **Download JSON** to keep a copy. You can also come back later by opening the client from the Credentials page.

Where each value comes from:

| `.env` variable | Where it comes from |
|-----------------|---------------------|
| `GOOGLE_OAUTH_CLIENT_ID` | The **Client ID** from step 5.6 (ends in `.apps.googleusercontent.com`) |
| `GOOGLE_OAUTH_CLIENT_SECRET` | The **Client secret** from step 5.6 |
| `GOOGLE_OAUTH_REDIRECT_URI` | Not issued by Google. You choose it, and it must be identical to the first URI in step 5.6. |
| `GOOGLE_GMAIL_OAUTH_REDIRECT_URI` | Not issued by Google. You choose it, and it must be identical to the second URI in step 5.6. |
| `GMAIL_TOKEN_ENCRYPTION_KEY` | Not from Google. You generate it yourself in step 6. |

Treat the client secret like a password. Never commit it, post it in chat, or put it in `.env.example`. `.env` is already listed in `.gitignore`.

Two things to remember:

- Google compares redirect URIs character by character. `localhost` and `127.0.0.1` are different hosts, and `http` is not `https`. **Always open the application at `http://localhost:8000`**, not `127.0.0.1`, otherwise the sign-in state cookie will not match and Google will reject the redirect.
- If you deploy the app on a real domain later, add that domain's two callback URLs to the same client, update both redirect variables in `.env`, and update `ALLOWED_HOSTS` and `PASSWORD_RESET_BASE_URL`. Google requires `https://` for any redirect that is not `localhost`.

### 6. Fill in the Google values in `.env`

Generate the encryption key. This is a Fernet key used to encrypt each student's Gmail refresh token before it is saved in the database:

```bash
docker compose run --rm --no-deps web python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Copy the printed string (about 44 characters ending in `=`). If you have Python and the `cryptography` package installed locally you can run the same command without Docker.

Then edit `.env` so the Google block looks like this, using your own values:

```env
GOOGLE_OAUTH_CLIENT_ID=1234567890-abcdefg.apps.googleusercontent.com
GOOGLE_OAUTH_CLIENT_SECRET=GOCSPX-xxxxxxxxxxxxxxxxxxxx
GOOGLE_OAUTH_REDIRECT_URI=http://localhost:8000/accounts/oauth/google/callback/
GOOGLE_GMAIL_OAUTH_REDIRECT_URI=http://localhost:8000/accounts/oauth/google/gmail/callback/
GMAIL_TOKEN_ENCRYPTION_KEY=paste-the-generated-key-here=
```

Rules for these values:

- No quotes and no spaces around `=`.
- Keep `GMAIL_TOKEN_ENCRYPTION_KEY` **stable and secret**. If you change or lose it, every saved Gmail authorization becomes unreadable and students must reconnect Gmail.
- If `GOOGLE_OAUTH_CLIENT_ID` or `GOOGLE_OAUTH_CLIENT_SECRET` is empty, Google sign-in shows "not configured". If `GMAIL_TOKEN_ENCRYPTION_KEY` is empty, Contact advisor shows "Gmail contact is not configured".
- The container reads `.env` only when it is created. After editing `.env` while the app is running, recreate it: `docker compose up -d --force-recreate web`.

### 7. Build and run with Docker

From `BNM-ISP/source/academic_hub`:

```bash
docker compose up --build
```

What happens, in order:

1. Docker builds the `web` image (Python 3.12, then `pip install -r requirements.txt`).
2. The `db` container starts. **On its very first start**, PostgreSQL loads `db_backup.sql`, which creates the `ISP_DJANGO_2026` schema, the `Users` table and three seeded accounts.
3. The `minio` container starts.
4. When `db` and `minio` are healthy, `web` runs this command automatically:

   ```
   python manage.py migrate --noinput
   python manage.py ensure_shorts_bucket
   python manage.py runserver 0.0.0.0:8000
   ```

   That means **all Django migrations are applied and the Shorts storage bucket is created for you** on every start.

Wait until you see `Starting development server at http://0.0.0.0:8000/`, then open:

| URL | What it is |
|-----|-----------|
| <http://localhost:8000> | The application. It redirects to the sign-in page. |
| <http://localhost:9001> | MinIO console (user `bnm-minio`, password `bnm-minio-dev-password`) |

To run in the background instead, use `docker compose up --build -d` and follow the log with `docker compose logs -f web`.

To stop: press `Ctrl+C` (foreground) or run `docker compose down` (background). Your data is kept in Docker volumes.

### 8. Database migrations

For a normal setup you do **not** have to do anything: step 7 already runs `migrate`. This section explains what is going on and how to run migrations yourself.

**How the database is built**

- `db_backup.sql` is mounted into the Postgres container's `docker-entrypoint-initdb.d`. Postgres runs it **only when the data volume is empty**, i.e. the first time the `db` container is created. It creates the `ISP_DJANGO_2026` schema and the legacy `Users` table (the `User` model is `managed = False`, so Django does not create that table itself) and inserts three accounts.
- The Django app's `search_path` is `ISP_DJANGO_2026,public`, so app tables are created in the schema or in `public` as the migrations specify.
- `accounts/migrations/0002_schema_and_permission_tables.py` uses `IF NOT EXISTS` everywhere. On a database restored from `db_backup.sql` it changes nothing. On an empty database it creates the schema, the `Users` table and the permission join tables, so migrations alone can also build a working database.
- The apps with migrations are `accounts`, `announcements`, `course`, `calendar_app`, `notifications` and `shorts`, plus Django's own `auth`, `admin`, `contenttypes` and `sessions`.

**Run or check migrations manually** (with the containers running):

```bash
# Apply every pending migration
docker compose exec web python manage.py migrate

# See which migrations are applied ([X]) and which are pending ([ ])
docker compose exec web python manage.py showmigrations

# Apply one app only
docker compose exec web python manage.py migrate course

# Create the Shorts bucket in MinIO (done automatically at startup)
docker compose exec web python manage.py ensure_shorts_bucket
```

If the stack is not running, use `docker compose run --rm web python manage.py migrate` instead of `exec`. That starts `db` and `minio` for you.

**After you change a model** (developers only):

```bash
docker compose exec web python manage.py makemigrations
docker compose exec web python manage.py migrate
```

Commit the new migration files. The project folder is mounted into the container, so the generated files appear on your machine. Do not run `makemigrations` for the `User` model: it is unmanaged and its table is owned by `db_backup.sql`.

**Start again from a clean database**

`db_backup.sql` only runs on an empty volume, so editing it or re-running `up` does nothing to an existing database. To wipe everything and rebuild:

```bash
docker compose down -v      # -v deletes the Postgres AND MinIO volumes (all data, including uploaded Shorts)
docker compose up --build   # reloads db_backup.sql, re-runs migrations
```

**Run the automated tests**

```bash
docker compose exec web python manage.py test
```

### 9. Seed demo data (optional)

Run these after the app is up and migrations have finished. Both are safe to run more than once.

```bash
# Demo announcements, FAQs and tags for the public board, and fills the search index
docker compose exec web python manage.py seed_public_content

# Load the team's sample Shorts videos from shorts/seed/ into MinIO
docker compose exec web python manage.py seed_shorts
```

Optional housekeeping, to delete expired guest accounts created by "Sign in as guest":

```bash
docker compose exec web python manage.py purge_guest_accounts
```

### 10. Sign in and assign academic advisors

**Seeded accounts** (from `db_backup.sql`):

| Nisit ID | Role | Email |
|----------|------|-------|
| `A0001` | Department (administrator) | `mosuwat50@gmail.com` |
| `L0001` | Lecturer | `phunipath.t@ku.th` |
| `6810545956` | Student | `suwat.te@ku.th` |

The passwords are stored as hashes, so they cannot be read from the file. If you do not know them, set a new one for any account:

```bash
docker compose exec web python manage.py changepassword A0001
```

Other ways in:

- **Register** a new student account on the sign-in page. Nisit IDs are 10 digits and emails must end in `@ku.th`.
- **Sign in with Google** with a KU account that is on your test-user list (step 5.5). A first-time Google user is asked for their Nisit ID and programme, which creates their account.
- **Sign in as guest** for read-only browsing.

**Make the Contact advisor feature work end to end**

1. Sign in as the Department account (`A0001`) and open **Manage roles** at <http://localhost:8000/accounts/roles/>.
2. Set the lecturer's role to **Lecturer**. Only lecturers can be advisors.
3. Assign each student an academic advisor from the advisor controls on that page.
4. Make sure the student's `@ku.th` address and the advisor's `@ku.th` address are on the Google test-user list (step 5.5).
5. Sign in as that student, open the course dashboard, and click **Contact advisor**. Google asks the student to authorize Gmail drafts. They then land in Gmail with a draft already addressed to their advisor.

The Contact advisor button only appears for students who have an assigned advisor and a `@ku.th` email.

### 11. Everyday commands

Run from `BNM-ISP/source/academic_hub`.

| Task | Command |
|------|---------|
| Start | `docker compose up` |
| Start in the background | `docker compose up -d` |
| Stop (keep data) | `docker compose down` |
| Stop and delete all data | `docker compose down -v` |
| Rebuild after changing `requirements.txt` or the `Dockerfile` | `docker compose up --build` |
| Apply `.env` changes | `docker compose up -d --force-recreate web` |
| Follow the app log | `docker compose logs -f web` |
| Open a shell in the app container | `docker compose exec web bash` |
| Django shell | `docker compose exec web python manage.py shell` |
| Connect to PostgreSQL | `docker compose exec db psql -U postgres -d postgres` |
| Create a Django superuser | `docker compose exec web python manage.py createsuperuser` |

Python code changes are picked up automatically because the folder is mounted into the container.

### 12. Troubleshooting

| Symptom | Cause and fix |
|---------|---------------|
| `env file ... .env not found` when running `docker compose` | You skipped step 4. Run `cp .env.example .env`. |
| `port is already allocated` for 5432, 8000, 9000 or 9001 | Another program is using it. Stop it (a local PostgreSQL is the usual cause of 5432), or change the left-hand port in `docker-compose.yml`. |
| `web` crashes with a database connection or authentication error | `db` is not ready or an old volume has different credentials. Run `docker compose down -v` and start again. |
| Pages fail with `relation "ISP_DJANGO_2026"."Users" does not exist` | `db_backup.sql` did not run, usually because the volume already existed. Run `docker compose down -v`, then `docker compose up --build`. |
| Warning that MinIO is not reachable, or **Create Short** fails | The app still runs without MinIO, only uploading and playing Shorts needs it. Check `docker compose ps` and `docker compose logs minio`. |
| "Google sign-in is not configured yet" | `GOOGLE_OAUTH_CLIENT_ID` or `GOOGLE_OAUTH_CLIENT_SECRET` is empty, or you did not recreate the container after editing `.env` (step 6). |
| "Gmail contact is not configured" | `GMAIL_TOKEN_ENCRYPTION_KEY` is empty or invalid. Generate a key as in step 6. |
| Google shows **Error 400: redirect_uri_mismatch** | The redirect URI in Google Cloud does not exactly match the one in `.env`. Compare both, including `http`, `localhost`, the port and the trailing `/`. |
| Google shows **Error 403: access_denied** ("has not completed the verification process") | The account is not on the test-user list. Add it in step 5.5. |
| "Sign-in could not be verified" | You opened the site at `127.0.0.1` or another host. Use `http://localhost:8000`. |
| "Use a verified KU Google account to sign in" | Only verified `@ku.th` Google accounts are accepted. |
| "Choose the verified KU Google account that matches your student email" | The Google account chosen in the popup differs from the email stored on the student's account. |
| Contact advisor asks the student to authorize again after about a week | Normal in Testing mode, since Google expires refresh tokens after 7 days. Authorizing again fixes it. |
| "The saved Gmail authorization cannot be decrypted" | `GMAIL_TOKEN_ENCRYPTION_KEY` changed. Restore the old key, or have the student reconnect Gmail. |
| No Contact advisor button on the dashboard | The student has no advisor assigned (step 10), the advisor is not a Lecturer, or the student's email is not `@ku.th`. |
| Password-reset email never arrives | Local runs use the console email backend. Find the reset link in `docker compose logs web`. Configure `EMAIL_*` for real SMTP delivery. |
