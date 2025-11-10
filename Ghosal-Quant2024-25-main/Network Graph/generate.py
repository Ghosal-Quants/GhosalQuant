# File: generate.py

import tkinter as tk

# Global variables for the Generate Connections hub
root = None
content_frame = None

def clear_content():
    for widget in content_frame.winfo_children():
        widget.destroy()

def load_page(page_function):
    clear_content()
    page_function(content_frame)

# Loads the Graph page from graph.py
def show_graph():
    from graph import main as graph_main
    load_page(graph_main)


# Loads the Connections page from connections.py
def show_connections():
    from connections import main as connections_main
    load_page(connections_main)


# Loads the Clusters page from clusters.py
def show_clusters():
    from clusters import main as clusters_main
    load_page(clusters_main)


# Returns to the original Network Graph page from network_graph.py
def go_back():
    # Clear the entire window
    for widget in root.winfo_children():
        widget.destroy()
    from network_graph import main as network_graph_main
    network_graph_main(root)

def main(root_window=None):

    global root, content_frame
    if root_window is None:
        root = tk.Tk()
    else:
        root = root_window

    root.title("Stock Universe - Generate Connections")
    root.geometry("1000x600")
    
    # Clear the entire window
    for widget in root.winfo_children():
        widget.destroy()
    
    # Create a navigation frame with new buttons
    nav_frame = tk.Frame(root, bg="black")
    nav_frame.pack(fill=tk.X, padx=10, pady=5)
    
    nav_buttons = [
        ("Graph", show_graph),
        ("Connections", show_connections),
        ("Clusters", show_clusters),
        ("Back", go_back)
    ]
    
    for i, (text, command) in enumerate(nav_buttons):
        btn = tk.Button(nav_frame, text=text, font=("Arial", 12), padx=10, pady=5, command=command)
        btn.grid(row=0, column=i, padx=5, pady=5, sticky="ew")
        nav_frame.columnconfigure(i, weight=1)
    
    # Create the content frame where each page’s elements will be drawn
    content_frame = tk.Frame(root, bg="gray")
    content_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
    
    # Load the Connections page as the default for testing
    show_connections()
    
    if root_window is None:
        root.mainloop()

if __name__ == "__main__":
    main()
