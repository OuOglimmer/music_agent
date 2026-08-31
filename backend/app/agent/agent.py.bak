# app/agent/agent.py

import os
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from dotenv import load_dotenv

load_dotenv()

llm = ChatOpenAI(
    model="qwen-turbo",  # 或 qwen-plus, qwen-max
    openai_api_key=os.getenv("DASHSCOPE_API_KEY"),
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    temperature=0.7,
    max_tokens=1024,
)

def process_request(user_input: str) -> str:
    if not os.getenv("DASHSCOPE_API_KEY"):
        return "Error: DASHSCOPE_API_KEY not configured in .env"
    try:
        messages = [
            SystemMessage(content="你是一个乐于助人的AI助手。"),
            HumanMessage(content=user_input),
        ]
        response = llm.invoke(messages)
        return response.content
    except Exception as e:
        return f"Error: {str(e)}"