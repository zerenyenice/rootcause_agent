"""Declarative ``Base`` and its deterministic constraint naming convention."""


def test_base_is_a_sqlalchemy_declarative_base():
    from sqlalchemy.orm import DeclarativeBase

    from rootcause.db.base import Base

    assert issubclass(Base, DeclarativeBase)


def test_naming_convention_pins_pk_fk_and_unique_templates():
    from rootcause.db.base import Base

    convention = Base.metadata.naming_convention

    assert convention["pk"] == "pk_%(table_name)s"
    assert convention["fk"] == "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"
    assert convention["uq"] == "uq_%(table_name)s_%(column_0_N_name)s"
    assert "ix" in convention
    assert "ck" in convention


def test_naming_convention_names_constraints_of_a_new_table():
    import sqlalchemy as sa
    from sqlalchemy.schema import CreateTable

    from rootcause.db.base import Base

    metadata = sa.MetaData(naming_convention=Base.metadata.naming_convention)
    sa.Table("parent", metadata, sa.Column("id", sa.Integer, primary_key=True))
    child = sa.Table(
        "child",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("parent_id", sa.ForeignKey("parent.id")),
        sa.Column("code", sa.String, unique=True),
    )

    ddl = str(CreateTable(child))

    assert "CONSTRAINT pk_child PRIMARY KEY (id)" in ddl
    assert "CONSTRAINT fk_child_parent_id_parent FOREIGN KEY(parent_id)" in ddl
    assert "CONSTRAINT uq_child_code UNIQUE (code)" in ddl
