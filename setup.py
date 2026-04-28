from setuptools import setup, find_packages

setup(
    name="arcfuse",
    version="1.1.0",
    description="ArcFuse — Autonomous Codebase Intelligence Agent — scan, refactor, review, repeat.",
    packages=find_packages(),
    install_requires=[],
    python_requires=">=3.10",
    entry_points={
        "console_scripts": [
            "arcfuse=codefuse.orchestrator:main",
        ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3.10",
        "Topic :: Software Development :: Quality Assurance",
    ],
)
