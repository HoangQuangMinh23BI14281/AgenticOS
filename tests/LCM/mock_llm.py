
import httpx
from agenticos.layer1_memory.LCM import TokenizerProtocol, CompletionResult, CompletionContentBlock

class SimpleTokenizer(TokenizerProtocol):
    def encode(self, text: str) -> list[int]: 
        # Average ~3 characters per token for more realistic compression metrics
        return [0] * max(1, len(text) // 3)
    def decode(self, tokens: list[int]) -> str: 
        return ""

class OllamaCompleter:
    def __init__(self, model: str = "qwen3.5:2b", base_url: str = "http://localhost:11434"):
        self.model = model
        self.base_url = f"{base_url}/api/chat"
        self.status_url = f"{base_url}/api/tags"

    async def check_health(self) -> bool:
        """Verify Ollama is reachable and model is available."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(self.status_url)
                if resp.status_code != 200: return False
                models = [m["name"] for m in resp.json().get("models", [])]
                return self.model in models or f"{self.model}:latest" in models
        except Exception:
            return False

    async def complete(self, *, model: str | None = None, messages: list[dict], system: str | None = None, max_tokens: int = 4096, temperature: float | None = 0.7, provider: str | None = None) -> CompletionResult:
        target_model = model or self.model
        ollama_messages = []
        if system:
            ollama_messages.append({"role": "system", "content": system})
        for msg in messages:
            ollama_messages.append({"role": msg["role"], "content": msg["content"]})

        payload = {
            "model": target_model,
            "messages": ollama_messages,
            "stream": False,
            "options": {"num_predict": max_tokens, "temperature": temperature or 0.7}
        }
        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                response = await client.post(self.base_url, json=payload)
                response.raise_for_status()
                data = response.json()
                message = data.get("message", {})
                content = message.get("content", "")
                thinking = message.get("thinking", "")
                
                # Nếu có phần suy nghĩ, ta lồng nó vào như cách Deepseek-R1 thường làm hoặc ghép lại
                full_response = content
                if thinking:
                    full_response = f"<think>\n{thinking}\n</think>\n\n{content}"
                
                return CompletionResult(content=[CompletionContentBlock(type="text", text=full_response)])
        except Exception as e:
            print(f"[OLLAMA ERROR] {e}")
            return CompletionResult(error={"message": str(e)})
