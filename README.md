# LifeLine

A Python course project.

## Project structure

```text
LifeLine/
├── src/lifeline/       # Application code
├── tests/               # Automated tests
├── pyproject.toml       # Project metadata and tool configuration
└── README.md
```

## Getting started

Create and activate a virtual environment, then install the project with
development tools:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

Run the application:

```bash
python -m lifeline.main
```

Run tests and linting:

```bash
pytest
ruff check .
```
Python course project
