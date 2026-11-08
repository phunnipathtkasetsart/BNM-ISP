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


### Shorts Video Storage (MinIO)

Shorts clips are stored in MinIO, an S3-compatible object store that Docker
Compose starts as the `minio` service. Nothing needs setting up by hand: the
web container creates the `shorts` bucket on startup.

* MinIO console: **http://localhost:9001**. Sign in with `MINIO_ROOT_USER` /
  `MINIO_ROOT_PASSWORD` from `.env`, or the defaults `bnm-minio` /
  `bnm-minio-dev-password` if you have not set them.
* The image is `pgsty/silo`, the maintained community build of MinIO. MinIO
  stopped publishing its own `minio/minio` image in 2026.
* `docker compose down -v` deletes uploaded clips along with the database.
* Running `manage.py` outside Docker stores clips in `media/shorts/` instead.
* Clips play straight from MinIO on port 9000, through short-lived signed
  links. To watch on a phone on the same Wi-Fi, open the site at the
  laptop's address (e.g. `http://192.168.1.20:8000`) after adding that
  address to `ALLOWED_HOSTS` in `.env`.

### Sharing Shorts With the Team (Seed)

Clips live in each person's own MinIO, so a push does not carry them. To
give everyone the same set of shorts, export them into the repo:

```bash
# On the machine that has the shorts:
docker compose exec web python manage.py export_shorts_seed
```

This writes `source/academic_hub/shorts/seed/` (the video files plus
`shorts.json` with each short's title, description, tags and uploader).
Commit and push that folder. Running the export again replaces it with the
current shorts.

Everyone else, after pulling:

```bash
docker compose exec web python manage.py seed_shorts
```

It uploads each clip to their MinIO and creates the short. Running it again
only adds shorts that are not there yet. Likes and comments are not copied.
A short keeps its uploader if that account exists on the other machine;
otherwise it shows as posted by that role (Lecturer or Department).

Keep the seed small: every clone downloads it, and GitHub rejects any file
over 100 MB.
