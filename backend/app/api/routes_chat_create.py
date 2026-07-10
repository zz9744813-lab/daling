"""对话式创建项目 API。

提供 POST /api/projects/chat-create 接口，支持：
- 多轮对话模式：用户与 AI 聊天，AI 引导用户描述故事设定
- 配置提取模式：从对话历史中提取项目配置（JSON）
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from app.model_gateway.base import LLMRequest, LLMMessage

router = APIRouter(prefix="/api/projects", tags=["chat-create"])


class ChatMessage(BaseModel):
    """单条对话消息。"""
    role: str  # "user" 或 "assistant"
    content: str


class ChatCreateRequest(BaseModel):
    """对话式创建项目请求。"""
    messages: list[ChatMessage]  # 对话历史
    extract: bool = False  # 是否提取配置（最后一次调用时为 True）


class ChatCreateResponse(BaseModel):
    """对话式创建项目响应。"""
    reply: str  # AI 回复
    config: Optional[dict] = None  # 提取的项目配置（extract=True 时返回）


# 对话引导系统提示词
SYSTEM_PROMPT = """你是一个专业的小说创作顾问。用户想创建一个小说项目，你需要通过对话引导用户描述他们想写的故事。

你的任务：
1. 通过友好的对话，逐步了解用户想写的故事
2. 每次只问 1-2 个问题，不要一次问太多
3. 了解这些关键信息（不需要全部问完，根据对话自然推进）：
   - 故事类型和题材
   - 主角设定（性格、背景）
   - 世界观设定
   - 核心冲突或主线
   - 期望的写作风格
   - AI 应该扮演什么角色（人设）
4. 当你认为信息足够时，告诉用户可以点击"生成配置"按钮
5. 对话要自然，像朋友聊天一样，不要像填表单

请记住：你是在帮用户梳理创作思路，不是在审问用户。"""


# 提取配置的系统提示词
EXTRACT_PROMPT = """根据以下对话内容，提取小说项目的配置信息。
以 JSON 格式返回，包含以下字段（如果对话中没有提到，用 null）：

{
  "title": "作品标题（从对话推断）",
  "genre": "类型/题材",
  "tone": "文风",
  "themes": ["主题1", "主题2"],
  "setting": "世界观设定描述",
  "custom_prompt": "AI角色/人设的系统提示词（根据用户描述的写作风格和期望生成）",
  "length_type": "short/medium/long/epic/mega",
  "pov": "写作视角",
  "tense": "叙事时态",
  "chapter_words": 3000
}

custom_prompt 字段特别重要：根据用户对话中描述的期望，生成一个完整的系统提示词，定义 AI 的角色、写作风格、行为准则。"""


# 检查是否有模型配置
async def get_llm():
    """获取 LLM 实例。

    优先从数据库的 llm_providers + model_bindings 表读取配置，
    回退到 OPENAI_COMPATIBLE_* 或 ANTHROPIC_* 环境变量。
    """
    import os
    from app.model_gateway.providers.openai_compatible import OpenAICompatibleProvider

    # 1. 尝试从数据库读取
    try:
        from app.core.database import async_session
        from sqlalchemy import select, text
        from app.db.models.provider import LlmProvider, ModelBinding

        async with async_session() as db:
            # 查全局绑定的任意 agent 角色（取第一个可用配置）
            stmt = (
                select(LlmProvider, ModelBinding)
                .join(ModelBinding, ModelBinding.provider_id == LlmProvider.id)
                .order_by(ModelBinding.project_id.is_not(None).desc())
                .limit(1)
            )
            result = await db.execute(stmt)
            row = result.first()
            if row is not None:
                provider_row, binding = row
                provider = OpenAICompatibleProvider(
                    base_url=provider_row.base_url or "",
                    api_key=provider_row.api_key_enc or "",
                )
                return (provider, binding.model_name), None
    except Exception as e:
        pass  # 数据库读取失败，降级到环境变量

    # 2. 回退到环境变量
    base_url = os.getenv("OPENAI_COMPATIBLE_BASE_URL", "")
    api_key = os.getenv("OPENAI_COMPATIBLE_API_KEY", "")
    model = os.getenv("OPENAI_COMPATIBLE_MODEL", "")
    if not all([base_url, api_key, model]):
        base_url = os.getenv("ANTHROPIC_BASE_URL", "")
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        model = os.getenv("ANTHROPIC_MODEL", "")
    if not all([base_url, api_key, model]):
        return None, "未配置 LLM 模型，请在设置页面配置模型，或在 .env 中设置 OPENAI_COMPATIBLE_* 环境变量"
    provider = OpenAICompatibleProvider(base_url=base_url, api_key=api_key)
    return (provider, model), None


@router.post("/chat-create", response_model=ChatCreateResponse)
async def chat_create(request: ChatCreateRequest):
    """对话式创建项目 - 多轮对话或提取配置。"""
    llm_info, err = await get_llm()
    if err:
        raise HTTPException(status_code=400, detail=err)

    provider, model = llm_info

    if request.extract:
        # 提取配置模式
        conversation = "\n".join([f"{'用户' if m.role=='user' else 'AI'}: {m.content}" for m in request.messages])
        messages = [
            LLMMessage(role="system", content=EXTRACT_PROMPT),
            LLMMessage(role="user", content=f"对话内容：\n{conversation}\n\n请提取项目配置："),
        ]
        req = LLMRequest(model=model, messages=messages, temperature=0.3, max_tokens=2000)
        resp = await provider.complete(req)
        import json, re
        text = resp.content
        # 提取 JSON
        match = re.search(r'\{[\s\S]*\}', text)
        config = json.loads(match.group()) if match else {}
        return ChatCreateResponse(reply="配置已生成", config=config)
    else:
        # 正常对话模式
        messages = [LLMMessage(role="system", content=SYSTEM_PROMPT)]
        for m in request.messages:
            messages.append(LLMMessage(role=m.role, content=m.content))
        req = LLMRequest(model=model, messages=messages, temperature=0.7, max_tokens=800)
        resp = await provider.complete(req)
        return ChatCreateResponse(reply=resp.content)
