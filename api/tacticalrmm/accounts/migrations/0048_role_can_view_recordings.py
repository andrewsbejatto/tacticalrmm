"""
Role permission "View Recordings" (recordings app). Same idempotent SQL as
0045-0047: the column keeps a database default so the stock code run by
update.sh can still insert roles.
"""

from django.db import migrations, models


def _add_column(table: str, column: str, ddl: str, default: str, copy_from: str | None = None) -> str:
    """
    The column keeps a database default: the official update.sh runs the
    stock Tactical RMM code (which doesn't know these columns) before this
    package is applied again, and its inserts must not fail. Django ignores
    database defaults.
    """
    copy = f"UPDATE {table} SET {column} = true WHERE {copy_from} = true;" if copy_from else ""
    return f"""
DO $do$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = '{table}' AND column_name = '{column}'
    ) THEN
        ALTER TABLE {table} ADD COLUMN {column} {ddl} DEFAULT {default};
        {copy}
    END IF;
    ALTER TABLE {table} ALTER COLUMN {column} SET DEFAULT {default};
END
$do$;
"""



class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0047_role_can_use_remote_access"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    _add_column(
                        "accounts_role",
                        "can_view_recordings",
                        "boolean NOT NULL",
                        "false",
                    ),
                    reverse_sql="ALTER TABLE accounts_role DROP COLUMN IF EXISTS can_view_recordings;",
                ),
            ],
            state_operations=[
                migrations.AddField(
                    model_name="role",
                    name="can_view_recordings",
                    field=models.BooleanField(default=False),
                ),
            ],
        ),
    ]
