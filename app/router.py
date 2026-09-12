import logging
from typing import Literal, Optional, Tuple
from app.schemas import ChatRequest

logger = logging.getLogger("tollgate.router")


class ModelRouter:
    """
    Decides routing path between 'cache' and 'gemini' (or future 'local').
    Rules:
    1. force_route overrides everything.
    2. briefagent: always 'gemini', never 'cache' (multi-step dynamic agent).
    3. docground: eligible for 'cache' if it's a retrieval/FAQ question without dynamic tool call loops.
    """

    def decide_route(self, request: ChatRequest) -> Tuple[Literal["cache", "gemini"], str]:
        # 1. Force route override
        if request.force_route:
            return request.force_route, f"forced_by_request: force_route='{request.force_route}'"

        # 2. BriefAgent rule: always Gemini
        if request.app == "briefagent":
            return "gemini", "app_rule: briefagent requires live reasoning, cache disabled"

        # 3. If tools are requested: always Gemini
        if request.tools and len(request.tools) > 0:
            return "gemini", "tools_rule: tool calling requires live model execution"

        # 4. DocGround rule: try semantic cache first
        if request.app == "docground":
            return "cache", "app_rule: docground queries check semantic cache first"

        # TODO: Future hook for Route = local (e.g. Qwen local GGUF)
        # if settings.use_local_llm:
        #     return "local", "local_router_rule"

        return "gemini", "default_rule: route to Gemini"


model_router = ModelRouter()
