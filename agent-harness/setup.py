from setuptools import setup, find_packages

setup(
    name="webgpu-inspector-cli",
    version="0.1.0",
    description="CLI tool for WebGPU Inspector - debug WebGPU applications from the command line",
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
    ],
    entry_points={
        "console_scripts": [
            "webgpu-inspector-cli=webgpu_inspector_cli.webgpu_inspector_cli:cli",
        ],
    },
)
