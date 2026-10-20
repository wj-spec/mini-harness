class FakeModel:
    def __init__(self, responses):
        self.responses = responses
        self.index = 0

    def __call__(self, prompt, **kwargs):
        r = self.responses[self.index]
        self.index += 1
        return r
class Response:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []   


def run(user_message, model, tools, max_steps=5, cancel_after=None, log_path=None):
    agent = Agent(model, Toolmanager(tools))
    state = AgentState()
    state.messages.append(user_message)
    for step in range(max_steps):
        response = model(state.messages)
        if response.content:
            state.final_answer = response.content
            return state
        for tool_call in response.tool_calls:
            tool_response = agent.tools.run_tool(tool_call.name)
            state.messages.append(f"Tool Response: {tool_response}")
        if cancel_after is not None and step + 1 >= cancel_after:
            state.status = "cancelled"
            return state
    state.status = "stopped"
    return state