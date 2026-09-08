"""Make the custom schema reproducible from migrations alone.

Two problems this solves.

1. `Users` is managed=False in the ISP_DJANGO_2026 schema, created only by
   db_backup.sql. Django could not build a test database, so *no* test in the
   project was allowed to touch the ORM - the suite had to be SimpleTestCase
   throughout, leaving announcements, search and visibility untestable.

2. PermissionsMixin needs Users_groups and Users_user_permissions. Nothing
   ever created them, so Django admin returned a 500 on every page and
   deleting a user raised ProgrammingError.

Every statement is IF NOT EXISTS, so on an existing database - where
db_backup.sql already ran - this migration does nothing at all. On a fresh or
test database it builds the pieces Django cannot derive from an unmanaged
model.
"""

from django.db import migrations

CREATE_SCHEMA = 'CREATE SCHEMA IF NOT EXISTS "ISP_DJANGO_2026";'

# Mirrors the live table. Column names and widths follow db_backup.sql rather
# than models.py, because the database is the thing that already exists.
CREATE_USERS = """
CREATE TABLE IF NOT EXISTS "ISP_DJANGO_2026"."Users" (
    "userID"         varchar(10)  NOT NULL PRIMARY KEY,
    "userPassword"   varchar(128),
    "userFirstName"  varchar(20),
    "userLastName"   varchar(20),
    "userEmail"      varchar(50),
    "userDepartment" varchar(30),
    "isLecturer"     boolean DEFAULT false,
    "isDepartment"   boolean DEFAULT false,
    "createDate"     timestamp(0) without time zone,
    "last_login"     timestamp with time zone,
    "is_active"      boolean NOT NULL DEFAULT true
);
"""

# The two join tables PermissionsMixin expects. Names and columns are the ones
# Django derives from the model - user_id, group_id, permission_id - not the
# camelCase used by the legacy table.
CREATE_USER_GROUPS = """
CREATE TABLE IF NOT EXISTS "ISP_DJANGO_2026"."Users_groups" (
    id       bigserial   PRIMARY KEY,
    user_id  varchar(10) NOT NULL
             REFERENCES "ISP_DJANGO_2026"."Users"("userID")
             DEFERRABLE INITIALLY DEFERRED,
    group_id integer     NOT NULL
             REFERENCES public.auth_group(id)
             DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT "Users_groups_user_group_uniq" UNIQUE (user_id, group_id)
);
"""

CREATE_USER_PERMISSIONS = """
CREATE TABLE IF NOT EXISTS "ISP_DJANGO_2026"."Users_user_permissions" (
    id            bigserial   PRIMARY KEY,
    user_id       varchar(10) NOT NULL
                  REFERENCES "ISP_DJANGO_2026"."Users"("userID")
                  DEFERRABLE INITIALLY DEFERRED,
    permission_id integer     NOT NULL
                  REFERENCES public.auth_permission(id)
                  DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT "Users_user_permissions_uniq" UNIQUE (user_id, permission_id)
);
"""

# Reversing drops only what this migration adds. Users is deliberately left
# alone: it holds real accounts and predates Django.
DROP_USER_GROUPS = 'DROP TABLE IF EXISTS "ISP_DJANGO_2026"."Users_groups";'
DROP_USER_PERMISSIONS = 'DROP TABLE IF EXISTS "ISP_DJANGO_2026"."Users_user_permissions";'


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
        # auth_group and auth_permission are created in auth's first migration,
        # and the join tables reference them.
        ("auth", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(CREATE_SCHEMA, migrations.RunSQL.noop),
        migrations.RunSQL(CREATE_USERS, migrations.RunSQL.noop),
        migrations.RunSQL(CREATE_USER_GROUPS, DROP_USER_GROUPS),
        migrations.RunSQL(CREATE_USER_PERMISSIONS, DROP_USER_PERMISSIONS),
    ]
