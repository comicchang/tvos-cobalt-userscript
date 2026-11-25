.PHONY: test clean

test:
	uv run --extra dev pytest -v

clean:
	rm -rf dist build .pytest_cache src/*.egg-info
