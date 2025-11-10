# File: connections.py

import tkinter as tk
from cluster_logic import build_similarity_adjacency, adjacency_to_text, build_undirected_graph_from_adjacency
from network_graph import selected_tickers  # Global list of selected companies (tickers with suffixes)
from attributes import selected_attributes, similarity_threshold  # Global dict of selected attributes (weights) and threshold
from company import Company
from coordinates import company_cache  # Import cached company data

# Global storage for computed adjacency and graph
current_adjacency = None
current_text = None
current_graph = None
node_cluster_labels = {}



# Creates or reuses Company objects and selected attributes from the cache then builds adjacency & graph.

def generate_connections():

    global current_adjacency, current_text, current_graph, node_cluster_labels

    if not selected_tickers:
        return

    # Determine chosen attributes (nonzero weight)
    chosen_attrs = []
    total_weight = 0
    for attr, weight in selected_attributes.items():
        if weight > 0:
            chosen_attrs.append(attr)
            total_weight += weight

    if total_weight == 0 or not chosen_attrs:
        # No meaningful attributes selected
        return

    companies = []
    for ticker in selected_tickers:
        ticker_upper = ticker.upper()
        cache_key = (ticker_upper, frozenset(chosen_attrs))
        if cache_key not in company_cache:
            try:
                company_cache[cache_key] = Company(ticker_upper, chosen_attrs)
            except Exception as e:
                print(f"Could not create Company for {ticker_upper}: {e}")
                continue
        companies.append(company_cache[cache_key])

    # Normalize attribute weights
    weights = {attr: selected_attributes[attr] / total_weight for attr in chosen_attrs}

    # Get user-defined similarity threshold
    threshold = similarity_threshold  # User-defined similarity threshold

    # Build adjacency
    current_adjacency = build_similarity_adjacency(companies, chosen_attrs, threshold, weights)

    # Generate text output (if needed)
    current_text = adjacency_to_text(current_adjacency)

    # Build graph
    current_graph = build_undirected_graph_from_adjacency(current_adjacency)

    # Initialize cluster labels to -1 (no clustering applied yet)
    node_cluster_labels = {node: -1 for node in current_graph.nodes()}


# Updates the display_frame with connections for the company whose ticker is selected.

def update_connections(display_frame, selected_company_ticker):

    # Clear the display frame
    for widget in display_frame.winfo_children():
        widget.destroy()

    # Find the Company object in current_adjacency with the given ticker
    selected_company = None
    for comp in current_adjacency:
        if comp.ticker == selected_company_ticker:
            selected_company = comp
            break

    # If not found, display an error message.
    if not selected_company:
        tk.Label(display_frame, text="Selected company not found.", 
                 font=("Arial", 14), bg="black", fg="white").pack(pady=10)
        return

    # Get the connections for the selected company
    comp_connections = current_adjacency.get(selected_company, {})
    if not comp_connections:
        tk.Label(display_frame, text="(No connections)", 
                 font=("Arial", 14), bg="black", fg="white").pack(pady=10)
        return

    # For each connected company, display the connection details
    for other, info in comp_connections.items():
        overall = info["overall"]
        tk.Label(display_frame, text=f"- {other.ticker} Similarity: {overall:.0f}%", 
                 font=("Arial", 14), bg="black", fg="white", anchor="w").pack(fill=tk.X, padx=20)
        details = info["details"]
        for attr, sim in details.items():
            weight = selected_attributes.get(attr, 0)
            tk.Label(display_frame, text=f"    * {attr}: {sim:.0f}%, {weight}", 
                     font=("Arial", 12), bg="black", fg="white", anchor="w").pack(fill=tk.X, padx=40)


def main(content_frame):
    # Run generate_connections to prepare data
    generate_connections()

    # Clear the content frame
    for widget in content_frame.winfo_children():
        widget.destroy()

    # Create a canvas with a scrollbar
    canvas = tk.Canvas(content_frame, bg="black")
    canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    scrollbar = tk.Scrollbar(content_frame, orient="vertical", command=canvas.yview)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
    canvas.configure(yscrollcommand=scrollbar.set)

    # Create an inner frame inside the canvas
    inner_frame = tk.Frame(canvas, bg="black")
    canvas.create_window((0, 0), window=inner_frame, anchor="nw")
    inner_frame.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))

    # Check if companies and attributes are selected
    if not selected_tickers:
        tk.Label(inner_frame, text="No companies selected.", font=("Arial", 14), bg="black", fg="white").pack(pady=10)
        return
    if not selected_attributes:
        tk.Label(inner_frame, text="No attributes selected.", font=("Arial", 14), bg="black", fg="white").pack(pady=10)
        return

    # Create a dropdown menu for selecting a company
    dropdown_frame = tk.Frame(inner_frame, bg="black")
    dropdown_frame.pack(fill=tk.X, padx=10, pady=10)
    tk.Label(dropdown_frame, text="Select Company:", font=("Arial", 14), bg="black", fg="white").pack(side=tk.LEFT, padx=5)

    # Build list of company tickers from current_adjacency
    company_options = sorted([comp.ticker for comp in current_adjacency.keys()])
    selected_company_var = tk.StringVar()
    if company_options:
        selected_company_var.set(company_options[0])
    option_menu = tk.OptionMenu(dropdown_frame, selected_company_var, *company_options, 
                                 command=lambda val: update_connections(display_frame, val))
    option_menu.config(font=("Arial", 12), bg="black", fg="white")
    option_menu["menu"].config(bg="black", fg="white")
    option_menu.pack(side=tk.LEFT, padx=5)

    # Create a frame to display the connections for the selected company
    display_frame = tk.Frame(inner_frame, bg="black")
    display_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

    # Initially display connections for the first company in the dropdown
    if company_options:
        update_connections(display_frame, company_options[0])

    # Optionally, if a network graph is available, you can indicate it here
    if current_graph and current_graph.number_of_edges() > 0:
        tk.Label(inner_frame, text="Network Graph Representation Available.", 
                 font=("Arial", 14, "bold"), bg="black", fg="white").pack(fill=tk.X, pady=10, padx=10)


if __name__ == "__main__":
    root = tk.Tk()
    root.title("Connections Page Test")
    root.geometry("800x600")
    test_frame = tk.Frame(root, bg="black")
    test_frame.pack(fill=tk.BOTH, expand=True)
    main(test_frame)
    root.mainloop()
