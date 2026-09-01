# BNM-ISP This is the first README setup.
## Developer Setup

### Prerequisites
1. Install [Docker Desktop](https://www.docker.com/products/docker-desktop/).
2. Install [Git](https://git-scm.com/).

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
cd BNM-ISP/name_list
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

Open a **new terminal tab/window**, navigate to `BNM-ISP/name_list`, and run:

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
docker compose up
```

### Stop the App

Press `Ctrl + C` in the running terminal, or open a new terminal and run:

```bash
docker compose down
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

