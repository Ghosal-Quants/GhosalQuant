# file: ui.py

import tkinter as tk
from tkinter import (
    Listbox, Entry, Button, StringVar, Scale, Label,
    Scrollbar, IntVar
)
import yfinance as yf
import networkx as nx
import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from matplotlib import pyplot as plt

# Import  similarity, adjacency building, & clustering logic
from cluster_logic import (
    build_similarity_adjacency,
    adjacency_to_text,
    build_undirected_graph_from_adjacency,
    gmmcluster
)

# Import our Company class
from company import Company

class CompanySelectorApp:
    def __init__(self, root, df_companies, df_attributes):
        """
        root: Tk root window
        df_companies: DataFrame of ticker/name pairs
        df_attributes: DataFrame of available attributes
        """
        self.root = root
        self.root.title("Company Network Graph")
        self.root.geometry("800x500")

        # A cache to store Company instances keyed by (ticker, frozenset_of_attributes)
        self.company_cache = {}

        # --- Data ---
        self.df_companies = df_companies
        # Each row is (Ticker, CompanyName, ...)
        self.company_list = [
            f"{row[0]} - {row[1]}" for row in self.df_companies.itertuples(index=False, name=None)
        ]
        self.selected_companies = []

        self.df_attributes = df_attributes
        self.attributes_list = self.df_attributes["Attributes"].tolist()
        # Each attribute has a slider 0-10 => which is the "importance weight"
        self.attribute_vars = {attr: IntVar(value=0) for attr in self.attributes_list}

        # Store adjacency, graph, adjacency text, cluster labels after generation
        self.current_adjacency = None
        self.current_graph = None
        self.current_text = None
        self.node_cluster_labels = {}

        # --- Top Navigation (Main Screen) ---
        self.frame_nav = tk.Frame(root, bd=2, relief=tk.SOLID)
        self.frame_nav.pack(fill=tk.X, side=tk.TOP, padx=5, pady=5)

        # Navigation Buttons (Main Screen)
        self.btn_companies = Button(
            self.frame_nav, text="Companies", command=self.show_companies
        )
        self.btn_companies.pack(side=tk.LEFT, expand=True, padx=5, pady=5)

        self.btn_attributes = Button(
            self.frame_nav, text="Attributes", command=self.show_attributes
        )
        self.btn_attributes.pack(side=tk.LEFT, expand=True, padx=5, pady=5)

        self.btn_generate = Button(
            self.frame_nav, text="Generate Connections", command=self.generate_connections
        )
        self.btn_generate.pack(side=tk.LEFT, expand=True, padx=5, pady=5)

        self.btn_coordinates = Button(
            self.frame_nav, text="Coordinates", command=self.show_coordinates
        )
        self.btn_coordinates.pack(side=tk.LEFT, expand=True, padx=5, pady=5)

        # Main Content Area
        self.frame_content = tk.Frame(root)
        self.frame_content.pack(fill=tk.BOTH, expand=True)

        # Default view
        self.show_companies()

    # -----------------------------------------
    # Companies Page
    # -----------------------------------------
    def show_companies(self):
        # Clear frame_content
        for widget in self.frame_content.winfo_children():
            widget.destroy()

        self.search_var = StringVar()
        search_entry = Entry(self.frame_content, textvariable=self.search_var)
        search_entry.pack(fill=tk.X, padx=10, pady=5)
        self.search_var.trace_add("write", self.update_company_list)

        frame_lists = tk.Frame(self.frame_content)
        frame_lists.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        frame_left = tk.Frame(frame_lists)
        frame_left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.lb_companies = Listbox(frame_left)
        self.lb_companies.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.populate_company_list()

        btn_add = Button(frame_left, text="Add Company", command=self.add_company)
        btn_add.pack(fill=tk.X, padx=5, pady=5)

        frame_right = tk.Frame(frame_lists)
        frame_right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        self.lb_selected = Listbox(frame_right)
        self.lb_selected.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.populate_selected_companies_list()

        btn_remove = Button(frame_right, text="Remove Company", command=self.remove_company)
        btn_remove.pack(fill=tk.X, padx=5, pady=5)

    def populate_company_list(self):
        self.lb_companies.delete(0, tk.END)
        for company in self.company_list:
            self.lb_companies.insert(tk.END, company)

    def populate_selected_companies_list(self):
        self.lb_selected.delete(0, tk.END)
        for sc in self.selected_companies:
            self.lb_selected.insert(tk.END, sc)

    def update_company_list(self, *args):
        term = self.search_var.get().lower()
        self.company_list = [
            f"{row[0]} - {row[1]}"
            for row in self.df_companies.itertuples(index=False, name=None)
            if term in f"{row[0]} - {row[1]}".lower()
        ]
        self.populate_company_list()

    def add_company(self):
        sel = self.lb_companies.curselection()
        if sel:
            company_str = self.lb_companies.get(sel[0])
            if company_str not in self.selected_companies:
                self.selected_companies.append(company_str)
                self.populate_selected_companies_list()

    def remove_company(self):
        sel = self.lb_selected.curselection()
        if sel:
            company_str = self.lb_selected.get(sel[0])
            self.selected_companies.remove(company_str)
            self.populate_selected_companies_list()

    # -----------------------------------------
    # Attributes Page
    # -----------------------------------------
    def show_attributes(self):
        # Clear frame_content
        for widget in self.frame_content.winfo_children():
            widget.destroy()

        canvas = tk.Canvas(self.frame_content)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scrollbar = Scrollbar(self.frame_content, orient="vertical", command=canvas.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.bind("<Configure>", lambda e: canvas.config(scrollregion=canvas.bbox("all")))

        attr_container = tk.Frame(canvas)
        canvas.create_window((0, 0), window=attr_container, anchor="nw")

        # --- Similarity Threshold Slider (10-100) ---
        self.similarity_threshold = IntVar(value=40)  # default 40
        frame_thresh = tk.Frame(attr_container, bd=2, relief=tk.SOLID, width=700, height=80)
        frame_thresh.pack(fill=tk.X, padx=10, pady=10)
        frame_thresh.pack_propagate(False)

        lbl_thresh = Label(frame_thresh, text="Similarity Threshold")
        lbl_thresh.pack(side=tk.LEFT, padx=10, pady=5)

        Scale(
            frame_thresh,
            from_=10, to=100, orient=tk.HORIZONTAL, resolution=10, tickinterval=10,
            variable=self.similarity_threshold
        ).pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=10, pady=5)

        # --- Attribute Sliders (0-10) ---
        for attr in self.attributes_list:
            frame_rect = tk.Frame(attr_container, bd=2, relief=tk.SOLID, width=700, height=80)
            frame_rect.pack(fill=tk.X, padx=10, pady=10)
            frame_rect.pack_propagate(False)

            lbl = Label(frame_rect, text=attr)
            lbl.pack(side=tk.LEFT, padx=10, pady=5)

            Scale(
                frame_rect,
                from_=0, to=10, orient=tk.HORIZONTAL, resolution=1, tickinterval=1,
                variable=self.attribute_vars[attr]
            ).pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=10, pady=5)

    # -----------------------------------------
    # Coordinates Page
    # -----------------------------------------
    def show_coordinates(self):
        # Clear frame_content
        for widget in self.frame_content.winfo_children():
            widget.destroy()

        text_area = tk.Text(self.frame_content, wrap="word")
        text_area.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        if not self.selected_companies:
            text_area.insert(tk.END, "No companies have been selected.\n")
            return

        for company_str in self.selected_companies:
            ticker = company_str.split(" - ")[0].strip()

            try:
                yf_ticker = yf.Ticker(ticker)
                info = yf_ticker.info
            except Exception as e:
                text_area.insert(tk.END, f"{ticker} = Error fetching data: {e}\n\n")
                continue

            lines_for_ticker = []
            for attr in self.attributes_list:
                slider_value = self.attribute_vars[attr].get()
                if slider_value != 0:
                    yf_value = info.get(attr, "N/A")
                    line_str = f"    '{attr}': {slider_value}, '{yf_value}'"
                    lines_for_ticker.append(line_str)

            if not lines_for_ticker:
                continue

            text_area.insert(tk.END, f"{ticker} = {{\n")
            for line in lines_for_ticker:
                text_area.insert(tk.END, line + "\n")
            text_area.insert(tk.END, "}\n\n")

    # -----------------------------------------
    # Generate Connections & Show New Page
    # -----------------------------------------
    def generate_connections(self):
        """
        Create or reuse Company objects from the cache (with selected attributes),
        then build adjacency & graph.
        """
        if not self.selected_companies:
            return

        # Determine chosen attributes (nonzero weight)
        chosen_attrs = []
        total_weight = 0
        for attr in self.attributes_list:
            w = self.attribute_vars[attr].get()  # 0..10
            if w > 0:
                chosen_attrs.append(attr)
                total_weight += w

        if total_weight == 0 or not chosen_attrs:
            # No meaningful attributes
            return

        companies = []
        for sc in self.selected_companies:
            ticker = sc.split(" - ")[0].strip()
            cache_key = (ticker, frozenset(chosen_attrs))

            if cache_key not in self.company_cache:
                try:
                    self.company_cache[cache_key] = Company(ticker, chosen_attrs)
                except Exception as e:
                    print(f"Could not create Company for {ticker}: {e}")
                    continue

            companies.append(self.company_cache[cache_key])

        # Build normalized weights
        weights = {}
        for attr in chosen_attrs:
            raw = self.attribute_vars[attr].get()
            weights[attr] = raw / total_weight

        threshold = self.similarity_threshold.get()  # 10..100

        # Build adjacency
        adjacency = build_similarity_adjacency(companies, chosen_attrs, threshold, weights)
        self.current_adjacency = adjacency

        # Build text
        self.current_text = adjacency_to_text(adjacency)

        # Build graph
        graph = build_undirected_graph_from_adjacency(adjacency)
        self.current_graph = graph

        # By default, no cluster labeling yet
        self.node_cluster_labels = {node: -1 for node in graph.nodes()}

        # Show the new "Connections" page (with 4 nav buttons)
        self.show_connections_page()

    def show_connections_page(self):
        # Clear
        for widget in self.frame_content.winfo_children():
            widget.destroy()

        nav_frame = tk.Frame(self.frame_content, bd=2, relief=tk.SOLID)
        nav_frame.pack(fill=tk.X, side=tk.TOP, padx=5, pady=5)

        btn_graph = Button(nav_frame, text="Network Graph", command=self.show_network_graph)
        btn_graph.pack(side=tk.LEFT, expand=True, padx=5, pady=5)

        btn_attr_conn = Button(nav_frame, text="Attribute Connections", command=self.show_attribute_connections)
        btn_attr_conn.pack(side=tk.LEFT, expand=True, padx=5, pady=5)

        btn_clusters = Button(nav_frame, text="Clusters Information", command=self.show_clusters_information)
        btn_clusters.pack(side=tk.LEFT, expand=True, padx=5, pady=5)

        btn_back = Button(nav_frame, text="Back", command=self.go_back_to_main)
        btn_back.pack(side=tk.LEFT, expand=True, padx=5, pady=5)

        self.conn_view_frame = tk.Frame(self.frame_content)
        self.conn_view_frame.pack(fill=tk.BOTH, expand=True)

    # -----------------------------------------
    # Connections Page Sub-Views
    # -----------------------------------------
    def show_network_graph(self):
        if not self.current_graph:
            return

        for widget in self.conn_view_frame.winfo_children():
            widget.destroy()

        fig = Figure(figsize=(6, 5), dpi=100)
        ax = fig.add_subplot(111)
        ax.set_title("Company Network (Similarity-Based)")

        pos = nx.spring_layout(self.current_graph, seed=42, k=3, iterations=50)

        colors = []
        for node in self.current_graph.nodes():
            colors.append(self.node_cluster_labels.get(node, -1))

        nx.draw_networkx_nodes(
            self.current_graph, pos, ax=ax,
            node_size=800,
            node_color=colors,
            cmap=plt.cm.rainbow,
            edgecolors="black"
        )
        nx.draw_networkx_edges(self.current_graph, pos, ax=ax, width=1.0, edge_color="gray")
        nx.draw_networkx_labels(self.current_graph, pos, ax=ax, font_size=9)

        ax.axis("off")

        canvas = FigureCanvasTkAgg(fig, master=self.conn_view_frame)
        canvas.draw()
        canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

    def show_attribute_connections(self):
        if not self.current_adjacency or not self.current_text:
            return

        for widget in self.conn_view_frame.winfo_children():
            widget.destroy()

        text_widget = tk.Text(self.conn_view_frame, wrap="word", width=80, height=20)
        text_widget.pack(fill=tk.BOTH, expand=True)

        text_widget.insert(tk.END, self.current_text)
        text_widget.configure(state="disabled")

    def show_clusters_information(self):
        if not self.current_graph:
            return

        for widget in self.conn_view_frame.winfo_children():
            widget.destroy()

        # Build new weights from the attribute sliders (in case they've changed)
        chosen_attrs = []
        total_weight = 0
        for attr in self.attributes_list:
            w = self.attribute_vars[attr].get()
            if w > 0:
                chosen_attrs.append(attr)
                total_weight += w
        if total_weight == 0 or not chosen_attrs:
            return

        weights = {attr: self.attribute_vars[attr].get() / total_weight for attr in chosen_attrs}

        clustering_result = gmmcluster(
            self.current_graph,
            self.current_adjacency,
            weights,
            n_clusters=min(3, len(self.current_graph.nodes()))
        )
        hard_clusters = clustering_result["hard_clusters"]
        soft_clusters = clustering_result["soft_clusters"]

        for ticker, cidx in hard_clusters.items():
            self.node_cluster_labels[ticker] = cidx

        all_companies = list(self.current_adjacency.keys())
        ticker_to_company = {c.ticker: c for c in all_companies}

        cluster_dict = {}
        for ticker, cluster_id in hard_clusters.items():
            company = ticker_to_company.get(ticker)
            if company:
                if cluster_id not in cluster_dict:
                    cluster_dict[cluster_id] = []
                cluster_dict[cluster_id].append(company)

        lines = []
        lines.append("GAUSSIAN MIXTURE MODEL CLUSTERING ANALYSIS\n")
        lines.append("Companies are clustered based on their similarity patterns.\n")

        for cluster_id in sorted(cluster_dict.keys()):
            companies = cluster_dict[cluster_id]
            lines.append(f"Cluster {cluster_id + 1} ({len(companies)} companies):")
            for company in companies:
                node_index = list(self.current_graph.nodes()).index(company.ticker)
                probs = soft_clusters[node_index]
                lines.append(f"  - {company.ticker}")
                lines.append(f"    Cluster membership probabilities:")
                for k, prob in enumerate(probs):
                    lines.append(f"      * Cluster {k + 1}: {prob * 100:.2f}%")
                lines.append("")

        text_widget = tk.Text(self.conn_view_frame, wrap="word", width=80, height=20)
        text_widget.pack(fill=tk.BOTH, expand=True)

        scrollbar = tk.Scrollbar(self.conn_view_frame, command=text_widget.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        text_widget.configure(yscrollcommand=scrollbar.set)

        text_widget.insert(tk.END, "\n".join(lines))
        text_widget.configure(state="disabled")

    # -----------------------------------------
    # Return to Main (Companies/Attributes/Coordinates)
    # -----------------------------------------
    def go_back_to_main(self):
        for widget in self.frame_content.winfo_children():
            widget.destroy()
        self.show_companies()
