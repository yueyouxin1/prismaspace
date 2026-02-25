# app/db/base.py

from sqlalchemy import MetaData
from sqlalchemy.orm import declarative_base

# [关键] 定义一个命名约定
# 这个约定会为所有约束自动生成名称，解决了 drop_all 时的命名问题。
naming_convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s"
}

# [关键] 创建一个带有命名约定的 MetaData 对象
metadata_obj = MetaData(naming_convention=naming_convention)

# [关键] 将 metadata 对象传递给 declarative_base
Base = declarative_base(metadata=metadata_obj)