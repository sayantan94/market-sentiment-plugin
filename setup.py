from setuptools import setup, find_packages

setup(
    name="market-sentiment-plugin",
    version="0.1.0",
    packages=find_packages(),
    py_modules=["msp_cli"],
    python_requires=">=3.11",
    install_requires=[
        "fastapi>=0.115.0",
        "uvicorn>=0.32.0",
        "pydantic>=2.0.0",
        "boto3>=1.35.0",
        "sse-starlette>=2.0.0"
    ],
    entry_points={
        "console_scripts": [
            "msp-cli=msp_cli:main",
        ],
    },
)
