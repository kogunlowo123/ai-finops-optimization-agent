# Contributing to AI FinOps Optimization Agent

Thank you for your interest in contributing! This guide will help you get started.

## How to Contribute

1. **Fork** the repository
2. **Create a branch** for your feature or fix (`git checkout -b feature/my-feature`)
3. **Make your changes** and ensure they follow the project conventions
4. **Test** your changes thoroughly
5. **Commit** with a clear, descriptive message
6. **Push** to your fork and open a **Pull Request**

## Development Setup

```bash
# Clone your fork
git clone https://github.com/<your-username>/ai-finops-optimization-agent.git
cd ai-finops-optimization-agent

# Create a virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

## Code Standards

- **Formatting**: Use [Black](https://github.com/psf/black) for code formatting
- **Linting**: Use [Flake8](https://flake8.pycqa.org/) for linting
- **Testing**: Write tests using [pytest](https://docs.pytest.org/) and ensure all tests pass before submitting

```bash
# Format code
black .

# Lint code
flake8 .

# Run tests
pytest
```

## Pull Request Guidelines

- Keep PRs focused on a single change
- Update documentation if needed
- Add tests for new functionality
- Ensure all existing tests pass
- Describe what your PR does and why

## Reporting Issues

- Use GitHub Issues to report bugs or request features
- Include steps to reproduce any bugs
- For security vulnerabilities, see [SECURITY.md](SECURITY.md)

## Code of Conduct

Be respectful, inclusive, and constructive in all interactions.
