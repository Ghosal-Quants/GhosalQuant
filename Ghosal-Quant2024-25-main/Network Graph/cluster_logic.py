# file: cluster_logic.py

import networkx as nx
import numpy as np
import random
import math
from sklearn.decomposition import PCA
from sklearn.mixture import GaussianMixture

# Compute similarity for a single attribute
def compute_attribute_similarity(valA, valB, is_categorical=False):
    if valA is None or valB is None:
        return 0.0
    if is_categorical:
        return 100.0 if valA == valB else 0.0
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

# Create an undirected graph (NetworkX) using adjacency data.
def build_undirected_graph_from_adjacency(adjacency):
    G = nx.Graph()
    for c in adjacency:
        G.add_node(c.ticker)
    for cA in adjacency:
        for cB, info in adjacency[cA].items():
            if cA.ticker < cB.ticker:
                G.add_edge(cA.ticker, cB.ticker, weight=info["overall"])
    return G

# Get feature matrix from the graph's nodes.
def get_features(G, adjacency, weights):
    nodes = list(G.nodes())
    n_nodes = len(nodes)
    n_attributes = len(weights)
    X = np.zeros((n_nodes, n_attributes))
    for i, node1 in enumerate(nodes):
        company1 = next(c for c in adjacency.keys() if c.ticker == node1)
        for j, (attr, w) in enumerate(weights.items()):
            similarities = []
            for company2 in adjacency[company1]:
                if attr in adjacency[company1][company2]["details"]:
                    similarities.append(adjacency[company1][company2]["details"][attr] * w)
            X[i, j] = np.mean(similarities) if similarities else 0
    return X, nodes

# Proportional stratified sampling using PCA.
def proportional_stratified_sampling(X, nodes, sample_size=150):
    if len(nodes) <= sample_size:
        return list(range(len(nodes)))
    normalX = (X - X.mean(axis=0)) / (X.std(axis=0) + 1e-8)
    pca = PCA(n_components=2)
    X_pca = pca.fit_transform(normalX)
    stratanum = 5
    pc1_quantiles = np.percentile(X_pca[:, 0], np.linspace(0, 100, stratanum + 1))
    pc2_quantiles = np.percentile(X_pca[:, 1], np.linspace(0, 100, stratanum + 1))
    strata = {}
    for i, (pc1, pc2) in enumerate(X_pca):
        row = 0
        for j in range(stratanum):
            if pc1_quantiles[j] <= pc1 < pc1_quantiles[j+1]:
                row = j
                break
        if pc1 == pc1_quantiles[-1]:
            row = stratanum - 1
        column = 0
        for j in range(stratanum):
            if pc2_quantiles[j] <= pc2 < pc2_quantiles[j+1]:
                column = j
                break
        if pc2 == pc2_quantiles[-1]:
            column = stratanum - 1
        stratum = (row, column)
        if stratum not in strata:
            strata[stratum] = []
        strata[stratum].append(i)
    total_points = len(X)
    chosen_comps = []
    samples_per_stratum = {}
    for stratum, indices in strata.items():
        stratum_proportion = len(indices) / total_points
        stratum_samples = int(np.round(sample_size * stratum_proportion))
        if len(indices) > 0:
            stratum_samples = max(1, stratum_samples)
        stratum_samples = min(stratum_samples, len(indices))
        samples_per_stratum[stratum] = stratum_samples
    total_allocated = sum(samples_per_stratum.values())
    if total_allocated != sample_size:
        diff = sample_size - total_allocated
        strata_by_size = sorted(strata.keys(), key=lambda s: len(strata[s]), reverse=(diff > 0))
        for stratum in strata_by_size:
            if diff == 0:
                break
            if diff > 0:
                if samples_per_stratum[stratum] < len(strata[stratum]):
                    samples_per_stratum[stratum] += 1
                    diff -= 1
            else:
                if samples_per_stratum[stratum] > 1:
                    samples_per_stratum[stratum] -= 1
                    diff += 1
    for stratum, indices in strata.items():
        comps_to_sample = samples_per_stratum[stratum]
        if comps_to_sample > 0:
            comp_samples = random.sample(indices, comps_to_sample)
            chosen_comps.extend(comp_samples)
    return chosen_comps, strata

# GMM clustering with optional stratified sampling.
def gmmcluster(G, adjacency, weights, n_clusters=6, max_iters=100, tol=1e-4, sample_size=None):
    nodes = list(G.nodes())
    n_nodes = len(nodes)
    n_attributes = len(weights)
    X, nodes = get_features(G, adjacency, weights)
    X = (X - X.mean(axis=0)) / (X.std(axis=0) + 1e-8)
    if sample_size is None:
        sample_size = n_nodes
    else:
        sample_size = min(sample_size, n_nodes)
    if sample_size < n_nodes:
        sampled_indices, strata_info = proportional_stratified_sampling(X, nodes, sample_size)
        X_sampled = X[sampled_indices]
        sampled_nodes = [nodes[i] for i in sampled_indices]
    else:
        X_sampled = X
        sampled_nodes = nodes
        sampled_indices = list(range(n_nodes))
    gmm = GaussianMixture(n_components=n_clusters, covariance_type='full', max_iter=max_iters, tol=tol, random_state=42, n_init=10)
    gmm.fit(X_sampled)
    soft_clusters_sampled = gmm.predict_proba(X_sampled)
    hard_clusters_sampled = gmm.predict(X_sampled)
    hard_clusters = {sampled_nodes[i]: int(hard_clusters_sampled[i]) for i in range(len(sampled_nodes))}
    return {
        "hard_clusters": hard_clusters,
        "soft_clusters": soft_clusters_sampled,
        "model": gmm,
        "sampled_indices": sampled_indices,
        "feature_matrix": X
    }

# Predict cluster assignments for new companies using a trained GMM model.
def predictgmmclusters(user_companies, G, adjacency, weights, trained_model_results):
    gmm = trained_model_results["model"]
    all_nodes = list(G.nodes())
    n_attributes = len(weights)
    X_user = np.zeros((len(user_companies), n_attributes))
    for i, ticker in enumerate(user_companies):
        comp1 = None
        for comp in adjacency.keys():
            if comp.ticker == ticker:
                comp1 = comp
                break
        if comp1 is not None:
            for j, (attr, w) in enumerate(weights.items()):
                similarities = []
                for comp2 in adjacency[comp1]:
                    if attr in adjacency[comp1][comp2]["details"]:
                        similarities.append(adjacency[comp1][comp2]["details"][attr] * w)
                X_user[i, j] = np.mean(similarities) if similarities else 0
        else:
            print(f"Warning: No company found with ticker {ticker}")
    feature_matrix = trained_model_results.get("feature_matrix")
    if feature_matrix is not None:
        feature_mean = feature_matrix.mean(axis=0)
        feature_std = feature_matrix.std(axis=0) + 1e-8
        X_user = (X_user - feature_mean) / feature_std
    else:
        X_user = (X_user - X_user.mean(axis=0)) / (X_user.std(axis=0) + 1e-8)
    hard_clusters = gmm.predict(X_user)
    soft_clusters = gmm.predict_proba(X_user)
    results = {
        "hard_clusters": {user_companies[i]: int(hard_clusters[i]) for i in range(len(user_companies))},
        "soft_clusters": {user_companies[i]: soft_clusters[i].tolist() for i in range(len(user_companies))},
        "cluster_probabilities": soft_clusters.tolist()
    }
    return results

# Get main (representative) companies per cluster.
def get_cluster_mains(G, hard_clusters, soft_clusters, nummains=3):
    nodes = list(G.nodes())
    companies_by_cluster = {}
    for ticker, cluster_id in hard_clusters.items():
        if cluster_id not in companies_by_cluster:
            companies_by_cluster[cluster_id] = []
        companies_by_cluster[cluster_id].append(ticker)
    cluster_mains = {}
    for cluster_id, cluster_comps in companies_by_cluster.items():
        node_indices = []
        node_tickers = []
        for ticker in cluster_comps:
            try:
                idx = nodes.index(ticker)
                node_indices.append(idx)
                node_tickers.append(ticker)
            except ValueError:
                continue
        if not node_indices:
            continue
        probs = [(node_tickers[i], soft_clusters[node_indices[i]][cluster_id]) for i in range(len(node_indices))]
        probs.sort(key=lambda x: x[1], reverse=True)
        mains = [ticker for ticker, _ in probs[:nummains]]
        cluster_mains[f"Cluster {cluster_id + 1}"] = mains
    return cluster_mains
