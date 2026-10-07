from alembic import op

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        'CREATE INDEX IF NOT EXISTS ix_documents_game_status '
        "ON documents (kind, json_extract(body, '$.game_id'), "
        "json_extract(body, '$.status'), json_extract(body, '$.actor_id'))"
    )


def downgrade():
    op.drop_index('ix_documents_game_status', table_name='documents')
