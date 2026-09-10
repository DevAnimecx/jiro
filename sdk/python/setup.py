from setuptools import setup, find_packages

setup(
    name="jiro-sdk",
    version="0.2.15",
    description="Official Python SDK for Jiro Search API",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    author="Jiro Team",
    author_email="webcrafterreal@gmail.com",
    url="https://github.com/DevAnimecx/jiro",
    packages=find_packages(),
    python_requires=">=3.8",
    install_requires=[
        "httpx>=0.24.0",
    ],
    extras_require={
        "websocket": ["websocket-client>=1.6.0"],
        "async": ["httpx[http2]>=0.24.0"],
        "dev": [
            "pytest>=7.0",
            "pytest-asyncio>=0.21",
            "pytest-cov>=4.0",
            "mypy>=1.0",
            "ruff>=0.1.0",
        ],
    },
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Programming Language :: Python :: 3.14",
        "Topic :: Software Development :: Libraries :: Python Modules",
    ],
    keywords="jiro search api web scraping sdk",
    license="MIT",
)
