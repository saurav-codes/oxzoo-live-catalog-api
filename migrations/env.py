from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

import db

if context.config.config_file_name:
    fileConfig(context.config.config_file_name)

with create_engine(db.database_url(), connect_args={"connect_timeout": 10}).connect() as connection:
    context.configure(connection=connection, target_metadata=db.Base.metadata)
    with context.begin_transaction():
        context.run_migrations()
