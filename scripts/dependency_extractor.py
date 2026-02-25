import ast
import os
import sys
import importlib.util

class DependencyExtractor:
    def __init__(self, project_root):
        self.project_root = os.path.abspath(project_root)
        self.visited_files = set()
        self.dependencies = {}  # 存储 {filepath: content}
        
        # 将项目根目录和 src 目录加入搜索路径，适配常见的 src 结构
        sys.path.insert(0, self.project_root)
        src_path = os.path.join(self.project_root, 'src')
        if os.path.exists(src_path):
            sys.path.insert(0, src_path)

    def resolve_import_to_file(self, module_name, base_file_path=None, level=0):
        """
        将 import 语句解析为具体的文件路径。
        """
        try:
            # 处理相对导入 (from . import x)
            if level > 0 and base_file_path:
                package = self._get_package_from_path(base_file_path)
                module_name = importlib.util.resolve_name('.' * level + (module_name or ''), package)
            
            # 使用 importlib 查找模块规范
            spec = importlib.util.find_spec(module_name)
            
            if spec and spec.origin:
                # 过滤掉非 python 文件（如 .so, .pyd）
                if not spec.origin.endswith('.py'):
                    return None
                
                # 过滤掉标准库和 site-packages (只保留项目内的代码)
                # 如果你需要提取第三方库源码，请注释掉下面这两行
                if 'site-packages' in spec.origin or 'dist-packages' in spec.origin:
                    return None
                if spec.origin.startswith(sys.base_prefix):
                    return None

                return os.path.abspath(spec.origin)
        except Exception:
            # 某些动态导入无法静态解析，忽略
            return None
        return None

    def _get_package_from_path(self, file_path):
        """根据文件路径推断其 Python package 名称 (用于相对导入解析)"""
        # 简单实现：假设从 sys.path 中能找到对应的包根
        # 这里的逻辑主要是为了辅助 resolve_name
        rel_path = os.path.relpath(os.path.dirname(file_path), self.project_root)
        if rel_path == '.':
            return ''
        return rel_path.replace(os.path.sep, '.')

    def parse_file(self, file_path):
        """递归解析文件"""
        file_path = os.path.abspath(file_path)
        
        if file_path in self.visited_files:
            return
        
        self.visited_files.add(file_path)
        
        if not os.path.exists(file_path):
            print(f"[Warn] 文件不存在: {file_path}")
            return

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # 记录内容
            self.dependencies[file_path] = content
            
            # 解析 AST
            tree = ast.parse(content, filename=file_path)
            
            for node in ast.walk(tree):
                target_file = None
                
                # 处理 import x.y
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        target_file = self.resolve_import_to_file(alias.name)
                        if target_file:
                            self.parse_file(target_file)
                            
                # 处理 from x import y
                elif isinstance(node, ast.ImportFrom):
                    # 如果 module 是 None，说明是 from . import x 这种形式
                    module_name = node.module if node.module else ''
                    target_file = self.resolve_import_to_file(module_name, file_path, node.level)
                    if target_file:
                        self.parse_file(target_file)

        except SyntaxError:
            print(f"[Error] 语法错误，无法解析: {file_path}")
        except UnicodeDecodeError:
            print(f"[Warn] 无法读取非文本文件: {file_path}")

    def run(self, entry_point):
        print(f"开始分析入口: {entry_point}")
        self.parse_file(entry_point)
        print(f"分析完成。共提取 {len(self.dependencies)} 个文件。")
        return self.dependencies

if __name__ == "__main__":
    # 配置
    PROJECT_ROOT = os.getcwd() # 假设你在项目根目录运行
    
    # 你的目标文件
    TARGET_FILE = "src/app/api/v1/uiapp.py"
    
    # 确保文件路径正确
    full_target_path = os.path.join(PROJECT_ROOT, TARGET_FILE)
    
    if not os.path.exists(full_target_path):
        print(f"错误: 找不到目标文件 {full_target_path}")
        sys.exit(1)

    extractor = DependencyExtractor(PROJECT_ROOT)
    results = extractor.run(full_target_path)

    # 输出结果：可以选择合并打印，或者写入一个 JSON/Text
    output_filename = "all_dependencies_content.txt"
    with open(output_filename, "w", encoding="utf-8") as f:
        for path, content in results.items():
            rel_path = os.path.relpath(path, PROJECT_ROOT)
            f.write(f"\n\n{'='*20} FILE: {rel_path} {'='*20}\n\n")
            f.write(content)
            print(f"提取: {rel_path}")

    print(f"\n结果已保存至: {output_filename}")