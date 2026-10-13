"""${message}. Frozen schema revision; do not import live application model definitions.

Revision: ${up_revision}
Parent: ${down_revision}
"""
from alembic import op, context
from contextlib import contextmanager
import sqlalchemy as sa
import pgvector.sqlalchemy
${imports if imports else ""}

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def create_table_if_absent(name, *columns, **kwargs):
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table(name):
        expected = {column.name for column in columns if isinstance(column, sa.Column)}
        actual = {column["name"] for column in inspector.get_columns(name)}
        if not expected.issubset(actual):
            raise RuntimeError(f"Legacy table {name} has incompatible columns; refusing blind adoption")
        return
    op.create_table(name, *columns, **kwargs)


def create_index_if_absent(name, table, columns, **kwargs):
    if name not in {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)}:
        op.create_index(name, table, columns, **kwargs)


def upgrade():
    # Scope this adapter to the adoption revisions; do not mutate Alembic globals.
    class AdoptExisting:
        create_table = staticmethod(create_table_if_absent)

        @staticmethod
        @contextmanager
        def batch_alter_table(table, **kwargs):
            class Indexes:
                def f(self, name):
                    return name

                def create_index(self, name, columns, **options):
                    create_index_if_absent(name, table, columns, **options)
            yield Indexes()
    op = AdoptExisting()
    ${upgrades if upgrades else "pass"}


def downgrade():
    if not context.config.attributes.get("allow_destructive"):
        raise RuntimeError("Schema rollback may remove data: restore into a new database or explicitly approve a tested rollback")
    ${downgrades if downgrades else "pass"}
