# File: network_graph.py

import tkinter as tk
from tkinter import ttk

# Global persistent variables for the entire application
selected_tickers = []  # List of companies (tickers with suffixes)
all_tickers = []       # Temporary list of companies for the current industry

root = None          # Main application window
content_frame = None # Frame that will contain page-specific UI

def clear_content():
    for widget in content_frame.winfo_children():
        widget.destroy()

def load_page(page_function):
    clear_content()
    page_function(content_frame)

# Navigation button callbacks
def show_companies():
    from companies import main as companies_main
    load_page(companies_main)

def show_attributes():
    from attributes import main as attributes_main
    load_page(attributes_main)

def show_coordinates():
    from coordinates import main as coordinates_main
    load_page(coordinates_main)

def show_generate_connections():
    # Clear the entire window (instead of just the content area)
    for widget in root.winfo_children():
        widget.destroy()
    # Import and call generate.py's main function, passing the current root window.
    from generate import main as generate_main
    generate_main(root)

# Loads the title page from main.py into the existing window.
def go_back():
    from main import build_title_page, dataframes
    build_title_page(root, dataframes)

def main(root_window=None):
    global root, content_frame
    if root_window is None:
        root = tk.Tk()
    else:
        root = root_window

    root.title("Stock Universe - Network Graph")
    root.geometry("1000x600")
    
    # Create a top navigation frame that remains persistent
    nav_frame = tk.Frame(root, bg="black")
    nav_frame.pack(fill=tk.X, padx=10, pady=5)
    
    # Define navigation buttons and their callbacks
    nav_buttons = [
        ("Companies", show_companies),
        ("Attributes", show_attributes),
        ("Coordinates", show_coordinates),
        ("Generate Connections", show_generate_connections),
        ("Back", go_back)
    ]
    
    for i, (text, command) in enumerate(nav_buttons):
        btn = tk.Button(nav_frame, text=text, font=("Arial", 12), padx=10, pady=5, command=command)
        btn.grid(row=0, column=i, padx=5, pady=5, sticky="ew")
        nav_frame.columnconfigure(i, weight=1)
    
    # Create the content frame where each page’s elements will be drawn
    content_frame = tk.Frame(root, bg="gray")
    content_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
    
    # Load the default page (Companies)
    show_companies()
    
    # Only call mainloop if we created the window here.
    if root_window is None:
        root.mainloop()

if __name__ == "__main__":
    main()
