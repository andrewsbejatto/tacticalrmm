"""
TRMM custom fields. Until Tactical RMM 1.6 they were migrations 0043-0045,
numbers now used by the official file browser migrations: renumbered after
them. The SQL only adds what is missing, so servers that already have the
columns (old numbering) migrate without errors and keep their values.
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
        ("accounts", "0046_role_can_use_quick_support"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                # new column: keeps remote control for roles that had it
                migrations.RunSQL(
                    _add_column(
                        "accounts_role",
                        "can_use_remote_access",
                        "boolean NOT NULL",
                        "false",
                        copy_from="can_use_mesh",
                    ),
                    reverse_sql="ALTER TABLE accounts_role DROP COLUMN IF EXISTS can_use_remote_access;",
                ),
            ],
            state_operations=[
                migrations.AddField(
                    model_name="role",
                    name="can_use_remote_access",
                    field=models.BooleanField(default=False),
                ),
            ],
        ),
    ]
