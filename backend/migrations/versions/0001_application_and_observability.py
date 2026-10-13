"""Create application and telemetry schema from the SQLAlchemy metadata."""

from alembic import op

from app.models import Base

revision = "0001_application_and_observability"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    raise RuntimeError("Automatic schema downgrade is disabled to protect business and audit records")