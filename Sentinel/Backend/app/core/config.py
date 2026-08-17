import os

# Postgres connection string.
# Override with DATABASE_URL env var in production.
# Format: postgresql://user:password@host:port/dbname
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://rohanbrown@localhost/sentinel"
)

# UTC offset for the deployment timezone.
# Jamaica is UTC-5 (no daylight saving). Override via TIMEZONE_OFFSET_HOURS env var.
TIMEZONE_OFFSET_HOURS = int(os.getenv("TIMEZONE_OFFSET_HOURS", "-5"))
