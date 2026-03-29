import base64
import logging
from typing import Dict, Any, Optional
import httpx

logger = logging.getLogger(__name__)

class VLMAnalyzer:
    def __init__(self, api_key: str, base_url: str = "https://api.deepseek.com/v1"):
        self.api_key = api_key
        self.base_url = base_url

    async def analyze_step(
        self,
        screenshot_bytes: bytes,
        dom_snippet: str,
        action_type: str,
        target_element: Optional[str] = None
    ) -> Dict[str, Any]:
        """使用 VLM 分析用户操作意图"""

        # 编码截图
        screenshot_b64 = base64.b64encode(screenshot_bytes).decode('utf-8')

        prompt = f"""你是 RPA 专家。分析用户的浏览器操作并返回 JSON。

操作类型: {action_type}
目标元素: {target_element or 'N/A'}
DOM 片段:
{dom_snippet[:500]}

请返回 JSON 格式:
{{
  "description": "用户操作的自然语言描述",
  "is_parameterizable": true/false,
  "suggested_param_name": "参数名(如果可参数化)",
  "param_type": "string/number/boolean",
  "selector_hint": "推荐的选择器策略"
}}"""

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={
                        "model": "deepseek-chat",
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": prompt},
                                    {
                                        "type": "image_url",
                                        "image_url": {"url": f"data:image/png;base64,{screenshot_b64}"}
                                    }
                                ]
                            }
                        ],
                        "response_format": {"type": "json_object"}
                    }
                )
                resp.raise_for_status()
                result = resp.json()

                import json
                content = result["choices"][0]["message"]["content"]
                return json.loads(content)

        except Exception as e:
            logger.error(f"VLM analysis failed: {e}")
            return {
                "description": f"{action_type} on {target_element or 'element'}",
                "is_parameterizable": False,
                "suggested_param_name": None,
                "param_type": None,
                "selector_hint": None
            }
