# File: graph.py

import tkinter as tk
import networkx as nx
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

# Import the current graph and node cluster labels from the connections module.
from connections import current_graph, node_cluster_labels

def main(content_frame):
    # Clear the content frame.
    for widget in content_frame.winfo_children():
        widget.destroy()
    
    # Check if a graph is available.
    if not current_graph or current_graph.number_of_nodes() == 0:
        tk.Label(content_frame, text="No graph available.", font=("Arial", 14), bg="black", fg="white").pack(pady=20)
        return
    
    # Create a matplotlib figure.
    fig = Figure(figsize=(6, 5), dpi=100)
    ax = fig.add_subplot(111)
    
    # Set the title using a font dictionary.
    ax.set_title("Company Network (Similarity-Based)", fontdict={"fontname": "Arial", "fontsize": 12})
    
    # Compute the layout for nodes using a spring layout.
    pos = nx.spring_layout(current_graph, seed=42, k=3, iterations=50)
    
    # Determine colors for nodes based on their cluster labels.
    # If a node has no cluster label, -1 is used.
    colors = [node_cluster_labels.get(node, -1) for node in current_graph.nodes()]
    
    # Draw nodes, edges, and labels.
    nx.draw_networkx_nodes(current_graph, pos, ax=ax,
                           node_size=800, node_color=colors, cmap=plt.cm.rainbow,
                           edgecolors="black")
    nx.draw_networkx_edges(current_graph, pos, ax=ax, width=1.0, edge_color="gray")
    nx.draw_networkx_labels(current_graph, pos, ax=ax, font_size=9, font_family="Arial")
    
    ax.axis("off")
    
    # Embed the matplotlib figure into the content frame.
    canvas = FigureCanvasTkAgg(fig, master=content_frame)
    canvas.draw()
    canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

if __name__ == "__main__":
    root = tk.Tk()
    root.title("Graph Page Test")
    root.geometry("800x600")
    test_frame = tk.Frame(root, bg="black")
    test_frame.pack(fill=tk.BOTH, expand=True)
    main(test_frame)
    root.mainloop()
