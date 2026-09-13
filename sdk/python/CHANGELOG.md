# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-09-13

### Added
- Initial release of AgentCanvas Python SDK
- Workflow management (create, read, update, delete)
- Execution control (start, monitor, cancel)
- Provider management (create, read, update, delete)
- Full type safety with Pydantic models
- Comprehensive error handling with custom exception hierarchy
- Context manager support for automatic resource cleanup
- Pagination support for list operations
- Type hints with py.typed marker
- Complete test suite with 9 tests

### Features
- `AgentCanvasClient`: Main client class for API interaction
- `Workflow`, `Execution`, `Provider`: Core data models
- `ExecutionStatus`: Enum for execution states
- HTTP error handling with specific exception types
- Request timeout configuration
- Bearer token authentication

### Development
- Ruff for code quality checks
- MyPy for static type checking
- Pytest for testing
- GitHub Actions CI/CD ready
