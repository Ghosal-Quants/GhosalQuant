# File: clusters.py

import tkinter as tk
from cluster_logic import (
    build_similarity_adjacency,
    build_undirected_graph_from_adjacency,
    gmmcluster,
    predictgmmclusters
)
from network_graph import selected_tickers  # Global list of selected companies (tickers with suffixes)
from attributes import selected_attributes, similarity_threshold  # Selected attributes (weights) and threshold
from company import Company
from coordinates import company_cache  # Reuse cached Company objects

def generate_clusters():
    # Validate that companies and attributes are selected.
    if not selected_tickers or not selected_attributes:
        return None

    # Determine chosen attributes and total weight.
    chosen_attrs = []
    total_weight = 0
    for attr, weight in selected_attributes.items():
        if weight > 0:
            chosen_attrs.append(attr)
            total_weight += weight

    if total_weight == 0 or not chosen_attrs:
        return None

    # Build Company objects (using cache to avoid redundant data fetching).
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

    # Normalize weights so they sum to 1.
    weights = {attr: selected_attributes[attr] / total_weight for attr in chosen_attrs}
    threshold = similarity_threshold  # Get the user-defined similarity threshold

    # Build the similarity adjacency and graph.
    adjacency = build_similarity_adjacency(companies, chosen_attrs, threshold, weights)
    graph = build_undirected_graph_from_adjacency(adjacency)

    # Set number of clusters (up to 3, but not more than the number of nodes).
    n_clusters = min(3, len(graph.nodes())) if len(graph.nodes()) > 0 else 1
    sample_size = 100  # Fixed sample size for clustering

    # Run GMM clustering on the graph.
    clustering_result = gmmcluster(graph, adjacency, weights, n_clusters=n_clusters, sample_size=sample_size)
    if not clustering_result:
        return None

    hard_clusters = clustering_result["hard_clusters"]
    soft_clusters = clustering_result["soft_clusters"]
    nodes = list(graph.nodes())

    # Build a mapping from ticker to Company object.
    ticker_to_company = {}
    for comp in adjacency.keys():
        ticker_to_company[comp.ticker] = comp

    # Build clusters for the selected companies.
    selected_tickers_list = [comp.ticker for comp in companies]
    selected_clusters = {}
    to_predict = []
    for ticker in selected_tickers_list:
        if ticker in hard_clusters:
            cluster_id = hard_clusters[ticker]
            selected_clusters.setdefault(cluster_id, []).append((ticker, True))
        else:
            to_predict.append(ticker)
    predictions = None
    if to_predict:
        predictions = predictgmmclusters(
            to_predict,
            graph,
            adjacency,
            weights,
            {"model": clustering_result["model"]}
        )
        if predictions and "error" not in predictions:
            for ticker, cluster_id in predictions["hard_clusters"].items():
                selected_clusters.setdefault(cluster_id, []).append((ticker, False))

    # Build a text report.
    lines = []
    lines.append("CLUSTER ANALYSIS OF SELECTED COMPANIES\n")
    lines.append(f"Showing clusters for {len(selected_tickers_list)} selected companies.\n")
    lines.append("Companies are clustered based on these attributes:")
    for attr, weight in weights.items():
        lines.append(f"  - {attr}: {weight:.3f}")
    lines.append("")

    # Count total companies per cluster in the full clustering result.
    full_cluster_counts = {cluster_id: sum(1 for _, cid in hard_clusters.items() if cid == cluster_id)
                           for cluster_id in range(clustering_result["model"].n_components)}
    # Report for each cluster.
    for cluster_id in sorted(selected_clusters.keys()):
        companies_in_cluster = selected_clusters[cluster_id]
        count_in_full = full_cluster_counts.get(cluster_id, 0)
        lines.append(f"Cluster {cluster_id + 1} ({len(companies_in_cluster)} selected companies, {count_in_full} total companies):")
        for ticker, was_in_sample in companies_in_cluster:
            # Get membership probabilities.
            if was_in_sample:
                try:
                    node_index = nodes.index(ticker)
                    probs = soft_clusters[node_index]
                except ValueError:
                    continue
            else:
                if predictions and "cluster_probabilities" in predictions:
                    for i, pred_ticker in enumerate(to_predict):
                        if pred_ticker == ticker:
                            probs = predictions["cluster_probabilities"][i]
                            break
                    else:
                        continue
                else:
                    continue
            sample_status = " (in training sample)" if was_in_sample else " (predicted)"
            lines.append(f"  - {ticker}{sample_status}")
            lines.append("    Cluster membership probabilities:")
            for k in range(clustering_result["model"].n_components):
                cluster_prob = probs[k]
                highlight = " ← assigned cluster" if k == cluster_id else ""
                lines.append(f"      * Cluster {k + 1}: {cluster_prob * 100:.2f}%{highlight}")
            lines.append("")
    report = "\n".join(lines)
    
    return {
        "report": report,
        "graph": graph,
        "adjacency": adjacency,
        "clustering_result": clustering_result
    }

def main(content_frame):
    # Draw the Clusters page into the provided content_frame.
    results = generate_clusters()
    for widget in content_frame.winfo_children():
        widget.destroy()
    if results is None:
        tk.Label(content_frame, text="No clustering could be performed.\nEnsure companies and attributes are selected.", 
                 font=("Arial", 14), bg="black", fg="white").pack(pady=20)
        return

    # Create a canvas with a scrollbar.
    canvas = tk.Canvas(content_frame, bg="black")
    canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    scrollbar = tk.Scrollbar(content_frame, orient="vertical", command=canvas.yview)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
    canvas.configure(yscrollcommand=scrollbar.set)

    # Create an inner frame.
    inner_frame = tk.Frame(canvas, bg="black")
    canvas.create_window((0, 0), window=inner_frame, anchor="nw")
    inner_frame.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))

    # Create a Text widget to display the clustering report.
    text_widget = tk.Text(inner_frame, wrap="word", font=("Arial", 16, "bold"), bg="black", fg="white", bd=0)
    text_widget.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
    text_widget.insert(tk.END, results["report"])
    text_widget.configure(state="disabled")

if __name__ == "__main__":
    root = tk.Tk()
    root.title("Clusters Page Test")
    root.geometry("800x600")
    test_frame = tk.Frame(root, bg="black")
    test_frame.pack(fill=tk.BOTH, expand=True)
    main(test_frame)
    root.mainloop()
