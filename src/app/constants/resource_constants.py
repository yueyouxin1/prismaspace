# app/constants/resource_constants.py

RESOURCE_TYPES_DATA = [
    {'name': 'agent', 'label': '智能体', 'is_application': True},
    {'name': 'uiapp', 'label': 'UI应用', 'is_application': True},
    {'name': 'tool', 'label': '工具', 'is_application': False},
    {'name': 'tenantdb', 'label': '数据库', 'is_application': False},
    {'name': 'vectordb', 'label': '知识库', 'is_application': False}
]