


class Tool:
    """ 一个简单的工具类"""
    def __init__(self, name):
        self.name = name

    def run(self):
        """ 执行工具的操作"""
        return f"Tool {self.name} executed."
    def __str__(self):
        return f"Tool(name={self.name})"

def create_tools(tool_names):
    """ 创建工具的函数"""
    return {name: Tool(name) for name in tool_names}

class Toolmanager:
    """ 工具管理器类"""
    def __init__(self, tools):
        self.tools = tools

    def get_tool(self, name):
        """ 根据名称获取工具"""
        return self.tools.get(name, None)

    def add_tool(self, tool):
        """ 添加工具"""
        self.tools[tool.name] = tool
    def remove_tool(self, name):
        """ 移除工具"""
        if name in self.tools:
            del self.tools[name]
    def run_tool(self, name):
        """ 执行工具的操作"""
        tool = self.get_tool(name)
        if tool:
            return tool.run()
        else:
            return f"Tool {name} not found."

class calculator_tool(Tool):
    """ 一个简单的计算器工具类"""
    def __init__(self, name):
        super().__init__(name)

    def run(self, a: int, b: int, operation: str) -> int:
        """ 执行计算器的操作"""
        return self.calculator(a, b, operation)

    def calculator(self, a: int, b: int, operation: str) -> int:
        """
        A simple calculator function that performs basic arithmetic operations.

        Parameters:
        a (int): The first operand.
        b (int): The second operand.
        operation (str): The operation to perform ('add', 'subtract', 'multiply', 'divide').

        Returns:
        int: The result of the arithmetic operation.

        Raises:
        ValueError: If an unsupported operation is provided or if division by zero is attempted.
        """
        if operation == 'add':
            return a + b
        elif operation == 'subtract':
            return a - b
        elif operation == 'multiply':
            return a * b
        elif operation == 'divide':
            if b == 0:
                raise ValueError("Cannot divide by zero.")
            return a // b  # Using integer division for simplicity
        else:
            raise ValueError(f"Unsupported operation: {operation}")