import json
import re
from typing import Any, Dict, Optional

def parse_json_from_llm_output(text: str) -> Dict[str, Any]:
    """
    Robustly parse JSON from LLM output, handling Markdown code blocks and raw text.
    
    Args:
        text (str): The raw string output from the LLM.
        
    Returns:
        Dict[str, Any]: The parsed JSON object.
        
    Raises:
        ValueError: If valid JSON cannot be found or parsed.
    """
    cleaned_text = text.strip()
    
    # 1. Try parsing directly (fastest)
    try:
        return json.loads(cleaned_text)
    except json.JSONDecodeError:
        pass
    
    # 2. Extract from Markdown code blocks (```json ... ```)
    # This regex handles ```json, ```JSON, or just ```
    pattern = r"```(?:json|JSON)?\s*([\s\S]*?)\s*```"
    match = re.search(pattern, cleaned_text)
    if match:
        json_str = match.group(1)
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass
            
    # 3. Last resort: Find the outermost curly braces
    start = cleaned_text.find("{")
    end = cleaned_text.rfind("}")
    if start != -1 and end != -1:
        json_str = cleaned_text[start : end + 1]
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass
            
    raise ValueError(f"Could not parse valid JSON from output: {text[:100]}...")