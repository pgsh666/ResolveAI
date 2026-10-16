"""Persistent storefront, per-question routing and unified ticket numbers."""
from datetime import datetime, timedelta, timezone

from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("tickets", sa.Column("legacy_ticket_no", sa.String(30), nullable=True))
    op.add_column("knowledge_documents", sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.create_table("daily_sequences", sa.Column("key", sa.String(40), primary_key=True), sa.Column("value", sa.Integer(), nullable=False))
    op.create_table("products",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("name", sa.String(120), nullable=False), sa.Column("description", sa.Text(), nullable=False),
        sa.Column("image", sa.Text(), nullable=False), sa.Column("price_cents", sa.Integer(), nullable=False),
        sa.Column("stock", sa.Integer(), nullable=False), sa.Column("category", sa.String(50), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("store_orders",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("order_no", sa.String(30), nullable=False, unique=True),
        sa.Column("request_key", sa.String(150), nullable=False, unique=True),
        sa.Column("owner_id", sa.String(36), nullable=False), sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("customer_name", sa.String(100), nullable=False), sa.Column("product_id", sa.String(36), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("product_name", sa.String(120), nullable=False), sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price_cents", sa.Integer(), nullable=False), sa.Column("total_cents", sa.Integer(), nullable=False),
        sa.Column("refunded_cents", sa.Integer(), nullable=False), sa.Column("status", sa.String(30), nullable=False),
        sa.Column("address", sa.String(500), nullable=False), sa.Column("expected_delivery_days", sa.Integer(), nullable=False),
        sa.Column("transit_days", sa.Integer(), nullable=False), sa.Column("shipped_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("order_events",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("order_id", sa.String(36), sa.ForeignKey("store_orders.id"), nullable=False),
        sa.Column("status", sa.String(30), nullable=False), sa.Column("description", sa.String(500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("payment_ledger",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("order_id", sa.String(36), sa.ForeignKey("store_orders.id"), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False), sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(150), nullable=False, unique=True), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("question_routes",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("ticket_id", sa.String(36), sa.ForeignKey("tickets.id"), nullable=False),
        sa.Column("message_id", sa.String(80), nullable=False, unique=True), sa.Column("question", sa.Text(), nullable=False),
        sa.Column("request_time", sa.DateTime(timezone=True), nullable=False), sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("outcome", sa.String(40), nullable=False), sa.Column("route", sa.String(80), nullable=False),
        sa.Column("api_requests", sa.Integer(), nullable=False), sa.Column("model_requests", sa.Integer(), nullable=False),
        sa.Column("tool_requests", sa.Integer(), nullable=False), sa.Column("total_ms", sa.Float(), nullable=False),
        sa.Column("steps", sa.JSON(), nullable=False), sa.Column("degraded", sa.Boolean(), nullable=False),
        sa.Column("handed_off", sa.Boolean(), nullable=False), sa.Column("handoff_reasons", sa.JSON(), nullable=False), sa.Column("error", sa.Text()))
    for table, columns in {"products": ["workspace_id"], "store_orders": ["order_no", "owner_id", "workspace_id", "status"],
                           "order_events": ["order_id"], "payment_ledger": ["order_id"], "question_routes": ["ticket_id", "request_time"]}.items():
        for column in columns:
            op.create_index(f"ix_{table}_{column}", table, [column])
    bind = op.get_bind()
    tickets = bind.execute(sa.text("SELECT id, ticket_no, created_at FROM tickets ORDER BY created_at, id")).all()
    counters = {}
    # UUID references are untouched. Keep original labels for lookup and audit.
    for row in tickets:
        at = row.created_at if isinstance(row.created_at, datetime) else datetime.fromisoformat(str(row.created_at))
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        day = at.astimezone(timezone(timedelta(hours=8))).strftime("%y-%m-%d")
        counters[day] = counters.get(day, 0) + 1
        bind.execute(sa.text("UPDATE tickets SET legacy_ticket_no=:old, ticket_no=:number WHERE id=:id"),
                     {"old": row.ticket_no, "number": f"{day}-{counters[day]}", "id": row.id})
    for day, value in counters.items():
        bind.execute(sa.text("INSERT INTO daily_sequences (key, value) VALUES (:key, :value)"), {"key": f"ticket:{day}", "value": value})


def downgrade():
    raise RuntimeError("Commerce history must be retained; restore an explicitly verified backup to roll back.")
