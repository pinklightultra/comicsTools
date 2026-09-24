"""Hugging Face Spaces entrypoint (Gradio SDK).

Spaces looks for ``app.py`` at the repo root and a ``demo`` object
(or a file that launches Gradio). We expose ``demo`` without calling
``launch()`` so Spaces can manage the server; local ``python app.py``
still launches on 0.0.0.0.
"""
from pencil_cleaner.app import build_demo

demo = build_demo()

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0")
