from setuptools import setup, find_packages

setup(
    name="webgpu-inspector-cli",
    version="0.2.2",
    description="CLI + MCP server for WebGPU Inspector — debug WebGPU applications from the command line or any MCP-capable LLM client",
    author="Arvind Sekar",
    python_requires=">=3.10",
    packages=find_packages(),
    package_data={
        "webgpu_inspector_cli": ["js/*.js", "skills/*.md"],
    },
    install_requires=[
        "click>=8.0",
        "playwright>=1.40",
        "Pillow>=10.0",
        "prompt_toolkit>=3.0",
        "mcp>=1.0",
    ],
    entry_points={
        "console_scripts": [
            "webgpu-inspector-cli=webgpu_inspector_cli.webgpu_inspector_cli:main",
            "webgpu-inspector-mcp=webgpu_inspector_cli.mcp_server.server:main",
        ],
    },
)
