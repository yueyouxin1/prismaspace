# scripts/collect_code.py
import pathlib

# --- [新] 自动计算项目根目录 ---
# __file__ 是当前脚本的路径 (e.g., /path/to/project/scripts/collect_code.py)
# .parent 是 'scripts' 目录
# .parent.parent 就是项目根目录
PROJECT_ROOT = pathlib.Path(__file__).parent.parent

# --- 配置 ---
# 源目录现在相对于项目根目录
SOURCE_DIRECTORY = PROJECT_ROOT / "tests"
# 输出文件也放在项目根目录
OUTPUT_FILENAME = PROJECT_ROOT / "full_app_test.txt"
# 要排除的目录名称
EXCLUDE_DIRS = {"__pycache__"}

def collect_project_code():
    """
    遍历指定目录，将所有 .py 文件的内容聚合到一个输出文件中。
    """
    print(f"Project root identified as: {PROJECT_ROOT}")
    print(f"Starting to collect code from: '{SOURCE_DIRECTORY}'...")
    
    # 使用 w (write) 模式，在开始时清空或创建文件
    with open(OUTPUT_FILENAME, "w", encoding="utf-8") as f:
        f.write(f"# Code aggregation for project: {PROJECT_ROOT.name}\n")
        f.write(f"# Source directory: {SOURCE_DIRECTORY}\n")
        f.write("# ===============================================\n\n")

    # 获取所有 .py 文件并按路径排序
    all_py_files = sorted(SOURCE_DIRECTORY.rglob("*.py"))
    
    file_count = 0
    for file_path in all_py_files:
        if any(part in EXCLUDE_DIRS for part in file_path.parts):
            continue
        
        file_count += 1
        # 使用 relative_to 来显示更清晰的相对路径
        relative_path = file_path.relative_to(PROJECT_ROOT)
        print(f"  -> Adding: {relative_path}")
        
        with open(OUTPUT_FILENAME, "a", encoding="utf-8") as outfile:
            outfile.write(f"# {'=' * 20} FILE: {relative_path} {'=' * 20}\n")
            outfile.write("\n")
            
            try:
                content = file_path.read_text(encoding="utf-8")
                outfile.write(content)
            except Exception as e:
                outfile.write(f"\n!!! ERROR READING FILE: {e} !!!\n")
            
            outfile.write("\n\n")
            outfile.write(f"# {'-' * 60}\n\n")

    print("\n-------------------------------------------------")
    print(f"Success! Aggregated {file_count} files into '{OUTPUT_FILENAME.name}'.")
    print("The file is located in your project root directory.")
    print("You can now copy its content for analysis.")
    print("-------------------------------------------------")


if __name__ == "__main__":
    if not SOURCE_DIRECTORY.is_dir():
        print(f"Error: Source directory '{SOURCE_DIRECTORY}' not found.")
        print("Please ensure the 'src/app' directory exists and this script is in the 'scripts' folder.")
    else:
        collect_project_code()