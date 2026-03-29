import json
import requests
try:
    from qwen_agent.agents import Assistant
    from qwen_agent.tools.base import BaseTool, register_tool
except ImportError:
    print("Error: qwen-agent not installed. Please run: uv pip install qwen-agent")
    exit(1)

# --- Define LCM Tools ---

@register_tool('lcm_grep')
class LcmGrep(BaseTool):
    description = 'Search through conversation history for keywords, including compressed summaries.'
    parameters = [{
        'name': 'query',
        'type': 'string',
        'description': 'The keyword or phrase to search for.',
        'required': True
    }]
    def call(self, params: str, **kwargs) -> str:
        q = json.loads(params)['query']
        resp = requests.get(f"http://localhost:8000/grep?q={q}")
        return json.dumps(resp.json(), indent=2)

@register_tool('lcm_expand')
class LcmExpand(BaseTool):
    description = 'Retrieve the full, lossless content of a specific summary node.'
    parameters = [{
        'name': 'item_id',
        'type': 'string',
        'description': 'The ID of the summary (e.g., sum_1d686fab) to expand.',
        'required': True
    }, {
        'name': 'query',
        'type': 'string',
        'description': 'Optional specific query to filter the expansion results.',
        'required': False
    }]
    def call(self, params: str, **kwargs) -> str:
        p = json.loads(params)
        url = f"http://localhost:8000/expand?id={p['item_id']}"
        if p.get('query'): url += f"&q={p['query']}"
        resp = requests.get(url)
        return json.dumps(resp.json(), indent=2)

@register_tool('lcm_describe')
class LcmDescribe(BaseTool):
    description = 'Get technical lineage and metadata for any node (summary or message) in history.'
    parameters = [{
        'name': 'item_id',
        'type': 'string',
        'description': 'The node ID (number for message, sum_xxx for summary).',
        'required': True
    }]
    def call(self, params: str, **kwargs) -> str:
        i = json.loads(params)['item_id']
        resp = requests.get(f"http://localhost:8000/describe?id={i}")
        return json.dumps(resp.json(), indent=2)

# --- Configuration ---

llm_cfg = {
    'model': 'qwen3.5:2b',
    'model_type': 'qwenvl_oai',
    'model_server': 'http://localhost:8000/v1',  
    'api_key': 'EMPTY',
    'generate_cfg': {'use_raw_api': True},
}

desktop_path = os.path.join(os.environ["USERPROFILE"], "Desktop") if os.name == "nt" else "/Users/xxxx/Desktop"

# Combine both Filesystem and LCM Tools
tools = [
    'lcm_grep', 'lcm_expand', 'lcm_describe',
    {
        'mcpServers': {
            "filesystem": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-filesystem", desktop_path]
            }
        }
    }
]

# Define Agent
bot = Assistant(llm=llm_cfg, function_list=tools)

print(f"--- Qwen Agent Started with LCM MEMORY TOOLS ---")
print(f"--- Tools: [lcm_grep, lcm_expand, lcm_describe, filesystem] ---")
print(f"--- Filesystem Root: {desktop_path} ---\n")

# Run some test interaction
messages = [{'role': 'user', 'content': 'You have memory tools like lcm_grep and lcm_expand. If you see a [SUMMARY] node in your context, use them to see details. Now, search my history for any mention of "Chapter 1" using grep.'}]
for response in bot.run(messages=messages):
    pass
print(f"AI: {response}\n")
