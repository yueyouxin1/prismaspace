import os
import pymysql

def check_flag_file(flag_file_path):
    # 检查标识文件是否存在
    return os.path.exists(flag_file_path)

def create_flag_file(flag_file_path):
    # 创建标识文件
    with open(flag_file_path, 'w') as flag_file:
        flag_file.write('Initialized')

def check_database_exists(host, port, user, password, db_name):
    # 检查数据库是否存在
    connection = pymysql.connect(host=host,
                                 port=port,
                                 user=user,
                                 password=password,
                                 database='')
    
    try:
        with connection.cursor() as cursor:
            cursor.execute("SHOW DATABASES")
            databases = cursor.fetchall()
            return (db_name,) in databases
    finally:
        connection.close()

def create_database(host, port, user, password, db_name):
    # 创建数据库
    connection = pymysql.connect(host=host,
                                 port=port,
                                 user=user,
                                 password=password,
                                 database='')
    
    try:
        with connection.cursor() as cursor:
            sql = f"CREATE DATABASE IF NOT EXISTS {db_name}"
            cursor.execute(sql)
        
        # 提交事务
        connection.commit()
    finally:
        connection.close()

def check_database_empty(host, port, user, password, db_name):
    # 检查数据库是否为空
    connection = pymysql.connect(host=host,
                                 port=port,
                                 user=user,
                                 password=password,
                                 database=db_name)
    
    try:
        with connection.cursor() as cursor:
            cursor.execute("SHOW TABLES")
            tables = cursor.fetchall()
            return len(tables) == 0
    finally:
        connection.close()

def import_backup(host, port, user, password, db_name, backup_file_path):
    if not backup_file_path:
        return
    # 导入备份文件
    connection = pymysql.connect(host=host,
                                 port=port,
                                 user=user,
                                 password=password,
                                 database=db_name)
    
    try:
        with open(backup_file_path, 'r') as file:
            sql_commands = file.read().split(';')
        
        with connection.cursor() as cursor:
            for command in sql_commands:
                try:
                    if command.strip():  # 忽略空命令
                        cursor.execute(command)
                except Exception as e:
                    print(f"Error executing command: {command}")
                    print(e)
        
        # 提交事务
        connection.commit()
    finally:
        # 关闭连接
        connection.close()

def initialize_database(host, port, user, password, db_name, backup_file_path, flag_file_path):
    # 初始化数据库
    if check_flag_file(flag_file_path):
        print("Database has already been initialized.")
        return
    
    if not check_database_exists(host, port, user, password, db_name):
        # 数据库不存在，创建它
        create_database(host, port, user, password, db_name)

    if not check_database_empty(host, port, user, password, db_name):
        # 数据库已存在但非空，提示用户
        print(f"Database '{db_name}' already exists and is not empty.")
        return

    # 数据库为空，开始导入备份
    import_backup(host, port, user, password, db_name, backup_file_path)
    
    # 创建标识文件
    create_flag_file(flag_file_path)