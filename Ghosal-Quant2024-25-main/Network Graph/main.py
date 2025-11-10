# File: main.py

import tkinter as tk
from tkinter import messagebox
import os
import pandas as pd
from pathlib import Path

# Define paths
cwd = Path.cwd()
data_folder = cwd / "data"
industry_folder = data_folder / "industry"
attributes_file = data_folder / "attributes.csv"

# Check if industry folder exists
if not industry_folder.exists():
    raise FileNotFoundError(f"Directory {industry_folder} not found.")

# Load industry CSV files and store them in dataframes
dataframes = {}
for file in industry_folder.glob("*.csv"):
    df_name = file.stem
    dataframes[df_name] = pd.read_csv(file)
    print(f"Loaded: {file}")
print("Loaded DataFrames:", list(dataframes.keys()))

# Load attributes from attributes.csv
attributes_list = []
if attributes_file.exists():
    df_attributes = pd.read_csv(attributes_file)
    attributes_list = df_attributes["Attributes"].dropna().unique().tolist()
    print("Loaded Attributes:", attributes_list)
else:
    print(f"File {attributes_file} not found.")

# Clears the root and loads the search tool UI from search_tool.py
def open_search_tool(root, dataframes):
    for widget in root.winfo_children():
        widget.destroy()
    from search_tool import main as search_tool_main
    search_tool_main(root, dataframes)

# Clears the root and loads the network graph UI from network_graph.py
def open_network_graph(root, dataframes):
    for widget in root.winfo_children():
        widget.destroy()
    from network_graph import main as network_graph_main
    network_graph_main(root)  

#  Clears the root and builds the title page UI
def build_title_page(root, dataframes):
    for widget in root.winfo_children():
        widget.destroy()
    
    root.title("Stock Universe")
    root.geometry("900x600")

    title_label = tk.Label(root, text="Stock Universe", font=("Arial", 80), fg="White")
    title_label.pack(pady=50)

    button_frame = tk.Frame(root)
    button_frame.pack()

    search_button = tk.Button(
        button_frame,
        text="Search Tool",
        font=("Arial", 15),
        fg="black",
        padx=20,
        pady=10,
        command=lambda: open_search_tool(root, dataframes)
    )
    search_button.pack(pady=20)

    network_button = tk.Button(
        button_frame,
        text="Network Graph",
        font=("Arial", 15),
        fg="black",
        padx=20,
        pady=10,
        command=lambda: open_network_graph(root, dataframes)
    )
    network_button.pack(pady=20)

# Creates the Tk window, builds the title page, starts the main loop
def create_window():
    root = tk.Tk()
    build_title_page(root, dataframes)
    root.mainloop()

if __name__ == "__main__":
    create_window()
