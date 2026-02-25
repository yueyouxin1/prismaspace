# scripts/collect_code.py
import pathlib
import argparse

# --- [1] 项目根目录设定 ---
PROJECT_ROOT = pathlib.Path(__file__).parent.parent

# --- [2] 目标列表配置 (在此处填入您想要收集的目录或文件) ---
# 如果命令行没有传入参数，脚本将默认使用此列表
TARGET_LIST = [
"src/app/engine"
]

# --- [3] 输出配置 ---
OUTPUT_FILENAME = PROJECT_ROOT / "full_app_code.txt"

# 忽略的文件夹
EXCLUDE_DIRS = {
    "__pycache__", ".git", ".idea", ".vscode", "node_modules", 
    "venv", ".venv", "env", "dist", "build", "egg-info", "migrations"
}

# 允许的文件后缀 (防止读取图片或二进制文件)
ALLOWED_EXTENSIONS = {
    ".py", ".json", ".yaml", ".yml", ".toml", ".ini", ".env.example",
    ".md", ".txt", 
    ".js", ".ts", ".html", ".css", ".sql", ".sh", "Dockerfile"
}

def is_allowed_file(file_path: pathlib.Path) -> bool:
    """过滤逻辑：检查后缀、排除目录、排除输出文件本身"""
    if file_path.name.startswith("."): return False # 忽略隐藏文件
    if file_path.suffix.lower() not in ALLOWED_EXTENSIONS and file_path.name not in ALLOWED_EXTENSIONS:
        return False
    if any(part in EXCLUDE_DIRS for part in file_path.parts):
        return False
    if file_path.resolve() == OUTPUT_FILENAME.resolve():
        return False
    return True

def resolve_files(paths_list):
    """解析输入的路径列表，展平为具体的文件列表"""
    collected_files = set()
    
    for p_str in paths_list:
        # 兼容绝对路径和相对路径
        path_obj = pathlib.Path(p_str)
        if not path_obj.is_absolute():
            path_obj = (PROJECT_ROOT / p_str).resolve()
        
        if not path_obj.exists():
            print(f"[Warn] Path not found: {p_str}")
            continue

        if path_obj.is_file():
            if is_allowed_file(path_obj):
                collected_files.add(path_obj)
        elif path_obj.is_dir():
            for f in path_obj.rglob("*"):
                if f.is_file() and is_allowed_file(f):
                    collected_files.add(f)
                    
    # 排序确保输出顺序稳定
    return sorted(list(collected_files))

def collect_code(input_targets):
    """核心执行函数"""
    print(f"Project root: {PROJECT_ROOT}")
    
    # 1. 解析文件
    files = resolve_files(input_targets)
    if not files:
        print("No valid files found to collect. Please check 'TARGET_LIST' or arguments.")
        return

    print(f"Collecting {len(files)} files...")

    # 2. 写入文件
    with open(OUTPUT_FILENAME, "w", encoding="utf-8") as out:
        # 头部简要说明
        out.write(f"# Context Export\n")
        out.write(f"# Root: {PROJECT_ROOT.name}\n\n")

        for file_path in files:
            try:
                # 获取相对路径作为标题
                try:
                    rel_path = file_path.relative_to(PROJECT_ROOT).as_posix()
                except ValueError:
                    rel_path = file_path.name

                print(f"  -> {rel_path}")

                # 读取内容
                content = file_path.read_text(encoding="utf-8")
                
                # 简单的语言推断，用于 markdown 标记
                ext = file_path.suffix.lower().replace(".", "")
                lang = "python" if ext == "py" else ext
                if lang == "yml": lang = "yaml"

                # --- 写入格式 ---
                # 格式：文件名标题 -> 代码块
                out.write(f"# {'='*10} FILE: {rel_path} {'='*10}\n")
                out.write(f"```{lang}\n")
                out.write(content)
                if not content.endswith("\n"): out.write("\n") # 补全换行
                out.write("```\n\n")

            except Exception as e:
                out.write(f"\n# !!! ERROR READING: {rel_path} ({e}) !!!\n\n")

    print("-" * 40)
    print(f"Done. Output file: {OUTPUT_FILENAME}")
    print("-" * 40)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*", help="Specific paths to collect")
    args = parser.parse_args()

    # 优先使用命令行参数，如果没有，则使用脚本内配置的 TARGET_LIST
    targets = args.paths if args.paths else TARGET_LIST
    
    collect_code(targets)