import sqlalchemy as sa
from alembic import op

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'documents',
        sa.Column('kind', sa.String(), primary_key=True),
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('body', sa.JSON(), nullable=False),
    )
    op.create_table(
        'events',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('game_id', sa.String(), nullable=False),
        sa.Column('sequence', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(), nullable=False),
        sa.Column('data', sa.JSON(), nullable=False),
        sa.Column('actor_ids', sa.JSON(), nullable=False),
        sa.Column('is_private', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.String(), nullable=False),
        sa.UniqueConstraint('game_id', 'sequence'),
    )
    op.create_index('ix_events_game_id', 'events', ['game_id'])
    op.create_table(
        'receipts',
        sa.Column('invitation_id', sa.String(), primary_key=True),
        sa.Column('submission_id', sa.String(), unique=True, nullable=False),
        sa.Column('response_hash', sa.String(), nullable=False),
        sa.Column('result', sa.JSON(), nullable=False),
    )


def downgrade():
    op.drop_table('receipts')
    op.drop_table('events')
    op.drop_table('documents')
