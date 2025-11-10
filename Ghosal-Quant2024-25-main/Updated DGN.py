# -*- coding: utf-8 -*-
"""
Stock Correlation Network Visualization with Slider Control
"""

# Import necessary libraries
import yfinance as yf
import pandas as pd
import networkx as nx
import matplotlib.pyplot as plt
from matplotlib.widgets import Slider

# --- Constants and Configuration ---
TICKERS = [
    "RIO.L", "TPR", "RWE.DE", "HEI.DE", "IMI.L",
    "ADBE", "GOOGL", "NXT", "HNI", "MKS.L",
    "CALM", "GRMN"
]
START_DATE = "2023-01-01"
END_DATE = "2024-01-01"
CORRELATION_THRESHOLD = 0.3

# --- Functions ---
def fetch_stock_data(tickers, start_date, end_date):
    """Download stock data and compute daily returns."""
    close_prices = yf.download(tickers, start=start_date, end=end_date)["Close"]
    returns = close_prices.pct_change().dropna()
    return returns

def build_daily_network(returns, date, threshold):
    """Build a NetworkX graph for a given date based on correlation threshold."""
    dreturns = returns.loc[:date]
    correlation_matrix = dreturns.corr()
    graph = nx.Graph()
    
    # Add nodes
    for ticker in TICKERS:
        graph.add_node(ticker)

    # Add edges based on correlation threshold
    for i, t1 in enumerate(TICKERS):
        for j, t2 in enumerate(TICKERS):
            if i < j:
                corr_value = correlation_matrix.iloc[i, j]
                if abs(corr_value) > threshold:
                    graph.add_edge(t1, t2, weight=corr_value)

    return graph

def plot_network(graph, node_positions, title, ax):
    """Plot the network graph."""
    ax.clear()
    nx.draw_networkx_nodes(graph, node_positions, node_size=500, ax=ax)
    nx.draw_networkx_edges(graph, node_positions, alpha=0.7, ax=ax)
    nx.draw_networkx_labels(graph, node_positions, font_size=12, ax=ax)
    ax.set_title(title)
    ax.set_axis_off()

def update_graph(val):
    """Update the graph when the slider value changes."""
    date_index = int(date_slider.val)
    selected_date = dates[date_index]
    daily_graph = build_daily_network(returns, selected_date, CORRELATION_THRESHOLD)
    plot_network(daily_graph, node_positions, f"Correlation Network on {selected_date.date()}", ax)
    fig.canvas.draw_idle()

# --- Main Script ---
if __name__ == "__main__":
    # Fetch stock data and calculate returns
    returns = fetch_stock_data(TICKERS, START_DATE, END_DATE)
    dates = returns.index

    # Initialize plot
    fig, ax = plt.subplots(figsize=(10, 8))
    plt.subplots_adjust(bottom=0.2)  # Make room for the slider

    # Compute initial graph and layout
    initial_graph = build_daily_network(returns, dates[0], CORRELATION_THRESHOLD)
    node_positions = nx.spring_layout(initial_graph, seed=42)  # Fixed layout for consistency

    # Plot the initial graph
    plot_network(initial_graph, node_positions, f"Correlation Network on {dates[0].date()}", ax)

    # Create slider
    slider_ax = plt.axes([0.2, 0.05, 0.6, 0.03])  # [left, bottom, width, height]
    date_slider = Slider(
        ax=slider_ax,
        label="Date",
        valmin=0,
        valmax=len(dates) - 1,
        valinit=0,
        valstep=1  # Step corresponds to index in `dates`
    )

    # Connect slider to update function
    date_slider.on_changed(update_graph)

    # Show the plot
    plt.show()
