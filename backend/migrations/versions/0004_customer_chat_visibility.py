"""Allow customers to remove chats from their own history, retaining merchant records."""

from alembic import op
import sqlalchemy as sa


revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("ticket_access", sa.Column("customer_deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    raise RuntimeError("Customer visibility choices must be retained; restore a verified backup to roll back.")

