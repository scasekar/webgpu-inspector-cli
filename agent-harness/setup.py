from setuptools import setup, find_namespace_packages

setup(
    name="cli-anything-webgpu-inspector",
    version="0.1.0",
    description="CLI tool for WebGPU Inspector - debug WebGPU applications from the command line",
    author="Arvind Sekar",
    python_requires=">=3.10",
    packages=find_namespace_packages(include=["cli_anything.*"]),
    package_data={
        "cli_anything.webgpu_inspector": ["js/*.js", "skills/*.md"],
    },
    install_requires=[
        "click>=8.0",
        "playwright>=1.40",
        "Pillow>=10.0",
        "prompt_toolkit>=3.0",
    ],
    entry_points={
        "console_scripts": [
            "cli-anything-webgpu-inspector=cli_anything.webgpu_inspector.webgpu_inspector_cli:cli",
        ],
    },
)
