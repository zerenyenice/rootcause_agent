"""Environment variable and option names the migration tooling reports in errors."""

from rootcause.settings import ENV_PREFIX

MIGRATION_URL_VARIABLE = f"{ENV_PREFIX}MIGRATION_DATABASE_URL"
ADMIN_URL_VARIABLE = f"{ENV_PREFIX}ADMIN_DATABASE_URL"
ADMIN_URL_OPTION = "--admin-url"
PASSWORD_VARIABLE = "PGPASSWORD"
