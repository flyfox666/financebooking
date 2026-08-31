"""aux types typed format + seed defaults for existing books

Revision ID: 9c81d2e4a510
Revises: 755169e907e7
Create Date: 2026-08-31
"""

from alembic import op

revision = "9c81d2e4a510"
down_revision = "755169e907e7"
branch_labels = None
depends_on = None

DEFAULT_AUX_CONFIG = {
    "1121": "contact:customer",
    "1122": "contact:customer",
    "1123": "contact:supplier",
    "1221": "contact:employee,contact:other",
    "2202": "contact:supplier",
    "2203": "contact:customer",
    "2241": "contact:employee,contact:other",
}


def upgrade() -> None:
    from sqlalchemy import text

    conn = op.get_bind()
    conn.execute(
        text(
            "UPDATE account SET aux_types = 'contact:customer,contact:supplier' "
            "WHERE aux_types = 'contact'"
        )
    )
    for code, value in DEFAULT_AUX_CONFIG.items():
        conn.execute(
            text(
                "UPDATE account SET aux_types = :value "
                "WHERE code = :code AND (aux_types IS NULL OR aux_types = '')"
            ),
            {"value": value, "code": code},
        )


def downgrade() -> None:
    from sqlalchemy import text

    conn = op.get_bind()
    conn.execute(
        text(
            "UPDATE account SET aux_types = 'contact' "
            "WHERE aux_types LIKE 'contact:%'"
        )
    )
