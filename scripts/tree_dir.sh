#!/bin/bash

# 简洁版本
dir="${1:-.}"
output_file="directory_tree.txt"

echo "正在扫描目录: $dir"
tree -I "__pycache__" "$dir" > "$output_file" 2>&1

if [ $? -eq 0 ]; then
    echo "✓ 目录树已保存到: $output_file"
    echo "总行数: $(wc -l < "$output_file")"
else
    echo "✗ 执行失败，请检查:"
    echo "1. tree命令是否安装"
    echo "2. 目录路径是否正确"
fi