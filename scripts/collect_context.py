import ast
import os
import sys
from pathlib import Path

class DependencyExtractor:
    def __init__(self, root_dir: str):
        self.root_dir = Path(root_dir).resolve()
        self.visited_files = set()
        self.file_contents = {}

    def is_local_module(self, module_name: str) -> bool:
        """
        简单判断是否为本地模块。
        如果 import 的名字对应项目根目录下的文件或文件夹，则视为本地。
        """
        if not module_name:
            return False
        
        # 将点号转换为路径，例如 app.core -> app/core
        base_path = module_name.split('.')[0]
        return (self.root_dir / base_path).exists()

    def resolve_path(self, module_name: str, current_file: Path, level: int = 0) -> Path | None:
        """
        将 import 语句解析为具体的文件路径。
        支持相对导入 (level > 0) 和绝对导入。
        """
        if level > 0:
            # 处理相对导入，如 from . import x (level=1), from .. import x (level=2)
            # current_file 是具体文件，parent 是它所在的目录
            search_dir = current_file.parent
            for _ in range(level - 1):
                search_dir = search_dir.parent
            
            if module_name:
                parts = module_name.split('.')
                path_probe = search_dir.joinpath(*parts)
            else:
                path_probe = search_dir
        else:
            # 处理绝对导入，如 from app.core import x
            parts = module_name.split('.')
            path_probe = self.root_dir.joinpath(*parts)

        # 尝试匹配 .py 文件
        py_file = path_probe.with_suffix('.py')
        if py_file.exists():
            return py_file
        
        # 尝试匹配 package (文件夹下的 __init__.py)
        init_file = path_probe / '__init__.py'
        if init_file.exists():
            return init_file
            
        return None

    def parse_imports(self, file_path: Path):
        if file_path in self.visited_files:
            return
        
        self.visited_files.add(file_path)
        
        try:
            content = file_path.read_text(encoding='utf-8')
            self.file_contents[str(file_path.relative_to(self.root_dir))] = content
            tree = ast.parse(content)
        except Exception as e:
            print(f"Error reading {file_path}: {e}")
            return

        for node in ast.walk(tree):
            target_path = None
            
            # 情况 1: import app.core.config
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if self.is_local_module(alias.name):
                        target_path = self.resolve_path(alias.name, file_path)
                        if target_path: self.parse_imports(target_path)

            # 情况 2: from app.core import config
            # 情况 3: from .base import base_impl_service (相对导入)
            elif isinstance(node, ast.ImportFrom):
                module_name = node.module or ""
                level = node.level # 0是绝对导入，1是 .，2是 ..
                
                # 如果是绝对导入，先检查是否是本地模块
                if level == 0 and not self.is_local_module(module_name):
                    continue

                target_path = self.resolve_path(module_name, file_path, level)
                if target_path:
                    self.parse_imports(target_path)

    def extract(self, entry_file: str):
        start_path = Path(entry_file).resolve()
        if not start_path.exists():
            print(f"File not found: {start_path}")
            return
            
        print(f"Starting analysis from: {start_path.name}...")
        self.parse_imports(start_path)
        return self.file_contents

# --- 使用示例 ---

if __name__ == "__main__":
    # 假设你的项目结构如下：
    # /project_root
    #    /src
    #       /app ...
    
    # 1. 设置你的项目根目录 (根据你的代码，应该是 src 文件夹的上级或 src 本身)
    # 如果你的代码都在 src/app 下，通常 root 设为 src 所在的目录
    PROJECT_ROOT = os.getcwd()
    
    # 2. 设置入口文件 (你提供的那个文件)
    ENTRY_FILE = "src/app/services/resource/agent/agent_service.py"

    if os.path.exists(ENTRY_FILE):
        extractor = DependencyExtractor(PROJECT_ROOT)
        print(f"开始分析入口: {ENTRY_FILE}")
        results = extractor.extract(ENTRY_FILE)
        print(f"分析完成。共提取 {len(results)} 个文件。")

        # 3. 输出结果 (例如输出到一个大文件)
        with open("full_context.txt", "w", encoding="utf-8") as f:
            for path, content in results.items():
                rel_path = os.path.relpath(path, PROJECT_ROOT)
                f.write(f"\n\n{'='*20} FILE: {path} {'='*20}\n\n")
                f.write(content)
                print(f"提取: {rel_path}")
        
        print(f"Extraction complete. Found {len(results)} related files.")
    else:
        print("Entry file not found. Please adjust paths in the script.")