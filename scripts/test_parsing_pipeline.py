# scripts/test_parsing_pipeline.py

import asyncio
import os
from typing import List

# [关键] 确保脚本可以找到 app 模块
# 这通常需要将项目根目录添加到 Python 路径中
import sys
# 获取当前脚本的绝对路径
current_path = os.path.dirname(os.path.abspath(__file__))
# 获取项目根目录 (scripts目录的上级目录)
project_root = os.path.dirname(current_path)
# 将项目根目录添加到sys.path
sys.path.insert(0, project_root)
# [注意] 在VSCode等IDE中直接运行时，可能需要配置.env文件或PYTHONPATH

from app.engine.parsing.base import BasePolicy, ParserPolicy, ChunkerPolicy, DocumentChunk
from app.engine.parsing.main import ProcessingPipeline

# --- 配置区 ---
# 确保你的 Tika 服务正在这个地址运行
TIKA_URL = "http://localhost:9998/tika" 
# 测试文件路径 (相对于项目根目录)
TEST_FILE_PATH = os.path.join(project_root, "test_data", "sample.pdf") 

async def run_pipeline(
    policy: BasePolicy, 
    file_path: str = None, 
    file_url: str = None,
    file_content: bytes = None
) -> List[DocumentChunk]:
    """一个辅助函数，用于运行流水线并打印结果。"""
    print("\n" + "="*80)
    print(f"🚀 EXECUTING PIPELINE with Policy:")
    print(f"   - Parser: {policy.parser.parser_name}")
    print(f"   - Chunkers: {[c.chunker_name for c in policy.chunkers]}")
    if file_path:
        print(f"   - Input File: {file_path}")
    elif file_url:
        print(f"   - Input URL: {file_url}")
    print("="*80)

    pipeline = ProcessingPipeline()
    
    try:
        # --- 读取文件内容 ---
        if file_path:
            with open(file_path, "rb") as f:
                file_content = f.read()

        # --- 执行流水线 ---
        chunks = await pipeline.execute(
            file_url=file_url,
            file_content=file_content,
            policy=policy
        )

        # --- 打印结果 ---
        print(f"\n✅ PIPELINE COMPLETED SUCCESSFULLY!")
        print(f"   - Total Chunks Generated: {len(chunks)}")
        
        for i, chunk in enumerate(chunks):
            print("-" * 50)
            print(f"  Chunk #{i+1}:")
            print(f"    - Source Chunker: {chunk.source_chunker}")
            print(f"    - Type: {chunk.chunk_type}")
            print(f"    - Length: {chunk.chunk_length}")
            print(f"    - Metadata: {chunk.metadata}")
            # 打印内容的前150个字符
            content_preview = chunk.content.replace('\n', ' ').strip()
            print(f"    - Content Preview: '{content_preview[:150]}'")
        
        return chunks

    except Exception as e:
        print(f"\n❌ PIPELINE FAILED!")
        print(f"   - Error: {e}")
        import traceback
        traceback.print_exc()
        return []

async def main():
    """主测试函数，编排不同的测试场景。"""
    
    # --- 场景 1: 完整的 PDF 解析与并行分块 ---
    # 这个策略会先用 simple_parser (Tika) 将 PDF 解析为 XHTML (因为它是PDF)，
    # 然后并行地调用 xml_chunker (按页分块) 和 simple_chunker (按字符数分块)。
    full_pdf_policy = BasePolicy(
        parser=ParserPolicy(
            parser_name="simple_parser_v1",
            allowed_mime_types=["application/pdf"],
            params={"tika_url": TIKA_URL}
        ),
        chunkers=[
            ChunkerPolicy(chunker_name="html_chunker_v1", params={}),
            ChunkerPolicy(chunker_name="simple_chunker_v1", params={"chunk_size": 200})
        ]
    )
    await run_pipeline(policy=full_pdf_policy, file_path=TEST_FILE_PATH)

    # --- 场景 2: 纯文本解析，无分块 ---
    # 这个策略只会调用解析器，然后直接将整个文档内容作为一个块返回。
    text_only_policy = BasePolicy(
        parser=ParserPolicy(
            parser_name="simple_parser_v1",
            allowed_mime_types=["text/plain", "application/pdf"], # 假设 Tika 能处理
            params={"tika_url": TIKA_URL}
        ),
        chunkers=[] # 空的分块器列表
    )
    # 模拟直接传入文本内容
    text_content = b"This is a short sentence. This is a longer second sentence that we will test."
    await run_pipeline(policy=text_only_policy, file_content=text_content)
    
    # --- 场景 3: 测试 MIME 类型不匹配 ---
    # 策略只允许 text/plain，但我们传入一个 PDF 文件。预期应该跳过解析，返回空结果。
    mismatch_policy = BasePolicy(
        parser=ParserPolicy(
            parser_name="simple_parser_v1",
            allowed_mime_types=["text/plain"], # 不包含 application/pdf
            params={"tika_url": TIKA_URL}
        ),
        chunkers=[ChunkerPolicy(chunker_name="simple_chunker_v1")]
    )
    await run_pipeline(policy=mismatch_policy, file_path=TEST_FILE_PATH)

    # --- 场景 4: 测试 URL 下载 ---
    # (使用一个公开可访问的 PDF URL)
    # 注意：确保这个URL是有效的
    # pdf_url = "https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf"
    # await run_pipeline(policy=full_pdf_policy, file_url=pdf_url)

if __name__ == "__main__":
    # 检查测试文件是否存在
    if not os.path.exists(TEST_FILE_PATH):
        print(f"❌ Error: Test file not found at '{TEST_FILE_PATH}'")
        print("Please create a 'test_data' directory in the project root and place a 'sample.pdf' file in it.")
    else:
        asyncio.run(main())