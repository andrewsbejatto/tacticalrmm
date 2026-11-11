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


# rows of the old numbering: their files no longer exist
OLD = ("0043_user_language", "0044_role_can_use_quick_support", "0045_role_can_use_remote_access")


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0044_copy_mesh_permission_to_file_browser"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    _add_column("accounts_user", "language", "varchar(10) NOT NULL", "''"),
                    reverse_sql="ALTER TABLE accounts_user DROP COLUMN IF EXISTS language;",
                ),
            ],
            state_operations=[
                migrations.AddField(
                    model_name="user",
                    name="language",
                    field=models.CharField(
                        blank=True,
                        choices=[
                            ("", "Auto"),
                            ("en-US", "English"),
                            ("pt-BR", "Português (Brasil)"),
                        ],
                        default="",
                        max_length=10,
                    ),
                ),
            ],
        ),
        migrations.RunSQL(
            "DELETE FROM django_migrations WHERE app = 'accounts' AND name IN %s;" % (OLD,),
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
