# BNM-ISP This is the first README setup.
## Developer Setup

### Prerequisites
1. Install [Docker Desktop](https://www.docker.com/products/docker-desktop/).
2. Install [Git](https://git-scm.com/).
3. Install requirements.txt
```bash
pip install -r requirements.txt
```
# BNM-ISP Django Project

This project uses **Docker** and **Docker Compose** to ensure every team member runs the exact same environment, Python dependencies, and database setup without manual configuration.

---

## 🛠️ Prerequisites

Before starting, every team member must install:

1. **Git:** Download and install [Git](https://git-scm.com/).
2. **Docker Desktop:** Download and install [Docker Desktop](https://www.docker.com/products/docker-desktop/).

   * **Windows users:** Ensure **"Use WSL 2 instead of Hyper-V"** is checked during installation.
   * **Mac users:** Choose the **Apple Silicon** build for M1/M2/M3/M4 chips or the **Intel** build for older Macs.
   * Launch Docker Desktop after installing and verify the bottom-left corner shows **Engine running**.

---

## First-Time Onboarding Setup

Follow these steps when setting up the project on your machine for the first time.

### 1. Clone the Repository

Open your terminal (PowerShell, Git Bash, or macOS Terminal) and run:

```bash
git clone https://github.com/phunnipathtkasetsart/BNM-ISP.git
cd BNM-ISP/source/academic_hub
```

### 2. Create Your Local Environment File

Duplicate the `.env.example` file to create your own local `.env` file. The `.env` file is ignored by Git to keep local configuration safe.

**PowerShell / Git Bash / macOS:**

```bash
cp .env.example .env
```

**Windows Command Prompt:**

```cmd
copy .env.example .env
```

### 3. Build and Launch the Application

Start the Docker containers:

```bash
docker compose up --build
```

Wait until the terminal shows that the server is running.

You can now access the site in your browser at:

**http://localhost:8000**

### Gmail API advisor drafts (optional)

To let students create advisor-email drafts in their own KU Gmail accounts:

1. Configure `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET` in `.env`.
2. Register `http://localhost:8000/accounts/oauth/google/gmail/callback/` as an authorized redirect URI for that OAuth client. For deployment, use the matching HTTPS callback URL and set `GOOGLE_GMAIL_OAUTH_REDIRECT_URI`.
3. Generate a Fernet key with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` and set it as `GMAIL_TOKEN_ENCRYPTION_KEY`. Keep it secret and stable; rotating it requires students to reconnect Gmail.
4. Enable the Gmail API and request the `gmail.compose` OAuth scope for the Google Cloud project. Google may require consent-screen verification for this scope.
5. Rebuild the web image after changing dependencies or environment configuration: `docker compose up --build`.

The app requests consent only for Gmail draft access, verifies the authorized Google email matches the student's registered `@ku.th` address, stores only an encrypted refresh token, and creates a draft for the student to review and send.

### 4. Apply Initial Database Migrations

Open a **new terminal tab/window**, navigate to `BNM-ISP/source/academic_hub`, and run:

```bash
docker compose exec web python manage.py migrate
```

### 5. Create an Admin User (Optional)

To access the Django admin panel at `http://localhost:8000/admin`, run:

```bash
docker compose exec web python manage.py createsuperuser
```

---

## Daily Development Workflow

Once the initial onboarding is complete, use these commands for everyday development.

### Start the App

```bash
docker compose up -d
```

### Stop the App

Press `Ctrl + C` in the running terminal, or open a new terminal and run:

```bash
docker compose down -v
```

### Add New Python Packages

1. Add the package name and version to `requirements.txt`.
2. Rebuild the container:

```bash
docker compose up --build
```

### Database Migrations

Generate migration files after editing `models.py`:

```bash
docker compose exec web python manage.py makemigrations
```

Apply migrations to the database:

```bash
docker compose exec web python manage.py migrate
```
