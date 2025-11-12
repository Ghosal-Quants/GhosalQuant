# === Makefile for GhosalQuant ===

# create and activate venv, install deps
setup:
	@python3 -m venv venv
	@./venv/bin/pip install --upgrade pip
	@./venv/bin/pip install -r requirements.txt
	@echo "Environment setup complete."

# run main file inside venv
run:
	@./venv/bin/python main.py

# remove venv
clean:
	rm -rf venv
	@echo "🧹 Virtual environment removed."

