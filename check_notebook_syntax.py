import json
import py_compile
import os
import sys

# Load the generated notebook
notebook_path = "mental_health_platform.ipynb"
if not os.path.exists(notebook_path):
    print("Notebook file does not exist!")
    sys.exit(1)

with open(notebook_path, "r") as f:
    nb = json.load(f)

print("Extracting and executing code cells to generate modular python files...")
# Execute code cells to write the files to disk
for idx, cell in enumerate(nb["cells"]):
    if cell["cell_type"] == "code":
        source = "".join(cell["source"])
        
        # Skip cell 2 (!pip install) and cell 12 (ngrok/streamlit blocking process)
        if "!pip" in source or "streamlit run" in source or "pyngrok" in source or "ngrok" in source:
            continue
            
        print(f"Executing Cell {idx+1}...")
        try:
            # Execute in global scope
            exec(source, globals())
        except Exception as e:
            print(f"Error executing Cell {idx+1}: {e}")

# List of generated files to check
files_to_check = [
    "config.py",
    "database.py",
    "rag_assistant.py",
    "agents.py",
    "report_generator.py",
    "app.py"
]

print("\n--- Compiling Generated Python Files ---")
all_ok = True
for filepath in files_to_check:
    if os.path.exists(filepath):
        try:
            py_compile.compile(filepath, doraise=True)
            print(f"[OK] {filepath}: Syntax OK")
        except py_compile.PyCompileError as err:
            print(f"[ERR] {filepath}: Syntax Error!")
            print(err)
            all_ok = False
    else:
        print(f"[WARN] {filepath}: File not found")
        all_ok = False

if all_ok:
    print("\n[SUCCESS] All python files compiled successfully without syntax errors!")
else:
    print("\n[FAILURE] Syntax errors found in one or more files.")

