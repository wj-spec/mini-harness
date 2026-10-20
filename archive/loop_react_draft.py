
 
class Agent:
    """ 一个简单的ReAct智能体"""
    def __init__(self, model, tools):
        self.model = model
        self.tools = tools
        self.state = AgentState()

    def _is_final_answer(self, response: str) -> bool:
        """ 判断是否是最终答案"""
        return "Final Answer:" in response
    
    def agent_loop(self, user_messaege: str, MAX_STEPS: int = 5, user=None):
        """ 手写的ReAct循环"""
        system_message = """
        你是一个智能体，能够根据用户的输入进行推理和行动。你可以使用工具来获取信息，并根据这些信息生成最终答案。请确保在回答中包含“Final Answer:”来标识你的最终答案。
        如果你需要使用工具，请在回答中包含“Action:”来标识你要执行的操作。你可以多次使用工具，直到你认为你有足够的信息来生成最终答案。
        """
        self.state.messages.append(system_message)
        for _ in range(MAX_STEPS):
            response = self.model.chat(self.state.messages)
            if self._is_final_answer(response):
                return response
            if "Action:" in response:
                action = response.split("Action:")[1].strip()
                tool_response = self.tools.run_tool(action)
                self.state.messages.append(f"Tool Response: {tool_response}")
            if "Observation:" in response:
                observation = response.split("Observation:")[1].strip()
                self.state.messages.append(f"Observation: {observation}")
    async def run(self, user_messaege: str, user):
        """ 运行ReAct循环"""
        response = await self.agent_loop(user_messaege, user=user)
        return response

class AgentState:
    """ 用于存储循环状态的类"""
    def __init__(self):
        self.messages = []
        self.step_count = 0
        self.final_answer = None
        self.memory = {}


