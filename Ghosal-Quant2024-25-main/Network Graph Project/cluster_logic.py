# file: cluster_logic.py

import networkx as nx
import numpy as np

# Compute similarity for a single attribute
def compute_attribute_similarity(valA, valB, is_categorical=False):

    if valA is None or valB is None:
        return 0.0

    # Categorical attributes
    if is_categorical:
        return 100.0 if valA == valB else 0.0

    # Numerical attributes
    try:
        A = float(valA)
        B = float(valB)
        if A < 0 or B < 0:
            A, B = abs(A), abs(B)
        mx = max(A, B)
        if mx == 0:
            return 0.0
        ratio = abs(A - B) / mx
        sim = max(0, 1 - ratio) * 100
        return sim
    except (ValueError, TypeError):
        return 0.0

# Compute overall similarity (0-100) between two Company objects.
def compute_company_similarity(cA, cB, attributes, weights):
    
    if not attributes:
        return 0.0, {}

    attr_details = {}
    total = 0.0
    for attr in attributes:
        valA = getattr(cA, attr, None)
        valB = getattr(cB, attr, None)

        # Decide categorical vs. numeric
        is_categorical = isinstance(valA, str) or isinstance(valB, str)
        attr_sim = compute_attribute_similarity(valA, valB, is_categorical)
        attr_scored = attr_sim * weights[attr]
        attr_details[attr] = attr_sim
        total += attr_scored

    overall_similarity = min(total, 100.0)
    return overall_similarity, attr_details

# Build adjacency dict for companies with overall similarity >= threshold.
def build_similarity_adjacency(companies, attributes, threshold, weights):

    adjacency = {}
    for c in companies:
        adjacency[c] = {}

    for i in range(len(companies)):
        for j in range(i + 1, len(companies)):
            cA = companies[i]
            cB = companies[j]
            overall_sim, details = compute_company_similarity(cA, cB, attributes, weights)
            if overall_sim >= threshold:
                adjacency[cA][cB] = {"overall": overall_sim, "details": details}
                adjacency[cB][cA] = {"overall": overall_sim, "details": details}

    return adjacency

# Produce a string describing each company's connections.
def adjacency_to_text(adjacency):
    lines = []
    lines.append("Connections Report:\n")
    for comp in adjacency:
        header = f"{comp.ticker}"
        lines.append(header)
        if adjacency[comp]:
            connected_companies = sorted(adjacency[comp].keys(), key=lambda x: x.ticker)
            for other in connected_companies:
                info = adjacency[comp][other]
                overall = info["overall"]
                details = info["details"]
                lines.append(f"  - Connected to {other.ticker} with overall similarity: {overall:.2f}%")
                lines.append("    Attribute similarities:")
                for attr in sorted(details.keys()):
                    lines.append(f"      * {attr}: {details[attr]:.2f}%")
        else:
            lines.append("  (No connections)")
        lines.append("")
    return "\n".join(lines)


# Create an undirected graph (NetworkX) using adjacency data
def build_undirected_graph_from_adjacency(adjacency):

    G = nx.Graph()
    # Add nodes
    for c in adjacency:
        G.add_node(c.ticker)
    # Add edges
    for cA in adjacency:
        for cB, info in adjacency[cA].items():
            # Only add once to avoid duplication
            if cA.ticker < cB.ticker:
                G.add_edge(cA.ticker, cB.ticker, weight=info["overall"])
    return G


# Perform a simple GMM clustering on the graph's nodes based on attribute similarities
def gmmcluster(G, adjacency, weights, n_clusters=3, max_iters=100, tol=1e-4):

    nodes = list(G.nodes())
    n_nodes = len(nodes)
    n_attributes = len(weights)

    # Create feature matrix
    X = np.zeros((n_nodes, n_attributes))
    for i, node1 in enumerate(nodes):
        company1 = next(c for c in adjacency.keys() if c.ticker == node1)
        for j, (attr, w) in enumerate(weights.items()):
            similarities = []
            for company2 in adjacency[company1]:
                if attr in adjacency[company1][company2]["details"]:
                    similarities.append(adjacency[company1][company2]["details"][attr] * w)
            X[i, j] = np.mean(similarities) if similarities else 0

    # Normalize
    X = (X - X.mean(axis=0)) / (X.std(axis=0) + 1e-8)

    # Random init
    means = X[np.random.choice(n_nodes, n_clusters, replace=False)]
    covs = np.array([np.eye(n_attributes) * 0.1 for _ in range(n_clusters)])
    mix_weights = np.ones(n_clusters) / n_clusters

    # EM Algorithm
    for iteration in range(max_iters):
        old_means = means.copy()
        # E-step
        responsibilities = np.zeros((n_nodes, n_clusters))
        for k in range(n_clusters):
            diff = X - means[k]
            exponent = -0.5 * np.sum(diff * diff / np.diag(covs[k]), axis=1)
            responsibilities[:, k] = mix_weights[k] * np.exp(exponent)

        # Normalize
        resp_sum = responsibilities.sum(axis=1, keepdims=True)
        resp_sum[resp_sum == 0] = 1e-10
        responsibilities /= resp_sum

        # M-step
        Nk = responsibilities.sum(axis=0)

        # Update means
        for k in range(n_clusters):
            if Nk[k] > 0:
                means[k] = np.sum(responsibilities[:, k, None] * X, axis=0) / Nk[k]

        # Update covariances
        for k in range(n_clusters):
            diff = X - means[k]
            covs[k] = np.diag(np.sum(responsibilities[:, k, None] * diff * diff, axis=0) / Nk[k])

        # Update mixture weights
        mix_weights = Nk / n_nodes

        # Check convergence
        if np.all(np.abs(means - old_means) < tol):
            break

    cluster_assignments = responsibilities.argmax(axis=1)
    return {
        "hard_clusters": {nodes[i]: int(cluster_assignments[i]) for i in range(n_nodes)},
        "soft_clusters": responsibilities
    }
